"""Offline pilot administrator CLI. Keep private keys outside source/exports."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from verity_core import activation


def save_new(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['init','issue','revoke'])
    parser.add_argument('--private-key',required=True)
    parser.add_argument('--public-key');parser.add_argument('--installation-id')
    parser.add_argument('--license-id',default='pilot-'+uuid.uuid4().hex)
    parser.add_argument('--days',type=int,default=7)
    parser.add_argument('--revision',type=int)
    parser.add_argument('--revoked-license-id',action='append',default=[])
    parser.add_argument('--output')
    args=parser.parse_args();now=int(time.time())
    if args.operation=='init':
        if not args.public_key:parser.error('--public-key is required for init')
        if Path(args.private_key).exists() or Path(args.public_key).exists():raise ValueError('Issuer key output already exists')
        key=Ed25519PrivateKey.generate()
        save_new(args.private_key,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        save_new(args.public_key,key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        print('New private issuer key and public verification key saved separately. Do not include the private key in source or session exports.')
        return
    if not args.output:parser.error('--output is required')
    key=serialization.load_pem_private_key(Path(args.private_key).read_bytes(),password=None)
    if not isinstance(key,Ed25519PrivateKey):raise ValueError('Ed25519 issuer key required')
    if args.operation=='issue':
        activation._id(args.installation_id);activation._id(args.license_id)
        if not 1<=args.days<=30:raise ValueError('Pilot validity must be 1 to 30 days')
        claims=dict(schema='verity_pilot_entitlement/v1',installation_id=args.installation_id,license_id=args.license_id,issued_at=now,expires_at=now+args.days*86400)
    else:
        if not args.revision or args.revision<1:raise ValueError('Positive revocation revision required')
        for value in args.revoked_license_id:activation._id(value)
        claims=dict(schema='verity_pilot_revocations/v1',revision=args.revision,issued_at=now,license_ids=args.revoked_license_id)
    payload=activation.canonical(claims)
    code=activation.encode(payload)+'.'+activation.encode(key.sign(payload))
    save_new(args.output,code.encode())
    print(json.dumps({'output':args.output,'operation':args.operation,'expires_at':claims.get('expires_at')}))


if __name__=='__main__':main()
