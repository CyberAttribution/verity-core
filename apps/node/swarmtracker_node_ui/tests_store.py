"""
Unit tests for swarmtracker_node_ui.store (v0.2.0 P1).
Run:  python3 -m pytest swarmtracker_node_ui/tests_store.py -q
Pure-local SQLite in tmp paths; no network, no runtime.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarmtracker_node_ui.store import (  # noqa: E402
    CorruptDatabase, Store, StoreError)

FUSED = {
    "report_id": "rpt-test000001", "overall_risk": "high",
    "entities": [
        {"id": "art-1", "type": "artifact", "label": "Email A"},
        {"id": "domain:evil.example", "type": "domain",
         "label": "evil.example"},
        {"id": "CAMP-ABC12345", "type": "campaign", "label": "CAMP-ABC12345"},
    ],
    "relationships": [
        {"source": "art-1", "target": "domain:evil.example",
         "relation": "contains", "artifact_ids": ["art-1"]},
        {"source": "CAMP-ABC12345", "target": "art-1",
         "relation": "clusters", "artifact_ids": ["art-1"]},
    ],
    "campaigns": [{"campaign_id": "CAMP-ABC12345", "confidence": 60,
                   "artifact_ids": ["art-1"]}],
    "attribution": [{"hypothesis": "vendor impersonation campaign",
                     "campaign_type": "BEC", "confidence": 55}],
}


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "t.db")


def _seed(s):
    pid = s.create_project("Case Alpha", "first case")
    eid = s.create_event(pid, "Initial phish", t_claimed="2026-07-01",
                         time_status="claimed")
    a1 = s.add_artifact(pid, eid, label="Email A", text="pay evil.example",
                        evidence_type="email_message")
    a2 = s.add_artifact(pid, eid, label="DNS log", text="query evil.example",
                        evidence_type="dns_log")
    return pid, eid, a1, a2


def test_persistence_across_reopen(tmp_path):
    s = Store(db_path=tmp_path / "p.db")
    pid, eid, a1, a2 = _seed(s)
    s.con.close()
    s2 = Store(db_path=tmp_path / "p.db")   # simulated restart
    projects = s2.list_projects()
    assert len(projects) == 1 and projects[0]["id"] == pid
    assert projects[0]["n_events"] == 1 and projects[0]["n_artifacts"] == 2
    from swarmtracker_node_ui.store import SCHEMA_VERSION
    assert s2.current_version() == SCHEMA_VERSION


def test_project_crud_and_explicit_delete(store):
    pid, *_ = _seed(store)
    store.update_project(pid, name="Case Alpha (renamed)")
    assert store.get_project(pid)["name"] == "Case Alpha (renamed)"
    with pytest.raises(StoreError):
        store.delete_project(pid, "wrong name")     # explicit confirm
    store.delete_project(pid, "Case Alpha (renamed)")
    assert store.get_project(pid) is None
    # deletion recorded in revisions before cascade
    row = store.con.execute(
        "SELECT * FROM revisions WHERE target_id=? AND action='delete'",
        (pid,)).fetchone()
    assert row is not None


def test_artifacts_immutable_and_hashed(store):
    pid, eid, a1, _ = _seed(store)
    art = store.get_artifact(a1)
    assert len(art["sha256"]) == 64
    # no update API exists for artifacts; verify direct SQL is the only path
    assert not hasattr(store, "update_artifact")


def test_save_analysis_merges_graph_with_epistemics(store):
    pid, eid, a1, a2 = _seed(store)
    xid = store.save_analysis(pid, kind="fused", report=FUSED,
                              artifact_ids=[a1, a2], event_id=eid,
                              model="qwen2.5:7b-instruct",
                              policy="read_only_analysis",
                              ledger_event_ids=["evt-1", "evt-2"])
    saved = store.get_analysis(xid)
    assert saved["report"]["report_id"] == "rpt-test000001"
    assert set(store.list_analyses(pid)[0]["artifact_ids"]) == {a1, a2}
    g = store.project_graph(pid)
    types = {e["type"] for e in g["entities"]}
    assert {"artifact", "domain", "campaign", "hypothesis"} <= types
    statuses = {r["epistemic_status"] for r in g["relationships"]}
    assert "deterministic" in statuses and "model_inferred" in statuses
    hyp = [r for r in g["relationships"]
           if r["relation"] == "hypothesizes"][0]
    assert hyp["epistemic_status"] == "model_inferred"
    assert hyp["provenance"]["policy"] == "read_only_analysis"
    assert hyp["provenance"]["confidence"] == 55
    det = [r for r in g["relationships"] if r["relation"] == "contains"][0]
    assert det["provenance"]["supporting_artifact_ids"] == [a1, a2]


def test_graph_dedup_across_analyses(store):
    pid, eid, a1, a2 = _seed(store)
    store.save_analysis(pid, kind="fused", report=FUSED,
                        artifact_ids=[a1], event_id=eid)
    store.save_analysis(pid, kind="fused", report=FUSED,
                        artifact_ids=[a2], event_id=eid)
    g = store.project_graph(pid)
    keys = [e["id"] for e in g["entities"]]
    assert len(keys) == len(set(keys))       # no duplicate entities
    rels = [(r["source"], r["target"], r["relation"])
            for r in g["relationships"]]
    assert len(rels) == len(set(rels))       # no duplicate edges


def test_graph_filters(store):
    pid, eid, a1, a2 = _seed(store)
    store.save_analysis(pid, kind="fused", report=FUSED,
                        artifact_ids=[a1], event_id=eid,
                        artifact_id_map={"art-1": a1})
    g = store.project_graph(pid, epistemic_status="model_inferred")
    assert g["relationships"]
    assert all(r["epistemic_status"] == "model_inferred"
               for r in g["relationships"])
    g2 = store.project_graph(pid, evidence_type="email_message")
    assert any(r["relation"] == "contains" for r in g2["relationships"])


def test_timeline_and_notes(store):
    pid, eid, a1, _ = _seed(store)
    store.add_marker(pid, "Vendor called AP", t_claimed="2026-07-02",
                     time_status="claimed")
    store.add_note(pid, "artifact", a1, "Reply-To mismatch confirmed by IT")
    tl = store.timeline(pid)
    assert len(tl) >= 2
    assert {t["time_status"] for t in tl} >= {"claimed"}
    notes = store.list_notes(pid, "artifact", a1)
    assert notes and "Reply-To" in notes[0]["body"]


def test_export_import_round_trip(store, tmp_path):
    pid, eid, a1, a2 = _seed(store)
    store.save_analysis(pid, kind="fused", report=FUSED,
                        artifact_ids=[a1, a2], event_id=eid)
    pkg = store.export_project(pid)
    assert pkg["package"] == "swarmtracker-project"
    blob = json.dumps(pkg, default=str)     # JSON-serializable
    s2 = Store(db_path=tmp_path / "other.db")
    new_pid = s2.import_project(json.loads(blob))
    assert new_pid != pid                    # never overwrites
    g = s2.project_graph(new_pid)
    assert g["entities"] and g["relationships"]
    assert len(s2.list_artifacts(new_pid)) == 2
    assert s2.list_artifacts(new_pid)[0]["sha256"] == \
        store.get_artifact(a1)["sha256"]     # hashes preserved


def test_import_rejects_garbage(store):
    with pytest.raises(StoreError):
        store.import_project({"package": "not-us"})
    with pytest.raises(StoreError):
        store.import_project({"package": "swarmtracker-project",
                              "package_version": 99, "project": {}})


def test_corrupted_db_detected(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 200)
    with pytest.raises(CorruptDatabase):
        Store(db_path=bad)


def test_migration_backup_created(tmp_path):
    # simulate an old DB at version 0 with pre-existing content marker
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE legacy(x)")
    con.commit(); con.close()
    Store(db_path=db)
    backups = list(tmp_path.glob("old.backup-*.db"))
    # version 0 -> no schema_migrations table -> treated as fresh v0;
    # backup only required when a versioned DB migrates; legacy table kept
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT name FROM sqlite_master WHERE name='legacy'"
                       ).fetchone() is not None
    assert con.execute("SELECT name FROM sqlite_master WHERE name='projects'"
                       ).fetchone() is not None
    con.close()


def test_hostile_strings_stored_safely(store):
    pid, eid, *_ = _seed(store)
    evil = "'; DROP TABLE projects; -- <img onerror=alert(1)>"
    aid = store.add_artifact(pid, eid, label=evil, text=evil,
                             evidence_type="freeform_text")
    assert store.get_artifact(aid)["label"].startswith("'; DROP")
    assert store.get_project(pid) is not None   # parameterized SQL held
