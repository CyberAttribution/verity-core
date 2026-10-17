import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.node.swarmtracker_node_ui.sessions import SessionStore, derive, validate_event


IDENTITY = {"id": "lambda-20261017", "purpose": "Sessions pilot", "provider": "Lambda", "macs": ["M1", "M2"], "branch": "glm/real-openclaw-action-v0.1", "commits": ["68d66dc"]}


def event(eid, kind, source, data, at=None):
    return {"id": eid, "kind": kind, "source": source, "at": at or time.time(), "data": data}


def test_append_history_restart_and_conflicting_id():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sessions.db"
        first = SessionStore(path)
        first.create(IDENTITY)
        observation = event("p1", "provider", "provider_console", {"state": "running", "instance_id": "i-123"})
        assert first.append(IDENTITY["id"], observation)
        assert not SessionStore(path).append(IDENTITY["id"], observation)
        with pytest.raises(ValueError, match="Conflicting"):
            first.append(IDENTITY["id"], {**observation, "data": {"state": "terminated"}})
        first.append(IDENTITY["id"], event("p2", "provider", "provider_console", {"state": "stopping"}))
        record = SessionStore(path).get(IDENTITY["id"])
        assert [e["id"] for e in record["events"]] == ["p1", "p2"]
        assert derive(record)["provider"] == "stopping"
        assert derive(record)["provider_details"]["instance_id"] == "i-123"


def test_empty_billing_blocked_ssh_download_local_work_and_stale():
    t = time.time()
    events = [event("p", "provider", "provider_console", {"state": "running"}, t - 40),
              event("b", "billing", "provider_console", {"rate": 22.32, "currency": "USD", "billing_start": t - 3600, "ceiling": 110}, t - 40),
              event("c", "connection", "ssh", {"state": "blocked"}, t - 30),
              event("l", "task", "operator", {"task_id": "mac", "title": "Local suite", "scope": "local", "state": "running"}, t - 25)]
    record = {"identity": IDENTITY, "events": [dict(e, received=t - 20) for e in events]}
    view = derive(record, now=t)
    assert view["model"] == "unknown" and view["activity"] != "active"
    assert view["estimated_charge"] == pytest.approx(22.32, abs=.01)
    assert view["estimate_basis"] == "provider billing start"
    assert view["reported_charge"] is None
    assert any("no recent GPU" in a for a in view["alerts"])
    assert any("Prolonged paid idle" in a for a in view["alerts"])
    record["events"].append(dict(event("inv", "inventory", "ssh", {"checked": True, "models": []}, t), received=t))
    assert derive(record, now=t)["model"] == "absent"
    record["events"].append(dict(event("dl", "model", "model_server", {"state": "downloading", "download_bytes": 200}, t), received=t + .1))
    assert derive(record, now=t + .2)["model"] == "downloading"
    record["events"].append(dict(event("load", "model", "model_server", {"state": "loading"}, t), received=t + .2))
    assert derive(record, now=t + .3)["model"] == "loading"
    assert derive(record, now=t + 600)["model"] == "unknown"
    assert derive(record, now=t + 600)["stale"]
    assert derive(record, now=t + 600)["provider"] == "unknown"
    record["events"].append(dict(event("sshblocked", "connection", "ssh", {"state": "blocked"}, t), received=t + 1))
    assert derive(record, now=t + 2)["model"] == "unknown"


def test_request_verification_and_termination_with_retained_resource():
    t = time.time()
    entries = [event("p", "provider", "provider_api", {"state": "running"}, t - 30),
               event("b", "billing", "provider_console", {"rate": 22.32, "currency": "USD", "billing_start": t - 3600, "ceiling": 110}, t - 30),
               event("task", "task", "operator", {"task_id": "review", "title": "Review", "scope": "gpu", "state": "running"}, t - 20),
               event("req", "request", "model_server", {"task_id": "review", "state": "running", "request_id": "r-1"}, t - 10)]
    record = {"identity": IDENTITY, "events": [dict(e, received=e["at"]) for e in entries]}
    assert derive(record, now=t)["activity"] == "active"
    record["events"].append(dict(event("other", "task", "operator", {"task_id": "other", "title": "Other", "scope": "gpu", "state": "running"}, t), received=t))
    record["events"].append(dict(event("old-done", "task", "operator", {"task_id": "review", "title": "Review", "scope": "gpu", "state": "completed"}, t), received=t))
    assert derive(record, now=t)["activity"] != "active"
    record["events"].append(dict(event("again", "task", "operator", {"task_id": "review", "title": "Review", "scope": "gpu", "state": "running"}, t), received=t))
    record["events"].append(dict(event("truncated", "result", "model_server", {"task_id": "review", "state": "completed", "finish_reason": "length"}, t), received=t))
    assert derive(record, now=t)["tasks"][0]["state"] == "incomplete output"
    assert derive(record, now=t)["activity"] != "active"
    record["events"].extend([dict(event("done", "task", "operator", {"task_id": "review", "title": "Review", "scope": "gpu", "state": "completed", "output": "review.json", "verification": "tests passed"}, t), received=t),
                             dict(event("complete", "result", "model_server", {"task_id": "review", "state": "completed", "finish_reason": "stop"}, t), received=t),
                             dict(event("stop", "shutdown", "operator", {"requested": True}, t), received=t)])
    assert derive(record, now=t)["shutdown_requested"] and not derive(record, now=t)["termination_confirmed"]
    record["events"].extend([dict(event("confirm", "shutdown", "provider_console", {"confirmed": True}, t + 15), received=t + 15),
                             dict(event("volume", "resource", "provider_console", {"name": "volume", "state": "attached", "billable": True}, t + 16), received=t + 16)])
    view = derive(record, now=t + 3600)
    assert view["termination_confirmed"] and view["provider"] == "terminated"
    assert view["estimated_charge"] == pytest.approx(22.41, abs=.01)
    assert view["retained_resources"][0]["name"] == "volume"
    with pytest.raises(ValueError, match="provider confirmation"):
        validate_event(event("bad", "shutdown", "operator", {"confirmed": True}))


