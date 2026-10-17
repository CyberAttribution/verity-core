"""Offline publisher utility. Keep the private key outside packages and repositories."""
import argparse
import json
from pathlib import Path
import time
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from verity_core.activation import canonical,encode
from verity_core.pilot_registry import read_private,save_private_new
from verity_core.release_update import artifact_digest,app_identity,verify_manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--private-key",required=True);p.add_argument("--artifact",required=True);p.add_argument("--output",required=True)
    p.add_argument("--release-id",required=True);p.add_argument("--version",required=True);p.add_argument("--channel",choices=("pilot","stable"),required=True)
    p.add_argument("--architecture",choices=("arm64","x86_64"),required=True);p.add_argument("--min-os",required=True)
    p.add_argument("--schema",type=int,required=True);p.add_argument("--notes",required=True)
    a=p.parse_args()
    if app_identity(a.artifact)!=a.version:raise ValueError("Release version differs from app Info.plist")
    key=serialization.load_pem_private_key(read_private(a.private_key),None)
    if not isinstance(key,Ed25519PrivateKey):raise ValueError("Ed25519 publisher key required")
    claims=dict(schema="verity_release/v1",release_id=a.release_id,version=a.version,
        artifact_sha256=artifact_digest(a.artifact),platform="macOS",architecture=a.architecture,
        min_os=a.min_os,channel=a.channel,min_schema=a.schema,target_schema=a.schema,
        migration="none",notes=a.notes)
    raw=canonical(claims);signed=encode(raw)+"."+encode(key.sign(raw))
    verify_manifest(signed,key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    save_private_new(a.output,signed.encode())
    print(json.dumps({"release_id":a.release_id,"version":a.version,"artifact_sha256":claims["artifact_sha256"],"manifest_path":str(Path(a.output).absolute())}))


if __name__=="__main__":main()
