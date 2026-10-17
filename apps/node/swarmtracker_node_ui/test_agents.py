"""Participant attribution, isolation, and disconnected-state regressions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from swarmtracker_node_ui import agents, app as ui

@pytest.fixture(autouse=True)
def clear_observation():
    with agents._lock:
        agents._last.clear()
    yield
    with agents._lock:
        agents._last.clear()

HEALTH = {"status": "ok", "node": "macmini-m1",
          "default_model": "qwen2.5:7b-instruct", "provider": "ollama_local"}

def test_global_or_other_model_event_is_not_attributed():
    events = [{"node":"other", "model":HEALTH["default_model"], "id":"wrong-node"},
              {"node":HEALTH["node"], "model":"other-model", "id":"wrong-model"},
              {"node":HEALTH["node"], "model":HEALTH["default_model"], "id":"correct"}]
    result=agents.participant_snapshot(HEALTH,[HEALTH["default_model"]],events)
    assert result["latest_event"]["id"]=="correct"
    assert result["current_task"]=="Not observed"
    assert result["trust_record"]=="No verified record link available"

def test_disconnect_retains_last_actual_observation_not_current_time():
    agents.participant_snapshot(HEALTH,[],[],observed_at="first")
    result=agents.participant_snapshot({},[],[],observed_at="later")
    assert result["status"]=="Unavailable"
    assert result["last_seen"]=="first"
    assert result["checked_at"]=="later"
    assert result["node"]=="macmini-m1"

def test_unhealthy_payload_does_not_establish_seen_or_identity():
    result=agents.participant_snapshot(dict(HEALTH,status="error"),[],[],observed_at="now")
    assert not result["online"]
    assert result["node"]=="Not observed"
    assert result["last_seen"]=="Not observed in this app session"

def test_model_list_does_not_imply_loaded_model_or_running_task():
    result=agents.participant_snapshot(HEALTH,[HEALTH["default_model"]],[])
    assert result["model_available"]
    assert result["current_task"]=="Not observed"

def test_route_escapes_metadata_and_does_not_invent_record(monkeypatch):
    health=dict(HEALTH,node="<script>alert(1)</script>")
    monkeypatch.setattr(agents.oc,"healthz",lambda:health)
    monkeypatch.setattr(agents.oc,"ollama_models",lambda:[HEALTH["default_model"]])
    monkeypatch.setattr(agents.ledger,"read_raw_events",lambda limit:[])
    response=TestClient(ui.app).get("/agents")
    assert response.status_code==200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "self-reported" in response.text
    assert "No verified record link available" in response.text

def test_route_skips_non_object_ledger_entries(monkeypatch):
    monkeypatch.setattr(agents.oc,"healthz",lambda:HEALTH)
    monkeypatch.setattr(agents.oc,"ollama_models",lambda:[])
    monkeypatch.setattr(agents.ledger,"read_raw_events",lambda limit:[None, [], "bad"])
    assert TestClient(ui.app).get("/agents").status_code==200


def test_incomplete_online_health_does_not_mix_old_and_new_identity():
    agents.participant_snapshot(HEALTH,[],[],observed_at="first")
    result=agents.participant_snapshot({"status":"ok","node":"new-node"},[],[],observed_at="second")
    assert result["node"]=="new-node"
    assert result["model"]=="Not observed"
    assert result["latest_event"] is None
    assert result["last_seen"]=="first"
    offline=agents.participant_snapshot({},[],[],observed_at="third")
    assert offline["node"]=="macmini-m1"
    assert offline["identity_scope"]=="Last observed identity"

def test_offline_view_discloses_historical_matching(monkeypatch):
    agents.participant_snapshot(HEALTH,[],[])
    monkeypatch.setattr(agents.oc,"healthz",lambda:{})
    monkeypatch.setattr(agents.oc,"ollama_models",lambda:[])
    monkeypatch.setattr(agents.ledger,"read_raw_events",lambda limit:[])
    text=TestClient(ui.app).get("/agents").text
    assert "Last observed identity" in text
    assert "last observed self-reported node and model" in text


def test_offline_cached_model_does_not_claim_current_availability():
    agents.participant_snapshot(HEALTH,[],[])
    result=agents.participant_snapshot({},[HEALTH["default_model"]],[])
    assert not result["model_available"]


@pytest.mark.parametrize("health", [{"status": "ok"}, {"status": "ok", "node": "new"}, {"status": "ok", "default_model": "model"}])
def test_reachable_incomplete_identity_is_not_current(monkeypatch, health):
    agents.participant_snapshot(HEALTH, [], [], observed_at="first")
    result = agents.participant_snapshot(health, [], [], observed_at="second")
    assert result["online"]
    assert not result["identity_observed_now"]
    assert result["identity_scope"] == "Incomplete current identity"
    assert result["last_seen"] == "first"
    assert result["latest_event"] is None
    monkeypatch.setattr(agents.oc, "healthz", lambda: health)
    monkeypatch.setattr(agents.oc, "ollama_models", lambda: [])
    monkeypatch.setattr(agents.ledger, "read_raw_events", lambda limit: [])
    text = TestClient(ui.app).get("/agents").text
    assert "Incomplete current identity" in text
    assert "no complete current identity match is established" in text
    assert "Matches the current self-reported" not in text


def test_complete_current_identity_is_explicit():
    result = agents.participant_snapshot(HEALTH, [], [])
    assert result["identity_observed_now"]
    assert result["identity_scope"] == "Current reported identity"