def test_read_only_sessions_navigation():
    import sys
    sys.path.insert(0, "apps/node")
    from swarmtracker_node_ui.app import app
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get("/sessions")
    assert response.status_code == 200
    assert "Read-only records" in response.text
    assert 'href="/sessions"' in response.text
    assert client.post("/sessions").status_code == 405


def test_collector_crash_restart_dedupes_and_alerts(tmp_path):
    from tools.session_collector import collect_once
    db = SessionStore(tmp_path / "session.db")
    db.create(IDENTITY)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    import json
    payload = {"session_id": IDENTITY["id"], "event": event("p", "provider", "provider_console", {"state": "running"})}
    pending = inbox / "provider.json"
    pending.write_text(json.dumps(payload))
    notices = collect_once(inbox, db)
    assert (inbox / "provider.seen").exists()
    assert any("no recent GPU" in n for n in notices)
    # Simulate commit followed by a crash before inbox rename.
    pending.write_text(json.dumps(payload))
    collect_once(inbox, SessionStore(tmp_path / "session.db"))
    assert len(db.get(IDENTITY["id"])["events"]) == 1


@pytest.mark.parametrize("kind,data", [
    ("task", {"state": "running", "scope": "gpu"}),
    ("request", {"state": "running"}),
    ("result", {"task_id": "x", "state": "completed"}),
    ("inventory", {"checked": "no", "models": []}),
    ("resource", {"billable": True}),
    ("billing", {"billing_start": -1}),
])
def test_invalid_observations_fail_before_storage(kind, data):
    with pytest.raises(ValueError):
        validate_event(event("bad", kind, "operator", data))


def test_guest_boot_proxy_is_not_labeled_provider_billing_start():
    now = time.time()
    record = {"identity": IDENTITY, "events": [dict(event("estimate", "billing", "ssh", {"rate": 22.32, "currency": "USD", "estimate_start_proxy": now - 1800}), received=now)]}
    view = derive(record, now=now)
    assert view["estimate_basis"] == "guest boot proxy"
    assert view["billing"].get("billing_start") is None
    assert view["estimated_charge"] == pytest.approx(11.16, abs=.01)


def test_outcome_chain_requires_completed_result_and_separate_review(tmp_path):
    db = SessionStore(tmp_path / "outcomes.db")
    with pytest.raises(ValueError, match="separate outcome"):
        validate_event(event("health-claimed-verified", "task", "model_server", {"task_id": "r1", "title": "Review", "scope": "gpu", "state": "verified"}))
    db.create(IDENTITY)
    sid = IDENTITY["id"]
    db.append(sid, event("task", "task", "operator", {"task_id": "r1", "title": "Review", "scope": "gpu", "state": "ready"}))
    for index, state in enumerate(("proposed", "queued", "submitted", "acknowledged")):
        db.append(sid, event(f"o{index}", "outcome", "operator", {"task_id": "r1", "state": state}))
    with pytest.raises(ValueError, match="matching complete result"):
        db.append(sid, event("premature", "outcome", "operator", {"task_id": "r1", "state": "completed", "result_id": "result"}))
    db.append(sid, event("result", "result", "model_server", {"task_id": "r1", "state": "completed", "finish_reason": "length"}))
    with pytest.raises(ValueError, match="matching complete result"):
        db.append(sid, event("truncated", "outcome", "operator", {"task_id": "r1", "state": "completed", "result_id": "result"}))
    db.append(sid, event("result-stop", "result", "model_server", {"task_id": "r1", "state": "completed", "finish_reason": "stop"}))
    db.append(sid, event("complete", "outcome", "operator", {"task_id": "r1", "state": "completed", "result_id": "result-stop"}))
    assert derive(db.get(sid))["tasks"][0]["outcome"] == "completed"
    with pytest.raises(ValueError, match="reviewer and evidence"):
        db.append(sid, event("model-verified", "outcome", "model_server", {"task_id": "r1", "state": "independently_verified", "result_id": "result-stop", "verifier": "model", "evidence": "none"}))
    reviewed = event("accepted", "outcome", "test", {"task_id": "r1", "state": "independently_verified", "result_id": "result-stop", "verifier": "independent source check", "evidence": "tests/test_sessions.py"})
    assert db.append(sid, reviewed)
    assert not SessionStore(tmp_path / "outcomes.db").append(sid, reviewed)
    assert derive(db.get(sid))["tasks"][0]["outcome"] == "independently_verified"
    assert derive(db.get(sid))["tasks"][0]["outcome_event_id"] == "accepted"
    assert derive(db.get(sid))["tasks"][0]["outcome_result_id"] == "result-stop"
