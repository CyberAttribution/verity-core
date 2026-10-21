import json
import plistlib
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from verity_core import activation,release_update
from verity_core.pilot_registry import Registry,export_history,verify_history


def setup(tmp_path):
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    reg=Registry(tmp_path/"registry.db")
    now=int(time.time())
    for name in ("alpha","beta"):
        claims=dict(schema="verity_pilot_entitlement/v1",installation_id=name,license_id="license-"+name,issued_at=now,expires_at=now+86400)
        raw=activation.canonical(claims)
        reg.issue(activation.encode(raw)+"."+activation.encode(key.sign(raw)),public,tmp_path/(name+".code"))
    artifact=tmp_path/"release.app";artifact.mkdir();(artifact/"binary").write_text("new")
    app_info(artifact,"2.0.0")
    claims=dict(schema="verity_release/v1",release_id="release-2",version="2.0.0",artifact_sha256=release_update.artifact_digest(artifact),platform="macOS",architecture="arm64",min_os="15.0",channel="pilot",min_schema=1,target_schema=1,migration="none",notes="Synthetic local update")
    raw=activation.canonical(claims);signed=activation.encode(raw)+"."+activation.encode(key.sign(raw))
    return reg,key,public,artifact,claims,signed


def app_info(app,version):
    contents=app/"Contents";contents.mkdir(exist_ok=True)
    with (contents/"Info.plist").open("wb") as out:
        plistlib.dump(dict(CFBundleIdentifier="ai.swarmtracker.verity.pilot",CFBundleShortVersionString=version),out)


def preview(reg,public,artifact,signed,installation="alpha",installed="1.0.0",schema=1,rollback=False):
    return release_update.inspect(signed,public,artifact,installation_id=installation,installed_version=installed,schema_version=schema,authorization=reg.release_allowed,rollback=rollback,system=("Darwin","arm64","15.1"))


def test_registry_two_installations_and_authorization(tmp_path):
    reg,_,public,artifact,claims,signed=setup(tmp_path)
    reg.observe("alpha",version="1.0.0",channel="pilot",update_state="current",contact_at=int(time.time()),imported_revocation_revision=1)
    reg.observe("beta",version="0.9.0",channel="pilot",update_state="unknown",contact_at=int(time.time())-90000)
    by_id={r["installation_id"]:r for r in reg.installations()}
    assert by_id["alpha"]["contact_state"]=="reported"
    assert by_id["beta"]["contact_state"]=="stale"
    assert by_id["alpha"]["reported"]["imported_revocation_revision"]==1
    assert by_id["beta"]["reported"]["imported_revocation_revision"] is None
    assert all(r["acknowledgment"]=="unknown" for r in by_id.values())
    with pytest.raises(ValueError,match="not authorized"):preview(reg,public,artifact,signed)
    reg.authorize_release("alpha","pilot","release-2",True,"operator grants this release")
    assert preview(reg,public,artifact,signed)["authorization_event"]
    with pytest.raises(ValueError,match="not authorized"):preview(reg,public,artifact,signed,"beta")
    reg.authorize_release("alpha","pilot","release-2",False,"withdrawn")
    with pytest.raises(ValueError,match="not authorized"):preview(reg,public,artifact,signed)
    assert [e["data"]["allowed"] for e in reg.status()["events"] if e["event"]=="release_authorization_changed"]==[True,False]


