"""Offline pilot entitlement verification. No network or cloud authorization.

Ed25519 signatures authenticate issuer claims, not the local operator. Offline
revocation is effective after importing a signed revocation list; expiry bounds
offline validity. This is not a tamper-proof DRM or machine attestation system.
"""
import base64
import json
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .json_safety import bounded_json_loads


def encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+",value):
        raise ValueError("Invalid activation encoding")
    return base64.urlsafe_b64decode(value + "=" * (-len(value)%4))


def canonical(claims):
    return json.dumps(claims,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def unpack(code, public_key):
    if not isinstance(code,str) or len(code)>8192: raise ValueError("Invalid activation code")
    parts=code.strip().split(".")
    if len(parts)!=2: raise ValueError("Invalid activation code")
    try:
        payload,signature=decode(parts[0]),decode(parts[1])
        key=serialization.load_pem_public_key(public_key)
        if not isinstance(key,Ed25519PublicKey): raise ValueError("Unsupported issuer key")
        key.verify(signature,payload)
        claims=bounded_json_loads(payload.decode())
        if not isinstance(claims,dict) or canonical(claims)!=payload: raise ValueError("Noncanonical claims")
        return claims
    except Exception:
        raise ValueError("Activation signature or claims invalid") from None


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}",value):
        raise ValueError("Invalid activation identifier")


def entitlement(code, public_key, installation_id, now, revoked=()):
    c=unpack(code,public_key)
    if set(c)!={"schema","installation_id","license_id","issued_at","expires_at"} or c["schema"]!="verity_pilot_entitlement/v1":
        raise ValueError("Invalid entitlement claims")
    _id(c["installation_id"]);_id(c["license_id"])
    for key in ("issued_at","expires_at"):
        if type(c[key]) is not int or c[key]<0:raise ValueError("Invalid entitlement time")
    if c["expires_at"]<=c["issued_at"] or c["issued_at"]>now+60 or now>=c["expires_at"]:
        raise ValueError("Activation expired or not yet valid")
    if c["installation_id"]!=installation_id:raise ValueError("Activation belongs to another installation")
    if c["license_id"] in revoked:raise ValueError("Activation was revoked by an imported issuer list")
    return c


def revocations(code, public_key, now, minimum_revision=0):
    c=unpack(code,public_key)
    if set(c)!={"schema","revision","issued_at","license_ids"} or c["schema"]!="verity_pilot_revocations/v1":
        raise ValueError("Invalid revocation claims")
    if type(c["revision"]) is not int or c["revision"]<=minimum_revision:
        raise ValueError("Revocation revision must advance")
    if type(c["issued_at"]) is not int or not 0<=c["issued_at"]<=now+60:
        raise ValueError("Invalid revocation time")
    if not isinstance(c["license_ids"],list) or len(c["license_ids"])>100:
        raise ValueError("Invalid revocation list")
    for lid in c["license_ids"]:_id(lid)
    return c
