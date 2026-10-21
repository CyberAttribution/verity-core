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


@pytest.mark.parametrize("intact", [False, True])
def test_reconcile_after_revocation_observes_history_without_new_export(tmp_path, issuer, monkeypatch, intact):
    import verity_core.pilot_registry as module
    key, public, code = issuer
    registry = Registry(tmp_path / "registry.db")
    artifact = tmp_path / "pending.code"
    save = module.save_private_new

    def write_then_interrupt(path, data):
        save(path, data)
        raise OSError("Lost acknowledgment after export")

    monkeypatch.setattr(module, "save_private_new", write_then_interrupt)
    with pytest.raises(OSError):
        registry.issue(code("pending"), public, artifact)
    if not intact:
        artifact.unlink()
    claims = dict(schema="verity_pilot_revocations/v1", revision=1,
                  issued_at=int(time.time()), license_ids=["pending"])
    payload = activation.canonical(claims)
    signed = activation.encode(payload) + "." + activation.encode(key.sign(payload))
    revocations = tmp_path / "revocations.code"
    revocations.write_text(signed)
    registry.record_revocations(signed, public, revocations)
    before = artifact.read_bytes() if intact else None
    prior_events = registry.status()["events"]
    if intact:
        registry.reconcile("pending")
        registry.reconcile("pending")
        assert registry.status("pending")["licenses"][0]["artifact_state"] == "exported"
        assert registry.status()["events"][:len(prior_events)] == prior_events
        assert sum(e["event"] == "issuance_exported" for e in registry.status()["events"]) == 1
    else:
        with pytest.raises(FileNotFoundError):
            registry.reconcile("pending")
        assert registry.status()["events"] == prior_events
        assert registry.status("pending")["licenses"][0]["artifact_state"] == "pending_reconciliation"
    with pytest.raises(Conflict, match="revocation"):
        registry.retry_export("pending", key)
    assert (artifact.read_bytes() if artifact.exists() else None) == before

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

@pytest.mark.parametrize("artifact", ["missing", "partial", "intact"])
def test_retry_export_rejects_issued_revocation_before_artifact_mutation(tmp_path,issuer,artifact):
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db");path=tmp_path/"first.code"
    original=code("first");r.issue(original,pub,path)
    claims=dict(schema="verity_pilot_revocations/v1",revision=1,issued_at=int(time.time()),license_ids=["first"])
    payload=activation.canonical(claims)
    raw=activation.encode(payload)+"."+activation.encode(key.sign(payload))
    rev=tmp_path/"revocations.code";rev.write_text(raw);r.record_revocations(raw,pub,rev)
    if artifact=="missing":path.unlink()
    elif artifact=="partial":path.write_text("partial")
    before=path.read_bytes() if path.exists() else None
    events=r.status()["events"]
    with pytest.raises(Conflict,match="revocation"):
        r.retry_export("first",key,preserve_partial=True)
    assert (path.read_bytes() if path.exists() else None)==before
    assert not list(tmp_path.glob("first.code.partial-*"))
    assert r.status()["events"]==events
    # Issuer bookkeeping does not remotely invalidate an exported signature.
    assert activation.entitlement(original,pub,"install-test",time.time())["license_id"]=="first"


def test_initial_issue_refuses_license_already_on_issued_list(tmp_path,issuer):
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db")
    claims=dict(schema="verity_pilot_revocations/v1",revision=1,issued_at=int(time.time()),license_ids=["first"])
    payload=activation.canonical(claims);raw=activation.encode(payload)+"."+activation.encode(key.sign(payload))
    rev=tmp_path/"rev.code";rev.write_text(raw);r.record_revocations(raw,pub,rev)
    with pytest.raises(Conflict,match="revocation"):
        r.issue(code("first"),pub,tmp_path/"first.code")
    assert not (tmp_path/"first.code").exists()
    assert not r.status()["licenses"]
    r.issue(code("unrevoked"),pub,tmp_path/"unrevoked.code")
    assert (tmp_path/"unrevoked.code").exists()


def test_initial_issue_rechecks_revocation_committed_after_reservation(tmp_path,issuer,monkeypatch):
    from contextlib import contextmanager
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db");other=Registry(r.path)
    claims=dict(schema="verity_pilot_revocations/v1",revision=1,issued_at=int(time.time()),license_ids=["first"])
    payload=activation.canonical(claims);raw=activation.encode(payload)+"."+activation.encode(key.sign(payload))
    rev=tmp_path/"rev.code";rev.write_text(raw)
    original=r.connection;triggered=False
    @contextmanager
    def interleaved():
        nonlocal triggered
        with original() as con:yield con
        if not triggered:
            triggered=True
            other.record_revocations(raw,pub,rev)
    monkeypatch.setattr(r,"connection",interleaved)
    with pytest.raises(Conflict,match="revocation"):
        r.issue(code("first"),pub,tmp_path/"first.code")
    assert not (tmp_path/"first.code").exists()
    assert r.status("first")["licenses"][0]["artifact_state"]=="pending_reconciliation"
