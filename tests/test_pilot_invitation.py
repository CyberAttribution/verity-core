import json,os
import pytest
from test_pilot_registry import issuer
from verity_core.pilot_registry import Registry,Conflict,file_fingerprint

def test_draft_is_unsent_noncredential_and_association_is_installation_specific(tmp_path,issuer):
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db")
    pkg=tmp_path/"pilot.zip";pkg.write_bytes(b"controlled package fixture")
    draft=r.prepare_invitation(pkg,tmp_path/"invite.txt","invite-test")
    assert draft["sent"] is False and draft["grants_authority"] is False
    text=(tmp_path/"invite.txt").read_text()
    assert "DRAFT" in text and "notarization" in text and "not an activation" in text
    assert file_fingerprint(pkg) in text
    with pytest.raises(Conflict):r.prepare_invitation(pkg,tmp_path/"duplicate.txt","invite-test")
    r.issue(code("first"),pub,tmp_path/"first.code",invitation_id="invite-test")
    with pytest.raises(Conflict):r.issue(code("other","other-install"),pub,tmp_path/"other.code",invitation_id="invite-test")
    assert not (tmp_path/"other.code").exists()
    assert len(r.status()["licenses"])==1
    assert all(e["data"].get("sent",False) is False for e in r.status()["events"])

def test_draft_failure_is_pending_and_retry_preserves_partial_file(tmp_path,monkeypatch):
    import verity_core.pilot_registry as m
    pkg=tmp_path/"pilot.zip";pkg.write_bytes(b"package")
    r=Registry(tmp_path/"registry.db");original=m.save_private_new
    def fail(path,raw):
        path.write_bytes(raw[:50]);path.chmod(0o600);raise OSError("controlled write failure")
    monkeypatch.setattr(m,"save_private_new",fail)
    with pytest.raises(OSError):r.prepare_invitation(pkg,tmp_path/"invite.txt","invite-test")
    assert r.status()["invitations"][0]["artifact_state"]=="pending_reconciliation"
    monkeypatch.setattr(m,"save_private_new",original)
    with pytest.raises(ValueError):r.retry_invitation("invite-test")
    r.retry_invitation("invite-test",True)
    assert len(list(tmp_path.glob("invite.txt.partial-*")))==1
    assert r.status()["invitations"][0]["artifact_state"]=="exported"
    r.retry_invitation("invite-test")
    assert sum(e["event"]=="invitation_draft_exported" for e in r.status()["events"])==1

def test_package_fingerprint_refuses_symlink_fifo_and_oversize(tmp_path):
    package=tmp_path/"package";package.write_bytes(b"example")
    link=tmp_path/"link";link.symlink_to(package)
    with pytest.raises(OSError):file_fingerprint(link)
    with pytest.raises(ValueError):file_fingerprint(package,limit=1)
    fifo=tmp_path/"fifo";os.mkfifo(fifo)
    with pytest.raises(ValueError):file_fingerprint(fifo)
