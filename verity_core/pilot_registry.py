"""Private administrator bookkeeping for offline pilot enrollment.
No keys or raw codes are stored. Issuance is reserved durably BEFORE exporting
the code, so a crash between filesystem and database operations is visible as
pending and can be reconciled. Replacement never remotely revokes an old code.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
from . import activation
from .trust import decision

DISCLAIMER = ("Administrator bookkeeping only. Replacement does not invalidate "
              "an older code. Offline revocation takes effect only after the "
              "installation imports a signed cumulative list. An acknowledgment "
              "is an operator-supplied association, not machine attestation.")

class Conflict(ValueError):pass

def fingerprint(data):return hashlib.sha256(data).hexdigest()

def artifact_present(path):
    try:return stat.S_ISREG(Path(path).lstat().st_mode)
    except FileNotFoundError:return False

def read_private(path,limit=8192):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>limit:
            raise ValueError("Artifact must be an owner-only bounded regular file")
        raw=os.read(fd,limit+1)
        if len(raw)>limit:raise ValueError("Artifact exceeds size limit")
        return raw
    finally:os.close(fd)

def file_fingerprint(path,limit=512*1024*1024):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError("Package must be a regular file no larger than 512 MiB")
        digest=hashlib.sha256();count=0
        while chunk:=os.read(fd,1024*1024):
            count+=len(chunk)
            if count>limit:raise ValueError("Package exceeds size limit")
            digest.update(chunk)
        return digest.hexdigest()
    finally:os.close(fd)

def save_private_new(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    except BaseException:
        # Preserve a possibly partial artifact for reconciliation; never claim
        # that a failed cross-resource operation left no file behind.
        raise

class Registry:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            os.close(fd)
        except FileExistsError:pass
        info=self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError("Registry must be an owner-only regular file")
        with sqlite3.connect(self.path) as con:
            if con.execute("PRAGMA journal_mode=WAL").fetchone()[0]!="wal":
                raise ValueError("Registry WAL mode is unavailable")
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS licenses (license_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, claims TEXT NOT NULL, code_sha256 TEXT NOT NULL, artifact_path TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0, supersedes TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, at INTEGER NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS release_authorizations (installation_id TEXT NOT NULL, channel TEXT NOT NULL, release_id TEXT NOT NULL, allowed INTEGER NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(installation_id,channel,release_id))")

    @contextmanager
    def connection(self):
        con=sqlite3.connect(self.path,timeout=10);con.row_factory=sqlite3.Row
        try:
            con.execute("PRAGMA synchronous=FULL");con.execute("BEGIN IMMEDIATE")
            yield con;con.commit()
        except BaseException:con.rollback();raise
        finally:con.close()

    def event(self,con,kind,data):
        con.execute("INSERT INTO events(at,event,data) VALUES(?,?,?)",
                    (int(time.time()),kind,json.dumps(data,sort_keys=True,allow_nan=False)))

    def _reserve(self,con,claims,code_sha256,artifact,supersedes):
        activation._id(claims["license_id"]);activation._id(claims["installation_id"])
        if set(claims)!={"schema","installation_id","license_id","issued_at","expires_at"} or claims["schema"]!="verity_pilot_entitlement/v1":
            raise ValueError("Invalid signed entitlement metadata")
        if any(type(claims[k]) is not int for k in ("issued_at","expires_at")) or not 0<claims["expires_at"]-claims["issued_at"]<=30*86400:
            raise ValueError("Invalid pilot validity interval")
        if con.execute("SELECT 1 FROM licenses WHERE license_id=?",(claims["license_id"],)).fetchone():
            raise Conflict("License IDs are never reused, including expired IDs")
        if supersedes:
            old=con.execute("SELECT * FROM licenses WHERE license_id=?",(supersedes,)).fetchone()
            if not old or not old["exported"] or old["installation_id"]!=claims["installation_id"]:
                raise Conflict("Replacement must reference an exported license for this installation")
            if con.execute("SELECT 1 FROM licenses WHERE supersedes=?",(supersedes,)).fetchone():
                raise Conflict("This license already has a replacement; replace its latest successor instead")
        con.execute("INSERT INTO licenses(license_id,installation_id,claims,code_sha256,artifact_path,supersedes) VALUES(?,?,?,?,?,?)",
                    (claims["license_id"],claims["installation_id"],json.dumps(claims),code_sha256,str(artifact),supersedes))
        self.event(con,"issuance_prepared",dict(license_id=claims["license_id"],installation_id=claims["installation_id"],code_sha256=code_sha256,supersedes=supersedes))

    def issue(self,code,public_key,output,supersedes=None,existing=False,invitation_id=None):
        claims=activation.unpack(code,public_key)
        activation.entitlement(code,public_key,claims.get("installation_id"),time.time())
        raw=code.strip().encode();output=Path(output).absolute()
        if not existing and (output.exists() or output.is_symlink()):raise Conflict("Code output already exists")
        if existing:
            info=output.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                raise ValueError("Existing code artifact must be owner-only")
            if read_private(output).strip()!=raw:raise ValueError("Existing artifact does not match code")
        with self.connection() as con:
            self._reserve(con,claims,fingerprint(raw),output,supersedes)
            if invitation_id:self._associate_invitation(con,invitation_id,claims["license_id"])
        if not existing:save_private_new(output,raw)
        self.reconcile(claims["license_id"])
        return self.status(claims["license_id"])

    def reconcile(self,license_id):
        activation._id(license_id)
        with self.connection() as con:
            row=con.execute("SELECT * FROM licenses WHERE license_id=?",(license_id,)).fetchone()
            if not row:raise Conflict("Unknown license")
            if row["exported"]:return
            path=Path(row["artifact_path"]);info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                raise ValueError("Code artifact must be owner-only")
            if info.st_size>8192 or fingerprint(read_private(path).strip())!=row["code_sha256"]:
                raise ValueError("Code artifact missing, partial, or does not match reserved fingerprint")
            con.execute("UPDATE licenses SET exported=1 WHERE license_id=?",(license_id,))
            self.event(con,"issuance_exported",dict(license_id=license_id))

    def retry_export(self,license_id,key,preserve_partial=False):
        with self.connection() as con:
            row=con.execute("SELECT * FROM licenses WHERE license_id=?",(license_id,)).fetchone()
            if not row:raise Conflict("Unknown license")
            claims=json.loads(row["claims"]);path=Path(row["artifact_path"])
            payload=activation.canonical(claims)
            raw=(activation.encode(payload)+"."+activation.encode(key.sign(payload))).encode()
            if fingerprint(raw)!=row["code_sha256"]:raise ValueError("Issuer does not reproduce the reserved code")
            if path.exists() or path.is_symlink():
                existing=read_private(path)
                if fingerprint(existing.strip())!=row["code_sha256"]:
                    if not preserve_partial:raise ValueError("Partial artifact retained; use --preserve-partial to move it aside and re-export")
                    backup=path.with_name(path.name+".partial-"+uuid.uuid4().hex)
                    path.rename(backup)
                    self.event(con,"partial_artifact_preserved",dict(license_id=license_id,backup_path=str(backup)))
            if not path.exists():save_private_new(path,raw)
            if fingerprint(read_private(path).strip())!=row["code_sha256"]:raise ValueError("Code artifact mismatch")
            if not row["exported"]:
                con.execute("UPDATE licenses SET exported=1 WHERE license_id=?",(license_id,))
                self.event(con,"issuance_exported",dict(license_id=license_id))
            else:self.event(con,"artifact_reexport_checked",dict(license_id=license_id))

    def acknowledge(self,license_id,installation_id,bundle,checkpoint):
        result=decision.verify(bundle,checkpoint)
        if not result["verified"] or not result["anchored"]:raise ValueError("Verified Trust Record and separate checkpoint required")
        manifest=bundle["record"]["entries"][0]["payload"]["manifest"]
        if manifest["source_kind"]!="analysis_snapshot":raise ValueError("A real-analysis snapshot is required")
        with self.connection() as con:
            row=con.execute("SELECT * FROM licenses WHERE license_id=?",(license_id,)).fetchone()
            if not row or not row["exported"] or row["installation_id"]!=installation_id:
                raise Conflict("Exported license and installation association required")
            data=dict(license_id=license_id,installation_id=installation_id,record_id=checkpoint["record_id"],
                      head_hash=checkpoint["head_hash"],source="operator-supplied association; integrity verified; not attestation")
            if any(json.loads(r[0])==data for r in con.execute("SELECT data FROM events WHERE event='analysis_acknowledged'")):return
            self.event(con,"analysis_acknowledged",data)

    def record_revocations(self,code,public_key,path):
        claims=activation.revocations(code,public_key,time.time())
        raw=Path(path).read_bytes().strip()
        if fingerprint(raw)!=fingerprint(code.strip().encode()):raise ValueError("Revocation artifact mismatch")
        with self.connection() as con:
            rows=con.execute("SELECT data FROM events WHERE event='revocation_list_issued' ORDER BY seq").fetchall()
            old=json.loads(rows[-1][0]) if rows else None
            if old and (claims["revision"]<=old["revision"] or not set(old["license_ids"]).issubset(claims["license_ids"])):
                raise Conflict("Registry revocation revisions must advance and lists must be cumulative")
            self.event(con,"revocation_list_issued",dict(revision=claims["revision"],license_ids=claims["license_ids"],
                       list_sha256=fingerprint(raw),artifact_path=str(Path(path).absolute()),installation_import="not observed"))

    def authorize_release(self,installation_id,channel,release_id,allowed,reason):
        activation._id(installation_id);activation._id(release_id)
        if channel not in ("pilot","stable") or type(allowed) is not bool or not isinstance(reason,str) or not reason.strip() or len(reason)>500:
            raise ValueError("Invalid release authorization")
        with self.connection() as con:
            if not con.execute("SELECT 1 FROM licenses WHERE installation_id=?",(installation_id,)).fetchone():
                raise Conflict("Unknown installation")
            self.event(con,"release_authorization_changed",dict(installation_id=installation_id,channel=channel,release_id=release_id,allowed=allowed,reason=reason))
            seq=con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO release_authorizations VALUES(?,?,?,?,?) ON CONFLICT(installation_id,channel,release_id) DO UPDATE SET allowed=excluded.allowed,seq=excluded.seq",(installation_id,channel,release_id,int(allowed),seq))

    def release_allowed(self,installation_id,channel,release_id):
        with self.connection() as con:
            row=con.execute("SELECT allowed,seq FROM release_authorizations WHERE installation_id=? AND channel=? AND release_id=?",(installation_id,channel,release_id)).fetchone()
            return dict(allowed=bool(row["allowed"]),event_seq=row["seq"]) if row else dict(allowed=False,event_seq=None)

    def observe(self,installation_id,*,version,channel,update_state,contact_at=None,source="operator-entered"):
        """An operator report is never promoted to an authenticated device observation."""
        activation._id(installation_id)
        if source not in ("operator-entered","installation-report") or channel not in ("pilot","stable") or update_state not in ("unknown","current","available","failed","recovered") or not isinstance(version,str) or not version or len(version)>80:
            raise ValueError("Invalid installation report")
        at=int(time.time()) if contact_at is None else contact_at
        if type(at) is not int or at<0 or at>time.time()+60:raise ValueError("Invalid contact timestamp")
        with self.connection() as con:
            if not con.execute("SELECT 1 FROM licenses WHERE installation_id=?",(installation_id,)).fetchone():raise Conflict("Unknown installation")
            self.event(con,"installation_reported",dict(installation_id=installation_id,version=version,channel=channel,update_state=update_state,contact_at=at,source=source))

    def installations(self,now=None,stale_after=86400):
        now=time.time() if now is None else now
        snapshot=self.status(now=now);events=snapshot["events"]
        out=[]
        for iid in sorted({r["installation_id"] for r in snapshot["licenses"]}):
            licenses=[r for r in snapshot["licenses"] if r["installation_id"]==iid]
            reports=[e for e in events if e["event"]=="installation_reported" and e["data"]["installation_id"]==iid]
            report=reports[-1] if reports else None
            out.append(dict(installation_id=iid,enrolled_at=min(r["issued_at"] for r in licenses),licenses=licenses,
                reported=report["data"] if report else None,reported_at=report["at"] if report else None,
                contact_state="unknown" if not report else ("stale" if now-report["data"]["contact_at"]>stale_after else "reported"),
                acknowledgment="operator-entered" if any(r["acknowledgment_reported"] for r in licenses) else "unknown",
                release_authorizations=[e for e in events if e["event"]=="release_authorization_changed" and e["data"]["installation_id"]==iid]))
        return out

    def prepare_invitation(self,package_path,output,invitation_id=None,days=7):
        from .pilot_invitation import draft
        package=Path(package_path);output=Path(output).absolute()
        if output.exists() or output.is_symlink():raise Conflict("Invitation output already exists")
        iid=invitation_id or "invite-"+uuid.uuid4().hex
        sha=file_fingerprint(package);text=draft(iid,package.name,sha,days)
        with self.connection() as con:
            prior=[json.loads(row[0]) for row in con.execute("SELECT data FROM events WHERE event='invitation_prepared'")]
            if any(row["invitation_id"]==iid for row in prior):raise Conflict("Invitation IDs are never reused")
            self.event(con,"invitation_prepared",dict(invitation_id=iid,package_filename=package.name,
                       package_sha256=sha,draft_path=str(output),draft_sha256=fingerprint(text.encode()),suggested_days=days,sent=False))
        save_private_new(output,text.encode())
        self.retry_invitation(iid)
        return dict(invitation_id=iid,output=str(output),package_sha256=sha,sent=False,grants_authority=False)

    def retry_invitation(self,invitation_id,preserve_partial=False):
        from .pilot_invitation import draft
        with self.connection() as con:
            rows=[json.loads(row[0]) for row in con.execute("SELECT data FROM events WHERE event='invitation_prepared' ORDER BY seq")]
            data=next((row for row in rows if row["invitation_id"]==invitation_id),None)
            if not data:raise Conflict("Unknown invitation")
            raw=draft(invitation_id,data["package_filename"],data["package_sha256"],data["suggested_days"]).encode()
            if data.get("draft_sha256") and fingerprint(raw)!=data["draft_sha256"]:raise ValueError("Draft template changed; preserve this history and prepare a new invitation ID")
            path=Path(data["draft_path"])
            if path.exists() or path.is_symlink():
                if read_private(path,16384)!=raw:
                    if not preserve_partial:raise ValueError("Partial draft retained; use --preserve-partial")
                    path.rename(path.with_name(path.name+".partial-"+uuid.uuid4().hex))
            if not path.exists():save_private_new(path,raw)
            if read_private(path,16384)!=raw:raise ValueError("Invitation artifact mismatch")
            exported=[json.loads(row[0]) for row in con.execute("SELECT data FROM events WHERE event='invitation_draft_exported'")]
            if not any(row["invitation_id"]==invitation_id for row in exported):
                self.event(con,"invitation_draft_exported",dict(invitation_id=invitation_id,sent=False))

    def _associate_invitation(self,con,invitation_id,license_id):
        activation._id(invitation_id)
        exported=[json.loads(row[0]) for row in con.execute("SELECT data FROM events WHERE event='invitation_draft_exported'")]
        if not any(row["invitation_id"]==invitation_id for row in exported):raise Conflict("Exported invitation draft required")
        license=con.execute("SELECT installation_id FROM licenses WHERE license_id=?",(license_id,)).fetchone()
        if not license:raise Conflict("Unknown license")
        old=[json.loads(row[0]) for row in con.execute("SELECT data FROM events WHERE event='invitation_associated'")]
        for row in old:
            if row["invitation_id"]==invitation_id and row["installation_id"]!=license[0]:
                raise Conflict("This invitation is associated with a different installation")
            if row["invitation_id"]==invitation_id and row["license_id"]==license_id:return
        self.event(con,"invitation_associated",dict(invitation_id=invitation_id,license_id=license_id,installation_id=license[0],
                   source="administrator bookkeeping; invitation grants no authority"))

    def status(self,license_id=None,now=None):
        now=time.time() if now is None else now
        with self.connection() as con:
            rows=con.execute("SELECT * FROM licenses"+(" WHERE license_id=?" if license_id else ""),(license_id,) if license_id else ()).fetchall()
            if license_id and not rows:raise Conflict("Unknown license")
            events=[dict(seq=r["seq"],at=r["at"],event=r["event"],data=json.loads(r["data"])) for r in con.execute("SELECT * FROM events ORDER BY seq")]
            out=[]
            for row in rows:
                claims=json.loads(row["claims"]);lid=row["license_id"]
                out.append(dict(license_id=lid,installation_id=row["installation_id"],issued_at=claims["issued_at"],expires_at=claims["expires_at"],
                    expired=now>=claims["expires_at"],artifact_state="exported" if row["exported"] else "pending_reconciliation",
                    code_sha256=row["code_sha256"],supersedes=row["supersedes"],artifact_present=artifact_present(row["artifact_path"]),
                    replacement_license_ids=[r[0] for r in con.execute("SELECT license_id FROM licenses WHERE supersedes=?",(lid,))],
                    acknowledgment_reported=any(e["event"]=="analysis_acknowledged" and e["data"]["license_id"]==lid for e in events),
                    revocation_list_issued=any(e["event"]=="revocation_list_issued" and lid in e["data"]["license_ids"] for e in events)))
        invitations=[]
        for e in events:
            if e["event"]=="invitation_prepared":
                data=e["data"];iid=data["invitation_id"]
                invitations.append(dict(invitation_id=iid,sent=False,grants_authority=False,
                    artifact_state="exported" if any(x["event"]=="invitation_draft_exported" and x["data"]["invitation_id"]==iid for x in events) else "pending_reconciliation",
                    artifact_present=artifact_present(data["draft_path"]),package_sha256=data["package_sha256"]))
        return {"licenses":out,"invitations":invitations,"events":events,"limits":DISCLAIMER}
