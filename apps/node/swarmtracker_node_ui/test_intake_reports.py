"""HTTP intake-to-export tests. Synthetic specimens and injected providers only."""
import io
import json
import re
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from swarmtracker_node_ui import app as ui, governed_analysis as ga, keel_events, media_runner

class Cloud:
    provider = "vertex"
    project = "synthetic-test"
    location = "global"
    def __init__(self, failure=False):
        self.calls = 0
        self.failure = failure
    def generate(self, prompt):
        self.calls += 1
        assert "BOUNDED EVIDENCE DIGEST" in prompt
        return self.result()
    def generate_multimodal(self, prompt, parts):
        self.calls += 1
        assert parts
        return self.result()
    def result(self):
        if self.failure:
            raise RuntimeError("synthetic transport failure")
        return {"ok": True, "provider": "vertex", "model_used": "synthetic",
                "response": json.dumps({"risk_level": "low", "confidence": 10,
                    "verdict": "synthetic bounded finding", "signals": [],
                    "recommended_action": "corroborate locally"})}

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "STORE", None)
    ui.GENERIC_JOBS.clear()
    ui.MEDIA_JOBS.clear()
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", tmp_path/"audit.jsonl")
    monkeypatch.setattr(ui.oc, "healthz", lambda: {"ok": True})
    monkeypatch.setattr(ui.oc, "ollama_models", lambda: [])
    monkeypatch.setattr(ui.ledger, "latest_event_id", lambda: None)
    monkeypatch.setattr(ui.oc, "generate", lambda prompt, **kw:
        {"ok": True, "response": json.dumps({"risk_level": "low",
            "confidence": 10, "verdict": "local synthetic assessment",
            "signals": [], "recommended_action": "corroborate"}),
         "provider": "synthetic-local"})
    monkeypatch.setattr(ga.providers, "provider_config", lambda: {
        "ready": True, "provider": "vertex", "reason": "", "name": "Vertex AI",
        "models": [], "project": "synthetic-test", "region": "global"})
    return TestClient(ui.app)

def setup_cloud(monkeypatch, cloud):
    monkeypatch.setattr(ga.keel_policy, "multimodal_escalation_allowed",
                        lambda: (True, "synthetic allow"))
    monkeypatch.setattr(ga.providers, "build_cloud_router",
                        lambda selected=None: (cloud, ""))

def text_export(client):
    token = next(reversed(ui.GENERIC_JOBS))
    body = client.get(f"/report/{token}/json")
    assert body.status_code == 200
    return body.json(), client.get(f"/report/{token}/report").text

@pytest.mark.parametrize("selection,state,calls", [
    ("", "local_only", 0), ("false", "local_only", 0),
    ("true", "local_only", 0), ("1", "cloud_succeeded", 1)])
def test_text_intake_report_export(client, monkeypatch, selection, state, calls):
    cloud = Cloud()
    setup_cloud(monkeypatch, cloud)
    response = client.post("/analyze", data={
        "evidence": "Synthetic vendor requests changed bank details.",
        "uploaded_filename": "synthetic.eml", "escalate_multimodal": selection})
    assert response.status_code == 200
    assert "Cloud execution and audit" in response.text
    payload, md = text_export(client)
    ex = payload["execution"]
    assert ex["state"] == state and cloud.calls == calls
    assert ex["provider_attempted"] is bool(calls)
    assert state in md
    if calls:
        rows = [json.loads(x) for x in keel_events.MEDIA_LEDGER_PATH.read_text().splitlines()]
        auth = [x for x in rows if x["event"] == "cloud_escalation_authorized"]
        result = [x for x in rows if x["event"] == "cloud_escalation_result"]
        assert result[0]["detail"]["authorization_event_id"] == auth[0]["id"]
        assert ex["authorization_event_id"] == auth[0]["id"]

@pytest.mark.parametrize("mode,state,calls", [
    ("policy", "denied", 0), ("unavailable", "unavailable", 0),
    ("auth_audit", "audit_blocked", 0), ("transport", "cloud_failed", 1),
    ("result_audit", "cloud_succeeded", 1)])
