"""Operator-run, stopped-app local release import; private signing keys stay offline.

Inspect before install. The approval phrase is tied to release ID and artifact
digest. Choose a fresh history directory outside the app and user profile.
"""
import argparse
import json
import plistlib
from pathlib import Path
import sys

from verity_core.pilot_registry import Registry
from verity_core.release_update import inspect,install


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=("registry","authorize","report","inspect","install"))
    p.add_argument("--registry",required=True)
    p.add_argument("--installation-id");p.add_argument("--channel");p.add_argument("--release-id")
    p.add_argument("--allow",action="store_true");p.add_argument("--deny",action="store_true");p.add_argument("--reason")
    p.add_argument("--version");p.add_argument("--update-state");p.add_argument("--source",choices=("operator-entered","installation-report"),default="operator-entered")
    p.add_argument("--manifest");p.add_argument("--publisher-public-key");p.add_argument("--artifact")
    p.add_argument("--installed-version");p.add_argument("--schema-version",type=int)
    p.add_argument("--rollback",action="store_true");p.add_argument("--approval");p.add_argument("--target");p.add_argument("--profile");p.add_argument("--history")
    a=p.parse_args();reg=Registry(a.registry)
    if a.operation=="registry":
        print(json.dumps({"installations":reg.installations(),"scope":"Operator records and reported values; installation IDs are not authenticated hardware identities. Offline installations may not know an issued revocation."},indent=2));return
    if a.operation=="authorize":
        if a.allow==a.deny or not all((a.installation_id,a.channel,a.release_id,a.reason)):p.error("authorize requires installation, channel, release, reason and exactly one of --allow/--deny")
        reg.authorize_release(a.installation_id,a.channel,a.release_id,a.allow,a.reason);print(json.dumps(reg.release_allowed(a.installation_id,a.channel,a.release_id)));return
    if a.operation=="report":
        if not all((a.installation_id,a.version,a.channel,a.update_state)):p.error("report requires installation, version, channel and update state")
        reg.observe(a.installation_id,version=a.version,channel=a.channel,update_state=a.update_state,source=a.source)
        print(json.dumps({"recorded":True,"source":a.source,"authentication":"none; operator entry or untrusted report"}));return
    if not all((a.installation_id,a.manifest,a.publisher_public_key,a.artifact,a.installed_version)) or a.schema_version is None:
        p.error("inspect/install require installation, signed manifest, publisher public key, artifact, installed version and schema version")
    plan=inspect(Path(a.manifest).read_text().strip(),Path(a.publisher_public_key).read_bytes(),a.artifact,
                 installation_id=a.installation_id,installed_version=a.installed_version,schema_version=a.schema_version,
                 authorization=reg.release_allowed,rollback=a.rollback)
    if a.operation=="inspect":print(json.dumps(plan,indent=2));return
    if not all((a.approval,a.target,a.profile,a.history)):p.error("install also requires approval, stopped target, profile and fresh history path")
    target=Path(a.target)
    with (target/"Contents/Info.plist").open("rb") as stream:
        actual=plistlib.load(stream).get("CFBundleShortVersionString")
    if actual!=a.installed_version:raise ValueError("Installed app version changed since operator inspection")
    journal=install(plan,a.artifact,target,a.profile,approval=a.approval,history=a.history,authorization=reg.release_allowed)
    reg.observe(a.installation_id,version=plan["manifest"]["version"],channel=plan["manifest"]["channel"],update_state="current",source="operator-entered")
    print(json.dumps({"journal":str(journal),"installed_version":plan["manifest"]["version"],"source":"operator-entered; no remote installation attestation"}))


if __name__=="__main__":
    try:main()
    except (ValueError,OSError,KeyError) as error:
        print("Local release operation failed: "+str(error),file=sys.stderr)
        raise SystemExit(1)
