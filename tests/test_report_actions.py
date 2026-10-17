"""Meaningful fixed-file action boundary and crash/retry regressions."""
import copy
import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from swarmtracker_node_ui import app as ui
from swarmtracker_node_ui import decision_routes, report_actions, report_routes
from verity_core.trust import record as trust


def manifest(text="# Proposed summary\n\n- A verified item.\n"):
    return {"schema": report_actions.SCHEMA, "agent_id": "openclaw-test",
            "task_id": "sanitized-session-report-summary", "model": "test-model",
            "provider": "ollama_local", "source_name": "sanitized report.md",
            "source_sha256": hashlib.sha256(b"report").hexdigest(),
            "runtime_event_id": "runtime-event-1", "proposal": text,
            "action": {"kind": "write_sanitized_summary", "target": "summary.md",
                       "sha256": hashlib.sha256(text.encode()).hexdigest()},
            "policy": report_actions.POLICY}


@pytest.fixture
def db(tmp_path):
    return report_actions.Store(tmp_path / "records.db", tmp_path / "output")


def approve(db, view):
    return db.decide(view["id"], "approved", "Reviewed exact text", "operator", view["head_hash"])


def test_approved_real_write_and_independent_file_digest(db):
    view = approve(db, db.create(manifest()))
    assert db.observed_file()["present"] is False
    result = db.execute(view["id"], "operator", view["head_hash"])
    observed = db.observed_file()
    assert result["state"] == "written" and observed["sha256"] == view["manifest"]["action"]["sha256"]
    assert (db.output_dir / "summary.md").read_text() == view["manifest"]["proposal"]
    bundle = report_actions.export(result["record"])
    assert report_actions.verify(bundle, report_actions.checkpoint(result["record"]))
    with pytest.raises(report_actions.Conflict):
        db.execute(view["id"], "operator", result["head_hash"])


def test_denied_rejected_and_prohibited_path(db, tmp_path):
    outside = tmp_path / "roadmap.md"
    outside.write_text("authoritative")
    view = db.create(manifest())
    denied = db.execute(view["id"], "operator", view["head_hash"])
    assert denied["state"] == "denied" and not db.observed_file()["present"]
    view = db.create(manifest())
    view = db.decide(view["id"], "rejected", "No", "operator", view["head_hash"])
    assert db.execute(view["id"], "operator", view["head_hash"])["state"] == "denied"
    bad = manifest()
    bad["action"]["target"] = "../roadmap.md"
    with pytest.raises(ValueError):
        db.create(bad)
    assert outside.read_text() == "authoritative"


def test_record_specific_binding_and_parameter_change(db):
    first = approve(db, db.create(manifest()))
    second = db.create(manifest())
    copied = copy.deepcopy(second["record"])
    trust.append_entry(copied, "approval_granted", actor="operator",
                       payload=first["record"]["entries"][1]["payload"])
    with pytest.raises(ValueError):
        report_actions.inspect(copied)
    altered = copy.deepcopy(first["record"])
    altered["entries"][0]["payload"]["manifest"]["proposal"] += " changed"
    assert not report_actions.verify({"schema": report_actions.SCHEMA,
                                      "record": altered, "checkpoint": report_actions.checkpoint(first["record"])})


def test_concurrent_execute_one_real_write(db):
    view = approve(db, db.create(manifest()))
    def call(_):
        try:
            return report_actions.Store(db.db_path, db.output_dir).execute(view["id"], "operator", view["head_hash"])["state"]
        except report_actions.Conflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=6) as pool:
        states = list(pool.map(call, range(6)))
    assert states.count("written") == 1 and states.count("conflict") == 5
    assert db.get(view["id"])["attempts"] == 1


def test_restart_after_reserved_attempt_fails_closed(db, monkeypatch):
    view = approve(db, db.create(manifest()))
    def interrupted(_):
        raise OSError("simulated power loss before output")
    monkeypatch.setattr(db, "_write", interrupted)
    with pytest.raises(OSError):
        db.execute(view["id"], "operator", view["head_hash"])
    recovered = report_actions.Store(db.db_path, db.output_dir).get(view["id"])
    assert recovered["state"] == "in_doubt" and not recovered["can_execute"]
    with pytest.raises(report_actions.Conflict):
        db.execute(view["id"], "operator", recovered["head_hash"])
    assert not db.observed_file()["present"]


def test_route_enforces_exact_parameters_and_shows_disk_state(db, tmp_path, monkeypatch):
    monkeypatch.setattr(report_routes, "STORE", db)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    view = db.create(manifest())
    path = "/report-actions/" + view["id"]
    assert client.get(path).status_code == 200
    form = {"operator_token": decision_routes.TOKEN, "expected_head": view["head_hash"],
            "choice": "approved", "reason": "Reviewed"}
    assert client.post(path + "/decision", data=dict(form, target="../roadmap.md")).status_code == 422
    assert client.post(path + "/decision", data=form, follow_redirects=False).status_code == 303
    approved = db.get(view["id"])
    assert client.post(path + "/execute", data={"operator_token": decision_routes.TOKEN,
          "expected_head": approved["head_hash"], "proposal": "different"}).status_code == 422
    assert client.post(path + "/execute", data={"operator_token": decision_routes.TOKEN,
          "expected_head": approved["head_hash"]}, follow_redirects=False).status_code == 303
    page = client.get(path).text
    assert "Current file hash matches this proposal" in page
    assert report_actions.verify(client.get(path + "/export.json").json(),
                                 client.get(path + "/checkpoint.json").json())

