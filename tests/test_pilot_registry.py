import json,time
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from verity_core import activation
from verity_core.pilot_registry import Registry,Conflict
from swarmtracker_node_ui.decision_runs import analysis_manifest
from verity_core.trust import decision

@pytest.fixture
def issuer():
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    def code(lid,iid="install-test"):
        now=int(time.time());c=dict(schema="verity_pilot_entitlement/v1",installation_id=iid,license_id=lid,issued_at=now,expires_at=now+86400)
        payload=activation.canonical(c);return activation.encode(payload)+"."+activation.encode(key.sign(payload))
    return key,public,code

def test_duplicate_replacement_expiry_and_no_false_revocation(tmp_path,issuer):
    key,pub,code=issuer;r=Registry(tmp_path/"private/registry.db")
    r.issue(code("first"),pub,tmp_path/"first.code")
    with pytest.raises(Conflict):r.issue(code("first"),pub,tmp_path/"duplicate.code")
    assert not (tmp_path/"duplicate.code").exists()
    r.issue(code("replacement"),pub,tmp_path/"replacement.code","first")
    first=r.status("first")["licenses"][0]
    assert first["replacement_license_ids"]==["replacement"]
    assert not first["revocation_list_issued"]
    assert r.status("first",now=time.time()+90000)["licenses"][0]["expired"]
    assert "does not invalidate" in r.status()["limits"]
    with pytest.raises(Conflict):r.issue(code("wrong-install","other-install"),pub,tmp_path/"wrong.code","first")

def test_export_failure_is_tracked_and_reconciliation_is_safe(tmp_path,issuer,monkeypatch):
    import verity_core.pilot_registry as m
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db");raw=code("pending")
    def fail(path,data):raise OSError("controlled disk failure")
    monkeypatch.setattr(m,"save_private_new",fail)
    with pytest.raises(OSError):r.issue(raw,pub,tmp_path/"pending.code")
    assert r.status("pending")["licenses"][0]["artifact_state"]=="pending_reconciliation"
    with pytest.raises(FileNotFoundError):r.reconcile("pending")
    p=tmp_path/"pending.code";p.write_text("partial");p.chmod(0o600)
    with pytest.raises(ValueError):r.reconcile("pending")
    p.write_text(raw);r.reconcile("pending");r.reconcile("pending")
    assert r.status("pending")["licenses"][0]["artifact_state"]=="exported"
    events=r.status()["events"]
    assert [e["event"] for e in events]==["issuance_prepared","issuance_exported"]
    assert raw not in json.dumps(r.status())

def test_reservation_write_failure_creates_no_code(tmp_path,issuer,monkeypatch):
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db")
    def fail(*a):raise OSError("controlled registry write failure")
    monkeypatch.setattr(r,"event",fail)
    with pytest.raises(OSError):r.issue(code("first"),pub,tmp_path/"first.code")
    assert r.status()["licenses"]==[] and not (tmp_path/"first.code").exists()

def test_registry_rejects_public_or_symlink_file(tmp_path):
    p=tmp_path/"registry";p.touch();p.chmod(0o644)
    with pytest.raises(ValueError):Registry(p)
    p.chmod(0o600);link=tmp_path/"link";link.symlink_to(p)
    with pytest.raises(ValueError):Registry(link)

def test_revocation_registry_does_not_claim_remote_import_and_cannot_drop_ids(tmp_path,issuer):
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db")
    def rev(revision,ids):
        claims=dict(schema="verity_pilot_revocations/v1",revision=revision,issued_at=int(time.time()),license_ids=ids)
        payload=activation.canonical(claims);raw=activation.encode(payload)+"."+activation.encode(key.sign(payload))
        path=tmp_path/(str(revision)+".rev");path.write_text(raw)
        return raw,path
    raw,path=rev(1,["first"]);r.record_revocations(raw,pub,path)
    assert r.status()["events"][-1]["data"]["installation_import"]=="not observed"
    with pytest.raises(Conflict):r.record_revocations(raw,pub,path)
    raw,path=rev(2,[])
    with pytest.raises(Conflict):r.record_revocations(raw,pub,path)

def test_ack_requires_integrity_installation_and_real_analysis_snapshot(tmp_path,issuer):
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db");r.issue(code("first"),pub,tmp_path/"code")
    job={"report_id":"test-analysis","report":{"fused_verdict":"Controlled local assessment"},"items":[{"text":"Synthetic evidence"}],
         "decision_participant":{"id":"local","name":"Local runtime","framework":"Ollama","model":"test","identity_provenance":"self-reported","delegated_by":None}}
    record=decision.new_run(analysis_manifest(job));bundle=decision.export(record);anchor=decision.checkpoint(record)
    with pytest.raises(Conflict):r.acknowledge("first","different-install",bundle,anchor)
    r.acknowledge("first","install-test",bundle,anchor);r.acknowledge("first","install-test",bundle,anchor)
    assert r.status("first")["licenses"][0]["acknowledgment_reported"]
    assert sum(e["event"]=="analysis_acknowledged" for e in r.status()["events"])==1
    anchor["head_hash"]="0"*64
    with pytest.raises(ValueError):r.acknowledge("first","install-test",bundle,anchor)
