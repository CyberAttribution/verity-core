"""Administrator CLI for tracked, offline pilot enrollment. Never sends messages."""
import argparse,json,time,uuid,sys,sqlite3
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from verity_core import activation
from verity_core.pilot_registry import Registry,read_private
from verity_core.trust.verify_decision import load

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=["issue","adopt","status","reconcile","ack","record-revocations","retry-export"])
    p.add_argument("--registry",required=True);p.add_argument("--private-key");p.add_argument("--public-key")
    p.add_argument("--license-id");p.add_argument("--installation-id");p.add_argument("--code-file")
    p.add_argument("--preserve-partial",action="store_true");p.add_argument("--output");p.add_argument("--replace");p.add_argument("--days",type=int,default=7)
    p.add_argument("--export");p.add_argument("--checkpoint")
    a=p.parse_args();reg=Registry(a.registry)
    if a.operation in ("reconcile","retry-export","ack") and not a.license_id:p.error("--license-id is required")
    if a.operation=="ack" and not all((a.installation_id,a.export,a.checkpoint)):p.error("ack requires --installation-id, --export and --checkpoint")
    if a.operation=="retry-export":
        if not a.private_key:p.error("retry-export requires --private-key")
        key=serialization.load_pem_private_key(read_private(a.private_key),None)
        if not isinstance(key,Ed25519PrivateKey):raise ValueError("Ed25519 issuer required")
        reg.retry_export(a.license_id,key,a.preserve_partial);print(json.dumps(reg.status(a.license_id),indent=2));return
    if a.operation=="status":
        print(json.dumps(reg.status(a.license_id),indent=2));return
    if a.operation=="reconcile":
        reg.reconcile(a.license_id);print(json.dumps(reg.status(a.license_id),indent=2));return
    if a.operation=="ack":
        reg.acknowledge(a.license_id,a.installation_id,load(a.export),load(a.checkpoint))
        print(json.dumps(reg.status(a.license_id),indent=2));return
    if a.operation=="issue":
        if not a.private_key or not a.installation_id or not a.output:p.error("issue requires --private-key, --installation-id and --output")
        key=serialization.load_pem_private_key(read_private(a.private_key),None)
        if not isinstance(key,Ed25519PrivateKey):raise ValueError("Ed25519 issuer required")
        public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        if not 1<=a.days<=30:raise ValueError("Pilot validity must be 1 to 30 days")
        now=int(time.time());lid=a.license_id or "pilot-"+uuid.uuid4().hex
        activation._id(a.installation_id);activation._id(lid)
        c=dict(schema="verity_pilot_entitlement/v1",installation_id=a.installation_id,license_id=lid,issued_at=now,expires_at=now+a.days*86400)
        payload=activation.canonical(c);code=activation.encode(payload)+"."+activation.encode(key.sign(payload))
        print(json.dumps(reg.issue(code,public,a.output,a.replace),indent=2));return
    if not a.public_key or not a.code_file:p.error("adopt/record-revocations require --public-key and --code-file")
    code=read_private(a.code_file).decode().strip();public=Path(a.public_key).read_bytes()
    if a.operation=="adopt":result=reg.issue(code,public,a.code_file,a.replace,existing=True)
    else:
        reg.record_revocations(code,public,a.code_file);result=reg.status()
    print(json.dumps(result,indent=2))
if __name__=="__main__":
    try:main()
    except (ValueError,OSError,TypeError,KeyError,IndexError,sqlite3.Error) as e:
        print("Enrollment operation failed: "+str(e)+". A reserved issuance can be repaired with retry-export/reconcile; preserve any partial artifact before retrying.",file=sys.stderr)
        raise SystemExit(1)
