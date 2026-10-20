import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig
from apps.node.swarmtracker_node_ui.local_inference import LocalInference


def configured(tmp_path):
    c = CustomerConfig(tmp_path / "customer-config.db")
    c.save("resource", "report", dict(description="Disposable specimen", provenance="operator-entered", status="unknown"))
    c.save("connection", "localOpenClaw", dict(provider="openclaw", endpoint="http://127.0.0.1:8090",
        model="qwen2.5:7b-instruct", revision="test-revision", scope="local", credential_ref="none"))
    return c


def client_for(tmp_path, monkeypatch):
    from swarmtracker_node_ui import app as ui, store, agents
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "profile.db")
    monkeypatch.setattr(agents, "observe", lambda: {})
    return TestClient(ui.app, base_url="http://127.0.0.1")


def form(adapter="openclaw_v0", identifier="newOpenClaw"):
    from swarmtracker_node_ui import decision_routes
    return dict(operator_token=decision_routes.TOKEN, agent_id=identifier,
                adapter_kind=adapter, connection_id="localOpenClaw", resource_id="report",
                instructions="Summarize the specimen.", runtime_version="0.3 (operator declared)",
                service_identity="standardAgent")


def test_guided_registration_records_policy_and_real_adapter_identity(tmp_path, monkeypatch):
    c = configured(tmp_path)
    client = client_for(tmp_path, monkeypatch)
    assert "Add an agent" in client.get("/agents").text
    page = client.get("/agents/add")
    assert page.status_code == 200 and "does not install" in page.text
    response = client.post("/agents/add", data=form(), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/agents"
    cfg = c.effective("newOpenClaw", "report")
    assert cfg["adapter_kind"] == "openclaw_v0"
    assert cfg["identity_status"] == "unverified; operator-declared"
    assert cfg["service_identity"] == "standardAgent"
    assert cfg["daily_request_limit"] == 20 and cfg["allowed_tools"] == ["generate_text"]
    assert cfg["external_sharing"] == "none"
    assert cfg["require_fresh_connection_observation"] is True
    runs = LocalInference(tmp_path / "runs.db", c)
    denied = runs.run(agent_id="newOpenClaw", task_id="unchecked-task", resource_id="report",
        purpose="generate_text", prompt="Bounded specimen", dispatch=lambda *_: pytest.fail("Unchecked route dispatched"))
    assert denied["state"] == "denied"
    c.observe("connection", "localOpenClaw", "reachable", "Synthetic health fixture")
    result = runs.run(agent_id="newOpenClaw", task_id="registered-task", resource_id="report",
        purpose="generate_text", prompt="Bounded specimen",
        dispatch=lambda cfg, *_: dict(model=cfg["model"], response="Complete", id="fixture-event", decision="allowed", done=True))
    assert result["state"] == "completed"
    assert result["configuration"]["adapter_kind"] == "openclaw_v0"
    assert "newOpenClaw" in client.get("/agents").text
    assert client.post("/agents/add", data=form()).status_code == 422
    assert c.latest("agent", "newOpenClaw")["version"] == 1


def test_custom_agent_cannot_dispatch_or_lose_its_adapter_gate(tmp_path, monkeypatch):
    c = configured(tmp_path)
    client = client_for(tmp_path, monkeypatch)
    assert client.post("/agents/add", data=form("custom_unverified", "customAgent")).status_code == 200
    seen = []
    result = LocalInference(tmp_path / "runs.db", c).run(
        agent_id="customAgent", task_id="custom-denial", resource_id="report",
        purpose="generate_text", prompt="Bounded", dispatch=lambda *_: seen.append(True))
    assert result["state"] == "denied" and not seen
    assert "Registration only" in client.get("/agents").text
    body = c.latest("agent", "customAgent")["body"]
    without_adapter = {k:v for k,v in body.items() if k not in ("adapter_kind", "runtime_version", "service_identity")}
    with pytest.raises(ValueError, match="cannot be removed"):
        c.save("agent", "customAgent", without_adapter)
    with pytest.raises(ValueError, match="cannot be removed"):
        c.save("agent", "customAgent", dict(body, adapter_kind="openclaw_v0"))
    from swarmtracker_node_ui import decision_routes
    response = client.post("/configuration", data=dict(
        operator_token=decision_routes.TOKEN, kind="agent", id="customAgent",
        connection_id="localOpenClaw", instructions="New instructions",
        allowed_tools="generate_text", resources="report", external_sharing="none",
        fallback_id="", daily_request_limit="10", session_request_limit="0"), follow_redirects=False)
    assert response.status_code == 303
    assert c.latest("agent", "customAgent")["body"]["adapter_kind"] == "custom_unverified"


def test_registration_refuses_spoofed_identity_unknown_adapter_and_nonlocal_peer(tmp_path, monkeypatch):
    c = configured(tmp_path)
    client = client_for(tmp_path, monkeypatch)
    for data in (dict(form(), adapter_kind="google_adk"), dict(form(), identity_status="verified"),
                 dict(form(), service_identity="password=do-not-store")):
        assert client.post("/agents/add", data=data).status_code == 422
    assert c.latest("agent", "newOpenClaw") is None
    from swarmtracker_node_ui import app as ui
    client = TestClient(ui.app, base_url="http://127.0.0.1", client=("203.0.113.9",1234))
    assert client.get("/agents/add").status_code == 403
    assert client.post("/agents/add", data=form()).status_code == 403


def test_new_agent_id_is_atomic_across_independent_instances(tmp_path):
    c = configured(tmp_path)
    body = dict(connection_id="localOpenClaw", instructions="Bounded",
                allowed_tools=["generate_text"], resources=["report"], external_sharing="none",
                fallback_id="", adapter_kind="custom_unverified")
    def save(_):
        try:
            CustomerConfig(c.path).save("agent", "sameAgent", body, new_only=True)
            return "registered"
        except ValueError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, (1, 2))) == ["refused", "registered"]
    assert c.latest("agent", "sameAgent")["version"] == 1