def test_text_failure_paths_export(client, monkeypatch, tmp_path, mode, state, calls):
    cloud = Cloud(failure=mode == "transport")
    setup_cloud(monkeypatch, cloud)
    if mode == "policy":
        monkeypatch.setattr(ga.keel_policy, "multimodal_escalation_allowed",
                            lambda: (False, "synthetic policy refusal"))
    if mode == "unavailable":
        monkeypatch.setattr(ga.providers, "build_cloud_router",
                            lambda selected=None: (None, "synthetic unavailable"))
    if mode == "auth_audit":
        monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", tmp_path)
    if mode == "result_audit":
        def fail(*args, **kw):
            raise keel_events.LedgerWriteError("synthetic result write failure")
        monkeypatch.setattr(ga, "audit_result", fail)
    response = client.post("/analyze", data={"evidence": "synthetic specimen",
                                            "escalate_multimodal": "1"})
    assert response.status_code == 200 and state in response.text
    payload, md = text_export(client)
    ex = payload["execution"]
    assert ex["state"] == state and cloud.calls == calls
    assert ex["provider_attempted"] is bool(calls)
    if not calls:
        assert not payload["router_meta"].get("sent_artifacts")
        assert "AUTHORIZED but FAILED" not in md
    if mode == "result_audit":
        assert ex["result_audit"] == "failed"
        assert "failed" in response.text

@pytest.mark.parametrize("selection,failure,state,calls", [
    ("", False, "local_only", 0), ("1", False, "cloud_succeeded", 1),
    ("1", True, "cloud_failed", 1)])
def test_real_image_upload_to_media_export(client, monkeypatch, selection, failure, state, calls):
    from PIL import Image
    specimen = io.BytesIO()
    Image.new("RGB", (32, 32), (80, 120, 160)).save(specimen, format="PNG")
    cloud = Cloud(failure=failure)
    setup_cloud(monkeypatch, cloud)
    response = client.post("/media/upload", data={"escalate_multimodal": selection},
                           files={"media_file": ("synthetic.png", specimen.getvalue(), "image/png")})
    assert response.status_code == 200 and "Cloud execution and audit" in response.text
    token = next(reversed(ui.MEDIA_JOBS))
    payload = client.get(f"/media/report/{token}/json").json()
    md = client.get(f"/media/report/{token}/report").text
    assert payload["execution"]["state"] == state
    assert payload["execution"]["provider_attempted"] is bool(calls)
    assert cloud.calls == calls and state in md
    custody = payload["router_meta"]["custody"]
    assert bool(custody["provider_submission"]) is bool(calls)
    assert "not confirmed delivery" in custody["submission_semantics"]
    if not calls:
        assert payload["router_meta"]["media_examined"] is False

def test_local_runtime_failure_never_escalates(client, monkeypatch):
    cloud = Cloud()
    setup_cloud(monkeypatch, cloud)
    monkeypatch.setattr(ui.oc, "generate", lambda *a, **kw: {"ok": False, "error": "synthetic offline"})
    response = client.post("/analyze", data={"evidence": "synthetic", "escalate_multimodal": "1"})
    assert "synthetic offline" in response.text
    assert cloud.calls == 0 and not ui.GENERIC_JOBS

@pytest.mark.parametrize("mode,state,calls", [
    ("policy", "denied", 0), ("unavailable", "unavailable", 0),
    ("auth_audit", "audit_blocked", 0), ("result_audit", "cloud_succeeded", 1)])
def test_media_failure_paths(client, monkeypatch, tmp_path, mode, state, calls):
    from PIL import Image
    specimen = io.BytesIO()
    Image.new("RGB", (24, 24), (40, 80, 120)).save(specimen, format="PNG")
    cloud = Cloud()
    setup_cloud(monkeypatch, cloud)
    if mode == "policy":
        monkeypatch.setattr(ga.keel_policy, "multimodal_escalation_allowed",
                            lambda: (False, "synthetic refusal"))
    if mode == "unavailable":
        monkeypatch.setattr(ga.providers, "build_cloud_router",
                            lambda selected=None: (None, "synthetic unavailable"))
    if mode == "auth_audit":
        monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", tmp_path)
    if mode == "result_audit":
        original = keel_events.append_event_mandatory
        def fail(event, *args, **kw):
            if event == "media_escalation_result":
                raise keel_events.LedgerWriteError("synthetic result failure")
            return original(event, *args, **kw)
        monkeypatch.setattr(keel_events, "append_event_mandatory", fail)
    response = client.post("/media/upload", data={"escalate_multimodal": "1"},
        files={"media_file": ("synthetic.png", specimen.getvalue(), "image/png")})
    assert response.status_code == 200 and state in response.text
    token = next(reversed(ui.MEDIA_JOBS))
    payload = client.get(f"/media/report/{token}/json").json()
    assert payload["execution"]["state"] == state
    assert payload["execution"]["provider_attempted"] is bool(calls)
    assert cloud.calls == calls
    if mode == "result_audit":
        assert payload["execution"]["result_audit"] == "failed"
    if not calls:
        assert not payload["router_meta"]["sent_artifacts"]

