"""Private, durable staging enrollment state. No provider calls or entitlements.

Only a backend that fetched invitation/user records from WorkOS after an
AuthKit code exchange should call accept(). Browser-supplied objects do not
authenticate a user. This store never persists invitation tokens or API keys.
"""
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
            if current.timestamp() > row["expires_at"]:
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