def test_configuration_health_flag_defaults_preserves_and_rejects_invalid(tmp_path, monkeypatch):
    c = configured(tmp_path)
    client = client_for(tmp_path, monkeypatch)
    from swarmtracker_node_ui import decision_routes
    form = dict(operator_token=decision_routes.TOKEN, kind="agent", id="configAgent", connection_id="localOpenClaw",
                instructions="Synthetic text only", allowed_tools="generate_text", resources="report",
                external_sharing="none", fallback_id="", daily_request_limit="20", session_request_limit="0")
    assert client.post("/configuration", data=form, follow_redirects=False).status_code == 303
    assert c.latest("agent", "configAgent")["body"]["require_fresh_connection_observation"] is True
    assert client.post("/configuration", data=dict(form, require_fresh_connection_observation="false"), follow_redirects=False).status_code == 303
    assert c.latest("agent", "configAgent")["body"]["require_fresh_connection_observation"] is False
    assert client.post("/configuration", data=form, follow_redirects=False).status_code == 303
    assert c.latest("agent", "configAgent")["body"]["require_fresh_connection_observation"] is False
    version = c.latest("agent", "configAgent")["version"]
    assert client.post("/configuration", data=dict(form, require_fresh_connection_observation="maybe")).status_code == 422
    assert c.latest("agent", "configAgent")["version"] == version
    legacy = dict(c.latest("agent", "configAgent")["body"])
    legacy.pop("require_fresh_connection_observation")
    c.save("agent", "legacyAgent", legacy)
    assert client.post("/configuration", data=dict(form, id="legacyAgent"), follow_redirects=False).status_code == 303
    assert "require_fresh_connection_observation" not in c.latest("agent", "legacyAgent")["body"]