def test_local_credential_expiry_revocation_and_replacement(tmp_path):
    reg,key,public,_,_,_=setup(tmp_path)
    profile=tmp_path/"profile";profile.mkdir()
    now=int(time.time())
    older=(tmp_path/"alpha.code").read_text()
    def write(code,revocations=None):
        (profile/"installation.json").write_text(json.dumps(dict(installation_id="alpha",settings={},max_seen=now,revocations=revocations,activation=code)))
    write(older)
    assert release_update.validate_installation(profile,public,now)["license_id"]=="license-alpha"
    successor=dict(schema="verity_pilot_entitlement/v1",installation_id="alpha",license_id="license-alpha-2",issued_at=now,expires_at=now+86400)
    raw=activation.canonical(successor);new=activation.encode(raw)+"."+activation.encode(key.sign(raw))
    reg.issue(new,public,tmp_path/"new.code",supersedes="license-alpha")
    # An issued replacement has no remote power over the still-valid older code.
    assert release_update.validate_installation(profile,public,now)["license_id"]=="license-alpha"
    rev=dict(schema="verity_pilot_revocations/v1",revision=1,issued_at=now,license_ids=["license-alpha"])
    raw=activation.canonical(rev);revoked=activation.encode(raw)+"."+activation.encode(key.sign(raw))
    write(older,revoked)
    with pytest.raises(ValueError,match="revoked"):release_update.validate_installation(profile,public,now)
    write(new,revoked)
    assert release_update.validate_installation(profile,public,now)["license_id"]=="license-alpha-2"
    with pytest.raises(ValueError,match="expired"):release_update.validate_installation(profile,public,now+86400)


def test_signature_digest_compatibility_replay_and_recovery(tmp_path):
    reg,key,public,artifact,claims,signed=setup(tmp_path)
    reg.authorize_release("alpha","pilot","release-2",True,"fixture approval")
    plan=preview(reg,public,artifact,signed)
    with pytest.raises(ValueError,match="signature"):preview(reg,public,artifact,signed[:-2]+"aa")
    (artifact/"binary").write_text("tampered")
    with pytest.raises(ValueError,match="digest"):preview(reg,public,artifact,signed)
    (artifact/"binary").write_text("new")
    app_info(artifact,"9.0.0")
    with pytest.raises(ValueError,match="version differs"):preview(reg,public,artifact,signed)
    app_info(artifact,"2.0.0")
    with pytest.raises(ValueError,match="Replayed"):preview(reg,public,artifact,signed,installed="2.0.0")
    with pytest.raises(ValueError,match="schema"):preview(reg,public,artifact,signed,schema=2)
    with pytest.raises(ValueError,match="incompatible"):release_update.inspect(signed,public,artifact,installation_id="alpha",installed_version="1.0.0",schema_version=1,authorization=reg.release_allowed,system=("Darwin","x86_64","15.1"))
    target=tmp_path/"installed.app";target.mkdir();(target/"binary").write_text("old")
    profile=tmp_path/"profile";profile.mkdir();(profile/"evidence.json").write_text("preserved")
    with pytest.raises(ValueError,match="approval"):release_update.install(plan,artifact,target,profile,approval="",history=tmp_path/"refused",authorization=reg.release_allowed)
    reg.authorize_release("alpha","pilot","release-2",False,"withdraw before switch")
    with pytest.raises(ValueError,match="withdrawn"):release_update.install(plan,artifact,target,profile,approval=plan["approval_phrase"],history=tmp_path/"withdrawn",authorization=reg.release_allowed)
    reg.authorize_release("alpha","pilot","release-2",True,"renew before switch")
    with pytest.raises(InterruptedError):release_update.install(plan,artifact,target,profile,approval=plan["approval_phrase"],history=tmp_path/"interrupted",authorization=reg.release_allowed,simulate_interrupt=True)
    assert (target/"binary").read_text()=="old" and (profile/"evidence.json").read_text()=="preserved"
    assert json.loads((tmp_path/"interrupted/outcome.json").read_text())["state"]=="recovered"
    def broken(profile,manifest):
        (profile/"evidence.json").write_text("lost")
        raise RuntimeError("migration failed")
    with pytest.raises(RuntimeError):release_update.install(plan,artifact,target,profile,approval=plan["approval_phrase"],history=tmp_path/"migration-failed",authorization=reg.release_allowed,migrate=broken)
    assert (target/"binary").read_text()=="old" and (profile/"evidence.json").read_text()=="preserved"
    release_update.install(plan,artifact,target,profile,approval=plan["approval_phrase"],history=tmp_path/"success",authorization=reg.release_allowed)
    reg.record_update("alpha",plan,tmp_path/"success/outcome.json")
    reg.record_update("alpha",plan,tmp_path/"success/outcome.json")
    exported=tmp_path/"history.json";checkpoint=tmp_path/"history.sha256"
    export_history(reg,"alpha",exported,checkpoint)
    assert verify_history(exported,checkpoint)
    assert exported.read_text().count('local_update_recorded')==1
    assert (tmp_path/"alpha.code").read_text() not in exported.read_text()
    assert 'artifact_path' not in exported.read_text()
    checkpoint.write_text("0"*64)
    assert not verify_history(exported,checkpoint)
    assert (target/"binary").read_text()=="new" and (profile/"evidence.json").read_text()=="preserved"
    assert (tmp_path/"success/previous.app/binary").read_text()=="old"
    old=tmp_path/"rollback.app";old.mkdir();(old/"binary").write_text("old");app_info(old,"1.0.0")
    older=dict(claims,release_id="release-1",version="1.0.0",artifact_sha256=release_update.artifact_digest(old))
    raw=activation.canonical(older);signed_old=activation.encode(raw)+"."+activation.encode(key.sign(raw))
    reg.authorize_release("alpha","pilot","release-1",True,"explicit recovery rollback")
    rollback=preview(reg,public,old,signed_old,installed="2.0.0",rollback=True)
    release_update.install(rollback,old,target,profile,approval=rollback["approval_phrase"],history=tmp_path/"rollback",authorization=reg.release_allowed)
    assert (target/"binary").read_text()=="old"