def test_observer_uses_monitoring_record_schema(tmp_path):
    from tools.propose_openclaw_report import Observer
    from verity_core.trust import telemetry
    observer = Observer(tmp_path / "development-monitor.db", "test-model")
    observer.emit("heartbeat")
    with sqlite3.connect(observer.path) as con:
        record = json.loads(con.execute("SELECT record FROM monitor_runs WHERE id=?",
                                        (observer.run_id,)).fetchone()[0])
    view = telemetry.inspect(record, now=record["entries"][-1]["payload"]["received_at"])
    assert view["agent_id"] == "isolated-openclaw-report-agent"
    assert view["provider"] == "ollama_local"
    assert view["status"] == "Active"

def test_verified_remote_activity_import_has_explicit_provenance(tmp_path):
    from swarmtracker_node_ui import monitoring
    from verity_core.trust import telemetry
    manifest = {"run_id": "import-test", "agent_id": "agent-test", "name": "Remote OpenClaw",
                "runtime": "OpenClaw", "model": "test-model"}
    record = telemetry.create(manifest, 100)
    telemetry.append(record, {"event_id": "heartbeat-1", "kind": "heartbeat", "data": {}}, 101)
    anchor = telemetry.checkpoint(record)
    store = monitoring.MonitorStore(tmp_path / "monitor.db")
    store.import_verified(record, anchor, "M1 mini isolated collector")
    assert store.provenance("import-test")["source"] == "M1 mini isolated collector"
    assert store.recent(now=102)[0]["import_provenance"]["verified_head"] == anchor["head"]
    bad = dict(anchor, head="0" * 64)
    with pytest.raises(ValueError):
        store.import_verified(record, bad, "M1 mini isolated collector")
    store.import_verified(record, anchor, "M1 mini isolated collector")
    assert len(store.recent(now=102)) == 1

def test_report_route_requires_loopback_peer(db, monkeypatch):
    monkeypatch.setattr(report_routes, "STORE", db)
    remote = TestClient(ui.app, base_url="http://127.0.0.1",
                        client=("198.51.100.9", 12345))
    view = db.create(manifest())
    assert remote.get("/report-actions/" + view["id"]).status_code == 403
    assert remote.post("/report-actions/" + view["id"] + "/execute",
                       data={"operator_token": decision_routes.TOKEN,
                             "expected_head": view["head_hash"]}).status_code == 403
    assert db.get(view["id"])["attempts"] == 0

def test_openclaw_request_metadata_and_response_hash_binding(tmp_path, monkeypatch):
    from tools import propose_openclaw_report as task
    workspace = tmp_path / "agent"
    (workspace / "input").mkdir(parents=True)
    (workspace / "input/report.md").write_text("Sanitized pilot completed.")
    captured = {}
    model_text = json.dumps({"completed": ["Pilot completed"], "issues": [],
                             "priorities": ["Test approval"]})
    reply = {"id": "runtime-test-1", "response": model_text, "done": True,
             "model": "test-model", "provider": "ollama_local",
             "response_sha256": hashlib.sha256(model_text.encode()).hexdigest()}
    class Response:
        status = 200
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return json.dumps(reply).encode()
    class Opener:
        def open(self, request, timeout):
            captured.update(json.loads(request.data))
            return Response(request.full_url)
    monkeypatch.setattr(task.urllib.request, "build_opener", lambda *_: Opener())
    view = task.run(workspace, tmp_path / "state/record.db",
                    tmp_path / "protected", "test-model")
    assert captured["human_approval_required"] is True
    assert captured["tools_allowed"] == ["generate_text"]
    assert "shell" in captured["tools_denied"]
    assert view["manifest"]["response_sha256"] == reply["response_sha256"]
    assert view["manifest"]["activity_run_id"].startswith("dev-20261016-openclaw-")
    assert not (tmp_path / "protected/summary.md").exists()


def test_connected_pages_show_governed_action_and_integrity_failure(db, monkeypatch):
    monkeypatch.setattr(report_routes, "STORE", db)
    proposal = manifest("# Specific summary\n")
    proposal["activity_run_id"] = "observed-run-1"
    view = db.create(proposal)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    for path in ("/agents", "/governance"):
        page = client.get(path)
        assert page.status_code == 200
        assert view["id"] in page.text
        assert proposal["policy"]["id"] in page.text
        assert "observed-run-1" in page.text
        assert "Export linked history" in page.text
        assert "designated file write is governed" in page.text
    with sqlite3.connect(db.db_path) as con:
        con.execute("UPDATE report_actions SET record=? WHERE id=?", ("{}", view["id"]))
    for path in ("/agents", "/governance"):
        page = client.get(path)
        assert page.status_code == 200
        assert "history is unavailable or failed integrity checks" in page.text
        assert "No governed report proposals recorded here" not in page.text


def test_report_input_directory_symlink_refused_before_runtime(tmp_path):
    from tools.propose_openclaw_report import run
    outside = tmp_path / "original-reports"
    outside.mkdir()
    (outside / "report.md").write_text("Private original report")
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    (workspace / "input").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Invalid isolated workspace"):
        run(workspace, tmp_path / "state/records.db", tmp_path / "protected", "test-model")
    assert not (workspace / "staging").exists()
    assert (outside / "report.md").read_text() == "Private original report"