def test_authorization_does_not_carry_to_next_job(client, monkeypatch):
    cloud = Cloud()
    setup_cloud(monkeypatch, cloud)
    for selection, expected in [("1", "cloud_succeeded"), ("", "local_only")]:
        response = client.post("/analyze", data={"evidence": "synthetic",
                        "escalate_multimodal": selection, "vertex_model": "synthetic"})
        assert response.status_code == 200
        payload, _ = text_export(client)
        assert payload["execution"]["state"] == expected
    assert cloud.calls == 1

@pytest.mark.parametrize("media", [False, True])
def test_denial_audit_failure_is_visible(client, monkeypatch, media):
    cloud = Cloud()
    setup_cloud(monkeypatch, cloud)
    monkeypatch.setattr(ga.keel_policy, "multimodal_escalation_allowed",
                        lambda: (False, "synthetic denial"))
    def fail(*args, **kwargs):
        raise keel_events.LedgerWriteError("synthetic decision audit failure")
    monkeypatch.setattr(keel_events, "append_event_mandatory", fail)
    if media:
        from PIL import Image
        specimen = io.BytesIO()
        Image.new("RGB", (16, 16)).save(specimen, format="PNG")
        response = client.post("/media/upload", data={"escalate_multimodal": "1"},
            files={"media_file": ("synthetic.png", specimen.getvalue(), "image/png")})
        token = next(reversed(ui.MEDIA_JOBS))
        payload = client.get(f"/media/report/{token}/json").json()
    else:
        response = client.post("/analyze", data={"evidence": "synthetic",
                                                "escalate_multimodal": "1"})
        payload, _ = text_export(client)
    assert response.status_code == 200 and cloud.calls == 0
    assert payload["execution"]["state"] == "denied"
    assert payload["execution"]["decision_audit"] == "failed"
    assert payload["execution"]["authorization_audit"] == "not_required"
    assert "Decision audit" in response.text and "failed" in response.text

@pytest.mark.parametrize("mode,state,calls", [
    ("setup", "unavailable", 0), ("malformed", "cloud_failed", 1)])
def test_setup_and_malformed_output(client, monkeypatch, mode, state, calls):
    cloud = Cloud()
    setup_cloud(monkeypatch, cloud)
    if mode == "setup":
        def fail(*args, **kw):
            raise RuntimeError("synthetic setup error")
        monkeypatch.setattr(ga.providers, "build_cloud_router", fail)
    else:
        cloud.result = lambda: ["invalid", "provider", "output"]
    response = client.post("/analyze", data={"evidence": "synthetic",
                                             "escalate_multimodal": "1"})
    payload, md = text_export(client)
    assert payload["execution"]["state"] == state and cloud.calls == calls
    assert response.status_code == 200
    if calls:
        assert "delivery is not independently confirmed" in md
        assert "HTTP None" not in md


@pytest.mark.parametrize("failure", ["issues", "validate_exception", "finalize_exception"])
def test_package_final_validation_withholds_all_report_exports(client, monkeypatch, failure):
    """A failed integrity gate must withhold both assessment and fused report."""
    from PIL import Image
    specimen = io.BytesIO()
    Image.new("RGB", (16, 16), (40, 80, 120)).save(specimen, format="PNG")
    package = media_runner.core_bridge.media_package
    def fail(*args, **kwargs):
        raise RuntimeError("SENSITIVE_INTERNAL_DIAGNOSTIC")
    if failure == "issues":
        monkeypatch.setattr(package, "validate", lambda pkg: ["synthetic custody issue"])
    else:
        monkeypatch.setattr(package, "validate" if failure == "validate_exception" else "finalize", fail)
    response = client.post("/media/upload", files={
        "media_file": ("synthetic.png", specimen.getvalue(), "image/png")})
    assert response.status_code == 200
    assert "Evidence-package" in response.text
    assert "SENSITIVE_INTERNAL_DIAGNOSTIC" not in response.text
    assert not ui.MEDIA_JOBS, "failed integrity gate exposed an exportable report"
