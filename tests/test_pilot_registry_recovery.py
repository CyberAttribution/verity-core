import sqlite3
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_pilot_registry import issuer
from verity_core.pilot_registry import Registry,Conflict

def test_registry_uses_wal_and_missing_artifact_is_reproducibly_reexported(tmp_path,issuer,monkeypatch):
    import verity_core.pilot_registry as m
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db")
    with sqlite3.connect(r.path) as con:assert con.execute("PRAGMA journal_mode").fetchone()[0]=="wal"
    original=m.save_private_new
    monkeypatch.setattr(m,"save_private_new",lambda *a:(_ for _ in ()).throw(OSError("disk failure")))
    raw=code("recoverable")
    with pytest.raises(OSError):r.issue(raw,pub,tmp_path/"code")
    monkeypatch.setattr(m,"save_private_new",original)
    with pytest.raises(ValueError):r.retry_export("recoverable",Ed25519PrivateKey.generate())
    r.retry_export("recoverable",key)
    assert (tmp_path/"code").read_text()==raw
    assert r.status("recoverable")["licenses"][0]["artifact_state"]=="exported"

def test_replacement_cannot_fork_or_replace_an_unexported_issuance(tmp_path,issuer,monkeypatch):
    import verity_core.pilot_registry as m
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db")
    r.issue(code("first"),pub,tmp_path/"first")
    r.issue(code("second"),pub,tmp_path/"second","first")
    with pytest.raises(Conflict):r.issue(code("fork"),pub,tmp_path/"fork","first")
    monkeypatch.setattr(m,"save_private_new",lambda *a:(_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):r.issue(code("pending"),pub,tmp_path/"pending")
    with pytest.raises(Conflict):r.issue(code("third"),pub,tmp_path/"third","pending")

def test_reconciliation_refuses_symlink_even_when_content_matches(tmp_path,issuer,monkeypatch):
    import verity_core.pilot_registry as m
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db");raw=code("pending")
    monkeypatch.setattr(m,"save_private_new",lambda *a:(_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):r.issue(raw,pub,tmp_path/"pending")
    other=tmp_path/"other";other.write_text(raw);other.chmod(0o600)
    (tmp_path/"pending").symlink_to(other)
    with pytest.raises(ValueError):r.reconcile("pending")

def test_partial_export_can_be_preserved_and_reissued_without_raw_code_in_registry(tmp_path,issuer,monkeypatch):
    import verity_core.pilot_registry as m
    key,pub,code=issuer;r=Registry(tmp_path/"registry.db");raw=code("partial")
    original=m.save_private_new
    def fail(path,data):
        path.write_bytes(data[:80]);path.chmod(0o600);raise OSError("interrupted write")
    monkeypatch.setattr(m,"save_private_new",fail)
    with pytest.raises(OSError):r.issue(raw,pub,tmp_path/"code")
    monkeypatch.setattr(m,"save_private_new",original)
    with pytest.raises(ValueError):r.retry_export("partial",key)
    r.retry_export("partial",key,preserve_partial=True)
    assert (tmp_path/"code").read_text()==raw
    backups=list(tmp_path.glob("code.partial-*"))
    assert len(backups)==1 and backups[0].read_bytes()==raw.encode()[:80]
    assert r.status("partial")["licenses"][0]["artifact_state"]=="exported"
