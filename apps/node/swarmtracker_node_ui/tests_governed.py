"""
v0.5.0 tests — universal governed investigations, bounded selection,
export parity, and sensitive-media PREPARATION guards (workflow inactive).

Run: python -m pytest swarmtracker_node_ui/tests_governed.py -q
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_T = tempfile.mkdtemp()
os.environ.setdefault("NODE_UI_DATA_DIR", _T)
os.environ.setdefault("NODE_UI_DB", os.path.join(_T, "t.db"))
os.environ.setdefault("NODE_UI_MEDIA_LEDGER", os.path.join(_T, "k.jsonl"))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "swarmtracker-main")

from swarmtracker_node_ui import (governed_analysis, keel_policy,  # noqa
                                  providers)
from swarmtracker_node_ui import core_bridge  # noqa

_ART = json.dumps({"risk_level": "high", "confidence": 70, "verdict": "v",
                   "signals": ["s"], "recommended_action": "verify"})
_FUS = json.dumps({"fused_verdict": "vendor fraud", "overall_risk": "high",
                   "overall_confidence": 72,
                   "attribution_hypotheses": []})


class _VOK:
    provider = "vertex"
    project = "swarmtracker-main"
    location = "global"
    models = ["gemini-3.6-flash"]

    def generate(self, p):
        return {"ok": True, "provider": "vertex",
                "response": "Bounded cloud assessment.",
                "model_used": "gemini-3.6-flash", "latency_ms": 640,
                "project": "swarmtracker-main", "region": "global",
                "attempted_models": ["gemini-3.6-flash"],
                "attempts": [{"model": "gemini-3.6-flash", "status": 200,
                              "latency_ms": 640}]}


class _VFAIL:
    provider = "vertex"
    project = "swarmtracker-main"
    location = "global"
    models = ["gemini-3.6-flash"]

    def generate(self, p):
        return {"ok": False, "provider": "vertex", "response": "",
                "error": "HTTP 403", "provider_status": 403,
                "attempted_models": ["gemini-3.6-flash"],
                "attempts": [{"model": "gemini-3.6-flash",
                              "status": 403}]}


def _client(monkeypatch, cloud=None):
    monkeypatch.setattr(core_bridge.model_router, "adc_available",
                        lambda: True)
    if cloud is not None:
        monkeypatch.setattr(providers, "build_cloud_router",
                            lambda sel=None: (cloud, ""))
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as app_mod
    app_mod.oc.generate = lambda p, **kw: {
        "ok": True, "response": _FUS if "fusion engine" in p else _ART,
        "openclaw_event_id": "e"}
    return TestClient(app_mod.app), app_mod


def _vis(h):
    return re.sub(r"<script.*?</script>", "", h, flags=re.S)


ITEMS = {
    "text": [{"label": "Note", "text": "suspicious wire to apex-billing.top",
              "evidence_type": "freeform_text"}],
    "email": [{"label": "Hdr", "text": "From: ceo@apex-billing.top\nReply-To: x@evil.top",
               "evidence_type": "email_headers"}],
    "logs": [{"label": "DNS", "text": "query: apex-billing.top IN A 198.51.100.7",
              "evidence_type": "dns_log"}],
    "json": [{"label": "EDR", "text": '{"host":"h1","proc":"x.exe","c2":"apex-billing.top"}',
              "evidence_type": "cloud_saas_audit_event"}],
}


# ---------- bounded evidence selection ----------

def test_bounded_text_payload_manifest():
    big = [{"label": "L", "text": "x" * 20000, "evidence_type": "dns_log"}]
    digest, manifest = governed_analysis.bounded_text_payload(
        big, max_chars=6000)
    assert len(manifest) == 1
    m = manifest[0]
    assert m["kind"] == "text_excerpt" and len(m["sha256"]) == 64
    assert m["size_bytes"] <= 6000 and "bounded" in m["reason"]
    assert "x" * 6000 in digest and "x" * 6001 not in digest


# ---------- universal governed authorization ----------

@pytest.mark.parametrize("kind", ["text", "email", "logs", "json"])
def test_local_mode_makes_no_cloud_call(monkeypatch, kind):
    calls = []
    monkeypatch.setattr(providers, "build_cloud_router",
                        lambda sel=None: (calls.append(1), (None, "x"))[1])
    c, _ = _client(monkeypatch)
    r = _vis(c.post("/analyze/fused",
                    data={"artifacts_json": json.dumps(ITEMS[kind])}).text)
    assert ">Local Only<" in r and ">No<" in r
    assert calls == []                       # never built a cloud router


@pytest.mark.parametrize("kind", ["text", "email", "logs", "json"])
def test_governed_authorization_reaches_cloud(monkeypatch, kind):
    c, _ = _client(monkeypatch, cloud=_VOK())
    r = _vis(c.post("/analyze/fused",
                    data={"artifacts_json": json.dumps(ITEMS[kind]),
                          "escalate_multimodal": "1"}).text)
    assert ">Governed Hybrid<" in r
    assert "Authorized — Vertex AI" in r and "gemini-3.6-flash" in r


def test_per_job_authorization_not_persisted(monkeypatch):
    c, _ = _client(monkeypatch, cloud=_VOK())
    r1 = _vis(c.post("/analyze/fused",
                     data={"artifacts_json": json.dumps(ITEMS["logs"]),
                           "escalate_multimodal": "1"}).text)
    assert ">Governed Hybrid<" in r1
    # next job WITHOUT the flag must be Local Only (no sticky auth)
    r2 = _vis(c.post("/analyze/fused",
                     data={"artifacts_json": json.dumps(ITEMS["logs"])}).text)
    assert ">Local Only<" in r2 and "Governed Hybrid" not in r2


def test_governed_truthful_fallback(monkeypatch):
    c, _ = _client(monkeypatch, cloud=_VFAIL())
    r = _vis(c.post("/analyze/fused",
                    data={"artifacts_json": json.dumps(ITEMS["logs"]),
                          "escalate_multimodal": "1"}).text)
    assert ">Governed Hybrid<" in r and "Failed" in r
    assert "did" in r.lower() and "not" in r.lower()   # did not examine
    assert "local findings" in r.lower()


# ---------- export parity ----------

@pytest.mark.parametrize("kind", ["text", "email", "logs", "json"])
def test_export_parity_generic(monkeypatch, kind):
    c, _ = _client(monkeypatch, cloud=_VOK())
    r = c.post("/analyze/fused",
               data={"artifacts_json": json.dumps(ITEMS[kind])}).text
    tok = re.search(r'/report/([^/]+)/json', r).group(1)
    j = c.get(f"/report/{tok}/json")
    assert j.status_code == 200 and json.loads(j.text)["report"]
    s = c.get(f"/report/{tok}/stix")
    assert s.status_code == 200
    bundle = json.loads(s.text)
    assert bundle["type"] == "bundle"
    rep = c.get(f"/report/{tok}/report")
    assert rep.status_code == 200 and "Governed Investigation" in rep.text


def test_export_no_embedded_media_and_no_false_cloud_claim(monkeypatch):
    c, _ = _client(monkeypatch, cloud=_VFAIL())
    r = c.post("/analyze/fused",
               data={"artifacts_json": json.dumps(ITEMS["logs"]),
                     "escalate_multimodal": "1"}).text
    tok = re.search(r'/report/([^/]+)/json', r).group(1)
    j = c.get(f"/report/{tok}/json").text
    # provider failed -> export must not imply it examined anything
    assert '"escalation_ok": false' in j.lower() or \
        '"escalation_ok":false' in j.lower()
    # no base64 image payload embedded
    assert "inline_data" not in j and "data:image" not in j


# ---------- sensitive-media PREPARATION (inactive) ----------

def test_reserved_workflow_cannot_be_invoked(monkeypatch):
    with pytest.raises(governed_analysis.ReservedWorkflowError):
        governed_analysis.guard_workflow(
            governed_analysis.RESERVED_SENSITIVE_WORKFLOW)
    c, _ = _client(monkeypatch)
    r = c.post("/analyze/fused",
               data={"artifacts_json": json.dumps(ITEMS["logs"]),
                     "workflow": "intimate_image_abuse_assessment"})
    assert "reserved" in r.text.lower()


def test_sensitive_publication_disabled_by_default():
    assert keel_policy.sensitive_media_publication_allowed() is False
    sm = keel_policy.sensitive_media_policy()
    assert sm["enabled"] is False
    assert sm["publication_disabled"] is True
    assert sm["exports_exclude_embedded_media"] is True


def test_safety_stop_state_exists_and_reserved_fields():
    assert governed_analysis.SAFETY_STOP_STATE == "safety_stopped"
    assert "safety_stopped" in governed_analysis.EXECUTION_STATES
    f = governed_analysis.reserved_sensitive_fields()
    assert f["identity_not_established"] and f["consent_not_established"]
    assert f["age_not_reliably_established"]
    assert f["restricted_handling_required"]


def test_reserved_refusal_is_audited(monkeypatch):
    from swarmtracker_node_ui import keel_events
    n0 = keel_events.count_events()
    try:
        governed_analysis.guard_workflow(
            governed_analysis.RESERVED_SENSITIVE_WORKFLOW)
    except governed_analysis.ReservedWorkflowError:
        pass
    evs = [e["event"] for e in keel_events.read_events(50)]
    assert "reserved_workflow_refused" in evs
    assert keel_events.count_events() > n0


def test_no_sensitive_route_or_nav(monkeypatch):
    c, _ = _client(monkeypatch)
    assert c.get("/sensitive").status_code == 404
    assert c.post("/sensitive").status_code in (404, 405)
    # no nav/link/label mentioning the feature
    for path in ("/", "/analyze", "/media"):
        body = c.get(path).text.lower()
        assert "intimate" not in body
        assert "porn" not in body


# ---------- single-artifact Analyze parity (shared service) ----------

def _single(c, evidence="click http://paypa1-verify.top now",
            escalate=False):
    data = {"evidence": evidence, "evidence_type": "url_domain",
            "profile_mode": "smb"}
    if escalate:
        data["escalate_multimodal"] = "1"
    r = c.post("/analyze", data=data)
    app_mod = sys.modules["swarmtracker_node_ui.app"]
    return r.text


def test_single_analyze_local_and_exports(monkeypatch):
    c, app_mod = _client(monkeypatch)
    app_mod.oc.generate = lambda p, **kw: {
        "ok": True, "openclaw_event_id": "evt-x",
        "response": json.dumps({"risk_level": "high", "confidence": 66,
                                "verdict": "phishing likely",
                                "signals": ["lookalike"],
                                "recommended_action": "verify"})}
    body = _vis(_single(c))
    assert ">Local Only<" in body
    for a in ("Export JSON", "Export Report", "Export to Atlas",
              "Generate Briefing"):
        assert a in body
    tok = re.search(r'/report/([^/]+)/json', body).group(1)
    jd = json.loads(c.get(f"/report/{tok}/json").text)
    assert any(e["type"] == "domain" for e in jd["report"]["entities"])


def test_single_analyze_governed_and_no_cloud_when_local(monkeypatch):
    c, app_mod = _client(monkeypatch, cloud=_VOK())
    app_mod.oc.generate = lambda p, **kw: {
        "ok": True, "openclaw_event_id": "e",
        "response": json.dumps({"risk_level": "high", "confidence": 60,
                                "verdict": "v", "signals": [],
                                "recommended_action": "r"})}
    # governed
    body = _vis(_single(c, escalate=True))
    assert ">Governed Hybrid<" in body and "gemini-3.6-flash" in body
    # local (no escalate) -> no cloud
    calls = []
    monkeypatch.setattr(providers, "build_cloud_router",
                        lambda sel=None: (calls.append(1), (None, "x"))[1])
    body2 = _vis(_single(c))
    assert ">Local Only<" in body2 and calls == []


# ---------- Atlas export + Executive briefing (bounded outputs) ----------

def _run_fused(c, kind="logs", escalate=False):
    data = {"artifacts_json": json.dumps(ITEMS[kind])}
    if escalate:
        data["escalate_multimodal"] = "1"
    r = c.post("/analyze/fused", data=data).text
    return re.search(r'/report/([^/]+)/json', r).group(1)


def test_atlas_export_local_only(monkeypatch):
    c, _ = _client(monkeypatch)
    tok = _run_fused(c, "logs")
    r = c.get(f"/report/{tok}/atlas")
    assert r.status_code == 200
    b = json.loads(r.text)
    assert b["ok"] and b["schema"] == "atlas_export/v1"
    assert b["examination_status"] == "local_only"
    # case node + the domain observable present; hashes/provenance kept
    types = {n["type"] for n in b["nodes"]}
    assert "canonical_case" in types
    assert any("apex-billing.top" in n["label"] for n in b["nodes"])
    # every edge references real nodes (no invented relationships)
    ids = {n["id"] for n in b["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids
               for e in b["edges"])


def test_atlas_export_governed_records_provider(monkeypatch):
    c, _ = _client(monkeypatch, cloud=_VOK())
    tok = _run_fused(c, "logs", escalate=True)
    b = json.loads(c.get(f"/report/{tok}/atlas").text)
    assert b["examination_status"] == "examined_by_provider"
    assert b["provider"] == "vertex" and b["model"] == "gemini-3.6-flash"


def test_atlas_export_idempotent(monkeypatch):
    c, _ = _client(monkeypatch)
    tok = _run_fused(c, "logs")
    b1 = json.loads(c.get(f"/report/{tok}/atlas").text)
    b2 = json.loads(c.get(f"/report/{tok}/atlas").text)
    ids1 = sorted(n["id"] for n in b1["nodes"])
    ids2 = sorted(n["id"] for n in b2["nodes"])
    assert ids1 == ids2                      # deterministic -> idempotent
    assert b1["source_case_id"] == b2["source_case_id"]


def test_atlas_export_audited(monkeypatch):
    from swarmtracker_node_ui import keel_events
    c, _ = _client(monkeypatch)
    tok = _run_fused(c, "logs")
    c.get(f"/report/{tok}/atlas")
    assert "atlas_export" in [e["event"] for e in
                              keel_events.read_events(50)]


def test_briefing_local_only(monkeypatch):
    c, _ = _client(monkeypatch)
    tok = _run_fused(c, "logs")
    md = c.get(f"/report/{tok}/briefing.md")
    assert md.status_code == 200
    assert "Executive Briefing" in md.text
    assert "Local Only — no cloud provider was used." in md.text
    h = c.get(f"/report/{tok}/briefing.html")
    assert h.status_code == 200 and "window.print()" in h.text


def test_briefing_cloud_success_discloses_provider(monkeypatch):
    c, _ = _client(monkeypatch, cloud=_VOK())
    tok = _run_fused(c, "logs", escalate=True)
    md = c.get(f"/report/{tok}/briefing.md").text
    assert "gemini-3.6-flash" in md and "Cloud-assisted (authorized)" in md
    assert "Local fusion remained authoritative" in md


def test_briefing_cloud_failure_not_concealed(monkeypatch):
    c, _ = _client(monkeypatch, cloud=_VFAIL())
    tok = _run_fused(c, "logs", escalate=True)
    md = c.get(f"/report/{tok}/briefing.md").text
    assert "FAILED" in md and "did NOT examine" in md
    # must NOT imply the provider examined the evidence
    assert "examined bounded excerpts" not in md


def test_briefing_no_unsupported_claims(monkeypatch):
    c, _ = _client(monkeypatch)
    tok = _run_fused(c, "logs")
    md = c.get(f"/report/{tok}/briefing.md").text
    # limitations section present; risk not overstated to certainty
    assert "Confidence & limitations" in md
    low = md.lower()
    for overstatement in ("definitely", "proven fact", "guaranteed",
                          "100% certain", "conclusively proven"):
        assert overstatement not in low


def test_policy_hook_extension_point_is_noop():
    seen = []
    governed_analysis.register_policy_hook(lambda ctx: seen.append(ctx))
    governed_analysis.run_policy_hooks({"x": 1})
    assert seen == [{"x": 1}]     # extension point works, empty by default
