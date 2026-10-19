import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig
from apps.node.swarmtracker_node_ui.local_inference import LocalInference


def setup(tmp_path):
    config = CustomerConfig(tmp_path / "customer-config.db")
    config.save("resource", "report", dict(description="Disposable report", provenance="operator-entered", status="unknown"))
    config.save("connection", "openclaw", dict(provider="openclaw", endpoint="http://127.0.0.1:8090",
        model="qwen2.5:7b-instruct", revision="local-pilot", scope="local", credential_ref="none"))
    config.save("agent", "existingOpenClaw", dict(connection_id="openclaw", instructions="Summarize the specimen.",
        allowed_tools=["generate_text"], resources=["report"], external_sharing="none", fallback_id=""))
    return config, LocalInference(tmp_path / "runs.db", config)


def test_allowed_and_denied_routes_are_durable_and_do_not_redispatch(tmp_path):
    config, runs = setup(tmp_path)
    seen = []
    def dispatch(cfg, prompt, purpose):
        seen.append((cfg["model"], prompt, purpose))
        return dict(model=cfg["model"], response="A bounded result.", event_id="evt-1",
                    decision="allow", provider="ollama_local", done=True, done_reason="stop")
    base = dict(agent_id="existingOpenClaw", resource_id="report", purpose="generate_text",
                prompt="Summarize this.", requested_model="")
    allowed = runs.run(task_id="task-1", dispatch=dispatch, **base)
    assert allowed["state"] == "completed"
    assert allowed["configuration"]["agent_version"] == 1
    assert allowed["result"]["runtime_event_id"] == "evt-1"
    assert allowed["result"]["response"] == "A bounded result."
    wrong = runs.run(task_id="task-2", dispatch=dispatch, **dict(base, requested_model="other"))
    purpose = runs.run(task_id="task-3", dispatch=dispatch, **dict(base, purpose="draft_text"))
    assert wrong["state"] == purpose["state"] == "denied"
    assert len(seen) == 1
    with pytest.raises(sqlite3.IntegrityError):
        runs.run(task_id="task-1", dispatch=dispatch, **base)
    assert len(seen) == 1
    assert [x["state"] for x in LocalInference(tmp_path / "runs.db", config).recent()] == [
        "denied", "denied", "completed"]
    assert "Summarize this." not in json.dumps(runs.recent())


def test_runtime_model_mismatch_is_failed_and_version_history_survives(tmp_path):
    config, runs = setup(tmp_path)
    base = dict(agent_id="existingOpenClaw", resource_id="report", purpose="generate_text",
                prompt="Bounded request", requested_model="")
    failed = runs.run(task_id="task-1", dispatch=lambda *_: dict(model="wrong", response="text"), **base)
    assert failed["state"] == "failed" and failed["result"] is None
    agent = config.latest("agent", "existingOpenClaw")["body"]
    config.save("agent", "existingOpenClaw", dict(agent, instructions="New instruction"))
    assert runs.recent()[0]["configuration"]["agent_version"] == 1
    newer = runs.run(task_id="task-2", dispatch=lambda c, *_: dict(
        model=c["model"], response="Complete", id="evt-2", decision="allowed",
        done=True, done_reason="stop"), **base)
    assert newer["configuration"]["agent_version"] == 2
    assert newer["state"] == "completed"
    runtime_denial = runs.run(task_id="task-3", dispatch=lambda c, *_: dict(
        model=c["model"], response="Refused", id="evt-3", decision="denied",
        done=True), **base)
    assert runtime_denial["state"] == "failed" and runtime_denial["result"] is None
    missing_done = runs.run(task_id="task-4", dispatch=lambda c, *_: dict(
        model=c["model"], response="Partial", id="evt-4", decision="allowed"), **base)
    assert missing_done["state"] == "failed" and missing_done["result"] is None
    omitted_reason = runs.run(task_id="omitted-finish", dispatch=lambda c, *_: dict(
        model=c["model"], response="Complete", id="evt-omitted", decision="allowed",
        done=True), **base)
    assert omitted_reason["state"] == "completed" and omitted_reason["result"]["finish_reason"] is None
    for reason in ("length", "cancelled", "error"):
        incomplete = runs.run(task_id="bad-finish-" + str(reason), dispatch=lambda c, *_: dict(
            model=c["model"], response="Looks complete", id="evt-bad", decision="allowed",
            done=True, done_reason=reason), **base)
        assert incomplete["state"] == "failed" and incomplete["result"] is None


def test_native_endpoint_records_denial_without_runtime(tmp_path, monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "profile.db")
    setup(tmp_path)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    form = dict(operator_token=decision_routes.TOKEN, agent_id="existingOpenClaw",
                task_id="denied-ui", resource_id="report", purpose="draft_text",
                prompt="Please draft", requested_model="")
    response = client.post("/configuration/inference", data=form)
    assert response.status_code == 200
    assert response.json()["state"] == "denied"
    assert "denied-ui" in client.get("/configuration").text
