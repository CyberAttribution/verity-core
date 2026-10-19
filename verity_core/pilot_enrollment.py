"""Private, durable staging enrollment state. No provider calls or entitlements.

Only a backend that fetched invitation/user records from WorkOS after an
AuthKit code exchange should call accept(). Browser-supplied objects do not
authenticate a user. This store never persists invitation tokens or API keys.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from datetime import datetime, timezone

from .activation import _id
from .pilot_acceptance import _email, _timestamp, validate_acceptance


class EnrollmentConflict(ValueError):
    pass


class EnrollmentStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Enrollment store must be an owner-only regular file")
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS invitations (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at REAL NOT NULL,
                state TEXT NOT NULL, acceptance TEXT, installation_id TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS activation_links (
                invitation_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
                license_id TEXT NOT NULL UNIQUE, code_sha256 TEXT NOT NULL,
                state TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS invitation_revocations (
                invitation_id TEXT PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL,
                exported_code_at_revocation INTEGER NOT NULL,
                export_status_uncertain INTEGER NOT NULL DEFAULT 0)""")
            if "export_status_uncertain" not in {r[1] for r in db.execute("PRAGMA table_info(invitation_revocations)")}:
                db.execute("ALTER TABLE invitation_revocations ADD COLUMN export_status_uncertain INTEGER NOT NULL DEFAULT 0")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def prepare(self, invitation_id, email, expires_at, *, now=None):
        """Persist metadata from a staging invitation issued by the backend."""
        _id(invitation_id)
        recipient = _email(email)
        expiry = _timestamp(expires_at).timestamp()
        current = time.time() if now is None else now
        if expiry <= current or expiry > current + 30*86400:
            raise ValueError("Invitation expiry is invalid")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT INTO invitations VALUES (?,?,?,'pending',NULL,NULL)",
                           (invitation_id, recipient, expiry))
            except sqlite3.IntegrityError:
                raise EnrollmentConflict("Invitation ID already prepared") from None
        return {"invitation_id": invitation_id, "state": "pending", "grants_authority": False}

    def prepare_from_gateway(self, gateway, invitation_id, expected_email, *, now=None):
        """Read back provider metadata before storing a pending local invitation."""
        _id(invitation_id)
        recipient=_email(expected_email)
        invitation=gateway.get(invitation_id)
        if (invitation.get("id")!=invitation_id or _email(invitation.get("email"))!=recipient
                or invitation.get("state")!="pending" or invitation.get("accepted_user_id")
                or invitation.get("accepted_at")):
            raise ValueError("Provider invitation is not pending for exact recipient")
        return self.prepare(invitation_id,recipient,invitation["expires_at"],now=now)

    def accept(self, invitation_id, invitation, user, name, organization="", *, now=None):
        """Bind a verified user once; never issue a license from a bare link."""
        _id(invitation_id)
        current = datetime.now(timezone.utc) if now is None else now
        if current.tzinfo is None:
            raise ValueError("Timezone-aware acceptance time required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM invitations WHERE id=?", (invitation_id,)).fetchone()
            if not row or row["state"] != "pending":
                raise EnrollmentConflict("Unknown or already accepted invitation")
            if current.timestamp() >= row["expires_at"]:
                raise ValueError("Pending invitation expired")
            candidate = validate_acceptance(invitation=invitation, user=user,
                expected_invitation_id=invitation_id, expected_email=row["email"],
                name=name, organization=organization, now=current)
            if _timestamp(invitation["expires_at"]).timestamp() != row["expires_at"]:
                raise ValueError("Provider invitation expiry changed")
            db.execute("UPDATE invitations SET state='accepted',acceptance=? WHERE id=?",
                       (json.dumps(candidate, sort_keys=True), invitation_id))
        return candidate

    def bind_installation(self, invitation_id, installation_id, *, now=None):
        _id(invitation_id)
        _id(installation_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM invitations WHERE id=?", (invitation_id,)).fetchone()
            if not row or row["state"] != "accepted":
                raise EnrollmentConflict("Verified, unused acceptance required")
            if (time.time() if now is None else now) >= row["expires_at"]:
                raise ValueError("Accepted invitation expired before installation binding")
            db.execute("UPDATE invitations SET state='installation_bound',installation_id=? WHERE id=?",
                       (installation_id, invitation_id))
        return {"invitation_id": invitation_id, "installation_id": installation_id,
                "state": "installation_bound", "activation_issued": False}

    def get(self, invitation_id):
        _id(invitation_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM invitations WHERE id=?", (invitation_id,)).fetchone()
        if not row:
            raise EnrollmentConflict("Unknown invitation")
        return dict(row, acceptance=json.loads(row["acceptance"]) if row["acceptance"] else None)

    def issue_bound_entitlement(self, invitation_id, installation_id, code, public_key,
                                output, registry, *, now=None):
        """Reserve exact verified binding before an administrator exports signed code.

        An interruption after reservation is visible as pending. A prior registry
        issuance can be reconciled only when its identity and digest match.
        This is not an AuthKit callback or an automatic signing service.
        """
        from . import activation
        from .pilot_registry import Conflict
        _id(invitation_id);_id(installation_id)
        claims=activation.unpack(code,public_key)
        current=time.time() if now is None else now
        activation.entitlement(code,public_key,installation_id,current)
        _id(claims["license_id"])
        license_id=claims["license_id"]
        digest=hashlib.sha256(code.strip().encode()).hexdigest()
        created=False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM invitations WHERE id=?",(invitation_id,)).fetchone()
            if not row or row["state"]!="installation_bound" or row["installation_id"]!=installation_id:
                raise EnrollmentConflict("Verified installation binding required")
            if current>=row["expires_at"]:
                raise ValueError("Invitation expired before entitlement issuance")
            link=db.execute("SELECT * FROM activation_links WHERE invitation_id=?",(invitation_id,)).fetchone()
            if link:
                if (link["installation_id"],link["license_id"],link["code_sha256"])!=(installation_id,license_id,digest):
                    raise EnrollmentConflict("Invitation already reserved for another entitlement")
            else:
                db.execute("INSERT INTO activation_links VALUES (?,?,?,?,?)",
                           (invitation_id,installation_id,license_id,digest,"pending"))
                created=True
        # Serialize revocation with export. The earlier reservation is already
        # committed, so a crash during registry issuance remains reconcilable.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT state,installation_id,expires_at FROM invitations WHERE id=?",(invitation_id,)).fetchone()
            if not row or row["state"]!="installation_bound" or row["installation_id"]!=installation_id:
                raise EnrollmentConflict("Invitation changed before entitlement export")
            if time.time()>=row["expires_at"]:
                raise ValueError("Invitation expired before entitlement export")
            try:
                status=registry.status(license_id)
            except Conflict as exc:
                if str(exc)!="Unknown license":
                    raise
                status=None
            if status is not None and created:
                raise EnrollmentConflict("License pre-exists verified enrollment reservation")
            if status is None:
                registry.issue(code,public_key,output)
                status=registry.status(license_id)
            entry=status["licenses"][0]
            if (entry["license_id"],entry["installation_id"],entry["code_sha256"])!=(license_id,installation_id,digest):
                raise EnrollmentConflict("Registry entitlement differs from reservation")
            if entry["artifact_state"]!="exported":
                raise EnrollmentConflict("Registry export needs administrator reconciliation")
            # The registry lock spans the enrollment commit, closing the gap
            # between its earlier status snapshot and activation confirmation.
            with registry.export_guard(license_id):
                db.execute("UPDATE activation_links SET state='exported' WHERE invitation_id=? AND state='pending'",
                           (invitation_id,))
                db.commit()
        return dict(invitation_id=invitation_id,installation_id=installation_id,
                    license_id=license_id,code_sha256=digest,activation_issued=True,
                    source="verified local enrollment and administrator-signed offline code")

    def activation_link(self,invitation_id):
        _id(invitation_id)
        with self._db() as db:
            row=db.execute("SELECT * FROM activation_links WHERE invitation_id=?",(invitation_id,)).fetchone()
        return dict(row) if row else None

    def revoke(self,invitation_id,reason,*,now=None):
        """Stop local enrollment/issuance; exported offline code needs signed-list import."""
        _id(invitation_id)
        if not isinstance(reason,str) or not reason.strip() or len(reason)>300:
            raise ValueError("Bounded revocation reason required")
        current=time.time() if now is None else now
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM invitations WHERE id=?",(invitation_id,)).fetchone()
            if not row or row["state"]=="revoked":
                raise EnrollmentConflict("Unknown or already revoked invitation")
            link=db.execute("SELECT state FROM activation_links WHERE invitation_id=?",(invitation_id,)).fetchone()
            exported=bool(link and link["state"]=="exported")
            uncertain=bool(link and link["state"]=="pending")
            db.execute("UPDATE invitations SET state='revoked' WHERE id=?",(invitation_id,))
            db.execute("INSERT INTO invitation_revocations VALUES (?,?,?,?,?)",
                       (invitation_id,current,reason.strip(),int(exported),int(uncertain)))
        return dict(invitation_id=invitation_id,state="revoked",
                    exported_code_at_revocation=exported,
                    offline_revocation_pending=exported or uncertain,
                    export_status_uncertain=uncertain,
                    source="local enrollment state; signed cumulative revocation import not observed")

    def revocation(self,invitation_id):
        _id(invitation_id)
        with self._db() as db:
            row=db.execute("SELECT * FROM invitation_revocations WHERE invitation_id=?",(invitation_id,)).fetchone()
        return dict(row) if row else None