def test_operator_ui_registry_and_signed_preview(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui,setup_routes,decision_routes
    monkeypatch.setattr(release_update.platform,"system",lambda:"Darwin")
    monkeypatch.setattr(release_update.platform,"machine",lambda:"arm64")
    monkeypatch.setattr(release_update.platform,"mac_ver",lambda:("15.1",("","",""),""))
    reg,key,public,artifact,_,signed=setup(tmp_path)
    reg.authorize_release("alpha","pilot","release-2",True,"synthetic release")
    reg.observe("beta",version="0.9.0",channel="pilot",update_state="unknown")
    registry_path=tmp_path/"registry.db";key_path=tmp_path/"publisher.pem";key_path.write_bytes(public)
    manifest=tmp_path/"manifest.signed";manifest.write_text(signed)
    monkeypatch.setenv("VERITY_PILOT_REGISTRY",str(registry_path))
    monkeypatch.setenv("VERITY_RELEASE_PUBLIC_KEY",str(key_path))
    monkeypatch.setenv("VERITY_ACTIVATION_PUBLIC_KEY",str(key_path))
    monkeypatch.setenv("VERITY_APP_VERSION","1.0.0")
    monkeypatch.delenv("VERITY_REQUIRE_ACTIVATION",raising=False)
    monkeypatch.setattr(setup_routes,"path",lambda:tmp_path/"installation.json")
    setup_routes.write(dict(installation_id="alpha",settings={},max_seen=0,revocations=None,activation=(tmp_path/"alpha.code").read_text()))
    client=TestClient(ui.app,base_url="http://127.0.0.1")
    response=client.get("/setup/registry")
    assert response.status_code==200 and "alpha" in response.text and "beta" in response.text
    assert "not authenticated hardware identities" in response.text and signed not in response.text
    assert "Imported revocation revision (reported): unknown" in response.text
    preview=client.post("/setup",data=dict(action="inspect_release",operator_token=decision_routes.TOKEN,manifest_path=str(manifest),artifact_path=str(artifact)))
    assert preview.status_code==200 and "Authorized release 2.0.0" in preview.text
    assert "awaiting explicit operator approval" in preview.text
    assert client.post("/setup",data=dict(action="inspect_release",manifest_path=str(manifest),artifact_path=str(artifact))).status_code==403


def test_repair_inspection_requires_independent_digest_and_never_mutates(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    target = tmp_path / "installed.app"
    empty = release_update.inspect_repair(history, target)
    assert not empty["manual_repair_eligible"] and empty["journal_state"] is None
    assert empty["proposed_change"] is None
    previous = history / "previous.app"
    previous.mkdir()
    (previous / "binary").write_text("retained previous app")
    profile = history / "profile-backup"
    profile.mkdir()
    (profile / "evidence").write_text("preserved")
    (history / "outcome.json").write_text(json.dumps({"state": "prepared"}))
    digest = release_update.artifact_digest(previous)
    before = {str(p): p.read_bytes() for p in history.rglob("*") if p.is_file()}
    unverified = release_update.inspect_repair(history, target)
    assert unverified["retained_previous_app"]["present"] and unverified["retained_profile_backup"]["present"]
    assert not unverified["manual_repair_eligible"]
    assert not release_update.inspect_repair(history, target, expected_previous_digest="0"*64)["manual_repair_eligible"]
    report = release_update.inspect_repair(history, target, expected_previous_digest=digest)
    assert report["manual_repair_eligible"] and report["requires_operator_review"]
    assert not report["mutation_performed"] and not report["automatic_recovery"]
    assert report["proposed_change"]["expected_sha256"] == digest
    assert not target.exists()
    assert before == {str(p): p.read_bytes() for p in history.rglob("*") if p.is_file()}
    target.mkdir()
    assert not release_update.inspect_repair(history, target, expected_previous_digest=digest)["manual_repair_eligible"]


def test_repair_inspection_refuses_symlink_and_unknown_journal(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "outcome.json").write_text('{"state":"unknown"}')
    with pytest.raises(ValueError, match="journal"):
        release_update.inspect_repair(history, tmp_path / "target")
    (history / "outcome.json").unlink()
    (history / "previous.app").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        release_update.inspect_repair(history, tmp_path / "target")


def _kill_during_app_switch(root):
    import os, signal
    reg, _, public, artifact, _, signed = setup(root)
    reg.authorize_release("alpha", "pilot", "release-2", True, "Disposable test")
    plan = preview(reg, public, artifact, signed)
    target = root / "installed.app"
    target.mkdir()
    (target / "binary").write_text("old")
    (root / "previous.sha256").write_text(release_update.artifact_digest(target))
    profile = root / "profile"
    profile.mkdir()
    (profile / "evidence").write_text("preserved")
    original = Path.rename

    def interrupted(path, destination):
        result = original(path, destination)
        if path == target:
            os.kill(os.getpid(), signal.SIGKILL)
        return result

    Path.rename = interrupted
    release_update.install(plan, artifact, target, profile, approval=plan["approval_phrase"],
                           history=root / "history", authorization=reg.release_allowed)


def test_hard_kill_switch_diagnosis_is_read_only_and_retains_backups(tmp_path):
    import multiprocessing, signal
    process = multiprocessing.get_context("spawn").Process(target=_kill_during_app_switch, args=(tmp_path,))
    process.start()
    try:
        process.join(10)
        assert not process.is_alive()
        assert process.exitcode == -signal.SIGKILL
        assert not (tmp_path / "installed.app").exists()
        expected = (tmp_path / "previous.sha256").read_text()
        result = release_update.inspect_repair(tmp_path / "history", tmp_path / "installed.app",
                                              expected_previous_digest=expected)
        assert result["journal_state"] == "prepared" and result["manual_repair_eligible"]
        assert result["retained_previous_app"]["present"] and result["retained_profile_backup"]["present"]
        assert not result["mutation_performed"] and not (tmp_path / "installed.app").exists()
        assert (tmp_path / "profile/evidence").read_text() == "preserved"
    finally:
        if process.is_alive():
            process.kill()
        process.join(10)
