"""Signed local release import for a stopped, disposable app target.

The publisher signature is application-level provenance, separate from Apple
Developer ID signing and notarization. The operator supplies the publisher
public key and an independently retained authorization registry.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from . import activation
from .activation import canonical, decode, _id
from .json_safety import bounded_json_loads


def artifact_digest(root):
    root=Path(root)
    if not root.is_dir() or root.is_symlink():raise ValueError("Expected an app directory")
    digest=hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel=path.relative_to(root).as_posix()
        if path.is_symlink():raise ValueError("Symlinks are unsupported in local release packages")
        if path.is_dir():continue
        if not path.is_file() or path.stat().st_size>512*1024*1024:raise ValueError("Invalid release file")
        digest.update(rel.encode()+b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def verify_manifest(signed,public_key):
    if not isinstance(signed,str) or len(signed)>16384 or len(signed.split("."))!=2:raise ValueError("Invalid signed manifest")
    try:
        raw,signature=map(decode,signed.split("."))
        key=serialization.load_pem_public_key(public_key)
        if not isinstance(key,Ed25519PublicKey):raise ValueError("Expected an Ed25519 publisher key")
        key.verify(signature,raw)
        claims=bounded_json_loads(raw.decode())
    except Exception:raise ValueError("Release signature invalid") from None
    required={"schema","release_id","version","artifact_sha256","platform","architecture","min_os","channel","min_schema","target_schema","migration","notes"}
    if not isinstance(claims,dict) or set(claims)!=required or canonical(claims)!=raw or claims["schema"]!="verity_release/v1":raise ValueError("Invalid release claims")
    _id(claims["release_id"])
    if claims["channel"] not in ("pilot","stable") or claims["platform"]!="macOS" or claims["architecture"] not in ("arm64","x86_64") or not isinstance(claims["artifact_sha256"],str) or len(claims["artifact_sha256"])!=64 or any(c not in "0123456789abcdef" for c in claims["artifact_sha256"]):raise ValueError("Invalid release compatibility or digest")
    if any(type(claims[k]) is not int or claims[k]<0 for k in ("min_schema","target_schema")) or claims["target_schema"]<claims["min_schema"]:raise ValueError("Invalid schema range")
    if any(not isinstance(claims[k],str) or not claims[k] or len(claims[k])>1000 for k in ("version","min_os","migration","notes")):raise ValueError("Invalid release text")
    return claims


def version(value):
    parts=value.split(".")
    if not 1<=len(parts)<=3 or any(not p.isdigit() or len(p)>5 for p in parts):raise ValueError("Expected numeric release version")
    return tuple(int(p) for p in (parts+["0"]*(3-len(parts))))


def validate_installation(profile,public_key,now=None):
    """Check local offline entitlement and imported revocations, not issuer bookkeeping."""
    now=time.time() if now is None else now
    path=Path(profile)/"installation.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size>32768:raise ValueError("Installation state unavailable")
    state=bounded_json_loads(path.read_text())
    if not isinstance(state,dict) or set(state)!={"installation_id","settings","max_seen","revocations","activation"}:raise ValueError("Invalid installation state")
    if now+300<state["max_seen"]:raise ValueError("Installation clock moved backward")
    revoked=activation.revocations(state["revocations"],public_key,now)["license_ids"] if state["revocations"] else []
    return activation.entitlement(state["activation"],public_key,state["installation_id"],now,revoked)


def inspect(signed,public_key,artifact,*,installation_id,installed_version,schema_version,authorization,rollback=False,system=None):
    claims=verify_manifest(signed,public_key)
    system=system or (platform.system(),platform.machine(),platform.mac_ver()[0])
    if system[0]!="Darwin" or system[1]!=claims["architecture"] or version(system[2])<version(claims["min_os"]):raise ValueError("Release incompatible with this Mac")
    if not claims["min_schema"]<=schema_version<=claims["target_schema"]:raise ValueError("Database schema incompatible; export or migrate using a supported path")
    if claims["target_schema"]!=schema_version or claims["migration"]!="none":raise ValueError("No supported migration for this release; preserve the existing profile")
    if version(claims["version"])<=version(installed_version) and not rollback:raise ValueError("Replayed or older release requires explicit rollback authorization")
    if rollback and version(claims["version"])>=version(installed_version):raise ValueError("Rollback requires an older release")
    decision=authorization(installation_id,claims["channel"],claims["release_id"])
    if not decision["allowed"]:raise ValueError("Release is not authorized for this installation")
    if artifact_digest(artifact)!=claims["artifact_sha256"]:raise ValueError("Release artifact digest mismatch")
    return dict(manifest=claims,installation_id=installation_id,authorization_event=decision["event_seq"],rollback=rollback,
                manifest_sha256=hashlib.sha256(signed.encode()).hexdigest(),verified_at=int(time.time()),
                approval_phrase="APPROVE "+claims["release_id"]+" "+claims["artifact_sha256"][:12])


def install(plan,artifact,target,profile,*,approval,history,authorization,simulate_interrupt=False,migrate=None):
    """Stop the app before calling. Journal a failed switch and retain backups."""
    if approval!=plan["approval_phrase"]:raise ValueError("Explicit operator approval required")
    current=authorization(plan["installation_id"],plan["manifest"]["channel"],plan["manifest"]["release_id"])
    if not current["allowed"]:raise ValueError("Release authorization was withdrawn before installation")
    target=Path(target);profile=Path(profile);history=Path(history)
    if not target.is_dir() or not profile.is_dir() or target.is_symlink() or profile.is_symlink():raise ValueError("Expected existing isolated target and profile")
    tr,pr,hr=target.resolve(),profile.resolve(),history.resolve()
    if tr==pr or tr in pr.parents or pr in tr.parents or any(hr==root or hr in root.parents or root in hr.parents for root in (tr,pr)):
        raise ValueError("Target, profile and history must be separate directories")
    if artifact_digest(artifact)!=plan["manifest"]["artifact_sha256"]:raise ValueError("Release artifact changed since preview")
    if target.resolve()==Path(artifact).resolve() or target in Path(artifact).parents:raise ValueError("Release source and target must differ")
    if history.exists():raise ValueError("Use a new history directory for each attempt")
    history.mkdir(mode=0o700,parents=True)
    journal=history/"outcome.json"
    def record(state,reason=""):
        raw=dict(state=state,reason=reason,release_id=plan["manifest"]["release_id"],manifest_sha256=plan["manifest_sha256"],at=int(time.time()))
        journal.write_text(json.dumps(raw,sort_keys=True));os.chmod(journal,0o600)
    # Preserve state and previous app outside the installed app bundle.
    backup=history/"profile-backup";old=history/"previous.app"
    shutil.copytree(profile,backup,symlinks=False)
    shutil.copytree(target,old,symlinks=False)
    record("prepared")
    stage=Path(tempfile.mkdtemp(prefix=".verity-update-",dir=target.parent))
    try:
        candidate=stage/target.name;shutil.copytree(artifact,candidate,symlinks=False)
        if artifact_digest(candidate)!=plan["manifest"]["artifact_sha256"]:raise ValueError("Staged digest mismatch")
        if simulate_interrupt:raise InterruptedError("Simulated interruption before replacement")
        # Replacement of a stopped app is bounded to this chosen target. The
        # previously installed app is retained in history for supervised repair.
        target.rename(stage/"old.app")
        try:candidate.rename(target)
        except BaseException:(stage/"old.app").rename(target);raise
        if migrate:migrate(profile,plan["manifest"])
        record("installed")
    except BaseException as error:
        if (stage/"old.app").exists():
            if target.exists():shutil.rmtree(target)
            (stage/"old.app").rename(target)
        # A migration may have touched state. Restore the exact pre-update
        # profile only after the old app has been put back.
        if profile.exists():shutil.rmtree(profile)
        shutil.copytree(backup,profile)
        record("recovered",type(error).__name__)
        raise
    finally:shutil.rmtree(stage,ignore_errors=True)
    return journal
