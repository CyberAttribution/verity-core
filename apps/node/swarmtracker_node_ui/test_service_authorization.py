"""
Focused direct-service regressions for the v0.5.0 per-job operator
authorization gate on the shared cloud-assist entry points
(governed_analysis.run_text_cloud_assist / build_router).

Run: PYTHONPATH=apps/node python -m pytest \
    apps/node/swarmtracker_node_ui/test_service_authorization.py -q

Everything is mocked; no cloud API is ever called. Stores go to a
temporary directory.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_T = tempfile.mkdtemp()
os.environ.setdefault("NODE_UI_DATA_DIR", _T)
os.environ.setdefault("NODE_UI_DB", os.path.join(_T, "auth_t.db"))
os.environ.setdefault("NODE_UI_MEDIA_LEDGER",
                      os.path.join(_T, "auth_k.jsonl"))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "swarmtracker-main")

from swarmtracker_node_ui import governed_analysis, providers  # noqa: E402
from swarmtracker_node_ui import core_bridge  # noqa: E402

_DENY = "operator authorization not granted"

_ITEMS = [{"label": "Note", "text": "suspicious wire to apex-billing.top",
           "evidence_type": "freeform_text"}]


class _VOK:
    provider = "vertex"
    project = "swarmtracker-main"
    location = "global"
    models = ["gemini-3.6-flash"]

    def generate(self, p):
        return {"ok": True, "provider": "vertex",
                "response": "Bounded cloud assessment.",
                "model_used": "gemini-3.6-flash", "latency_ms": 5,
                "project": "swarmtracker-main", "region": "global",
                "attempted_models": ["gemini-3.6-flash"], "attempts": []}


class _VFail(_VOK):
    """Provider CONTACTED and returned a genuine failure."""

    def generate(self, p):
        return {"ok": False, "provider": "vertex",
                "error": "vertex 500 boom",
                "model_used": None, "latency_ms": None,
                "project": "swarmtracker-main", "region": "global",
                "attempted_models": ["gemini-3.6-flash"],
                "attempts": [{"model": "gemini-3.6-flash",
                              "error": "vertex 500 boom"}]}


def _patch_ready(monkeypatch, *, policy_ok=True, provider_ready=True,
                 cloud=_VOK()):
    """Mock ALL policy/provider layers; record every probe."""
    seen = {"policy_probes": 0, "provider_config": 0, "router_builds": 0,
            "generates": 0}

    def _policy():
        seen["policy_probes"] += 1
        return policy_ok, ("policy lock" if not policy_ok else "")

    def _pcfg():
        seen["provider_config"] += 1
        if provider_ready:
            return {"ready": True, "reason": "", "provider": "vertex",
                    "project": "swarmtracker-main", "region": "global"}
        return {"ready": False, "reason": "no ADC", "provider": "vertex",
                "project": None, "region": None}

    def _build(sel=None):
        seen["router_builds"] += 1
        if provider_ready and cloud is not None:
            return cloud, ""
        return None, "no ADC"

    if cloud is not None and provider_ready:
        _base = type(cloud)

        class _Counting(_base):
            def generate(self, p):
                seen["generates"] += 1
                return _base.generate(self, p)

        _build = _mk_build(seen, _Counting())
    monkeypatch.setattr(governed_analysis.keel_policy,
                        "multimodal_escalation_allowed", _policy)
    monkeypatch.setattr(governed_analysis.providers, "provider_config",
                        _pcfg)
    monkeypatch.setattr(governed_analysis.providers, "build_cloud_router",
                        _build)
    return seen


def _mk_build(seen, cloud):
    def _build(sel=None):
        seen["router_builds"] += 1
        return cloud, ""
    return _build


# ---- missing / false / malformed authorization: deny by default ----

@pytest.mark.parametrize("sig", [
    pytest.param({}, id="missing"),
    pytest.param({"operator_authorized": False}, id="false"),
    pytest.param({"operator_authorized": None}, id="null"),
    pytest.param({"operator_authorized": "yes"}, id="truthy-string"),
    pytest.param({"operator_authorized": "True"}, id="string-True"),
    pytest.param({"operator_authorized": 1}, id="truthy-number"),
    pytest.param({"operator_authorized": [True]}, id="truthy-list"),
    pytest.param({"operator_authorized": {"x": 1}}, id="truthy-dict"),
])
def test_deny_by_default_no_calls(monkeypatch, sig):
    seen = _patch_ready(monkeypatch)
    r = governed_analysis.run_text_cloud_assist(_ITEMS, **sig)
    assert r["ok"] is False and r["available"] is False
    assert r["authorized"] is False and r["denied"] is True
    assert _DENY in r["reason"]
    assert r["sent_manifest"] == [] and r["text"] == ""
    assert not r.get("provider") and not r.get("model_used")
    # zero readiness / provider-build / generate calls
    assert seen == {"policy_probes": 0, "provider_config": 0,
                    "router_builds": 0, "generates": 0}


def test_deny_by_default_build_router_no_calls(monkeypatch):
    seen = _patch_ready(monkeypatch)
    router, why = governed_analysis.build_router(lambda p: {}, "m",
                                                 operator_authorized=False)
    assert router is None and _DENY in why
    assert seen["policy_probes"] == 0 and seen["router_builds"] == 0


# ---- reserved workflow refused at the entry point, even with True ----

def test_reserved_workflow_refused_even_when_authorized(monkeypatch):
    seen = _patch_ready(monkeypatch)
    with pytest.raises(governed_analysis.ReservedWorkflowError):
        governed_analysis.run_text_cloud_assist(
            _ITEMS, workflow=governed_analysis.RESERVED_SENSITIVE_WORKFLOW,
            operator_authorized=True)
    assert seen["generates"] == 0 and seen["router_builds"] == 0


def test_reserved_workflow_refusal_is_audited(monkeypatch):
    from swarmtracker_node_ui import keel_events
    _patch_ready(monkeypatch)
    n0 = keel_events.count_events()
    with pytest.raises(governed_analysis.ReservedWorkflowError):
        governed_analysis.run_text_cloud_assist(
            _ITEMS, workflow=governed_analysis.RESERVED_SENSITIVE_WORKFLOW,
            operator_authorized=True)
    evs = [e["event"] for e in keel_events.read_events(50)]
    assert "reserved_workflow_refused" in evs
    assert keel_events.count_events() > n0


# ---- authorized success ----

def test_authorized_success_reaches_mocked_provider(monkeypatch):
    seen = _patch_ready(monkeypatch)
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    assert r["ok"] is True and r["available"] is True
    assert r["authorized"] is True
    assert r.get("denied") is False
    assert r["text"] == "Bounded cloud assessment."
    assert r["sent_manifest"] and r["model_used"] == "gemini-3.6-flash"
    assert seen["router_builds"] == 1 and seen["generates"] == 1


def test_authorized_build_router_success(monkeypatch):
    seen = _patch_ready(monkeypatch)
    router, why = governed_analysis.build_router(lambda p: {"ok": True},
                                                 operator_authorized=True)
    assert router is not None and why == ""
    assert seen["router_builds"] == 1


# ---- authorized but policy / provider refusal: honest, not conflated ----

def test_authorized_policy_refusal_no_provider_build(monkeypatch):
    seen = _patch_ready(monkeypatch, policy_ok=False)
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    # policy refusal = execution authorization DENIED (intent was given)
    assert r["operator_authorized"] is True
    assert r["authorized"] is False and r["denied"] is True
    assert r["ok"] is False and r["available"] is False
    assert "policy lock" in r["reason"]
    assert r["sent_manifest"] == []
    assert seen["router_builds"] == 0 and seen["generates"] == 0
    assert not r.get("provider")


def test_authorized_provider_refusal_not_conflated_with_denial(monkeypatch):
    seen = _patch_ready(monkeypatch, provider_ready=False)
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    assert r["ok"] is False and r["denied"] is False
    assert r["authorized"] is True
    # explicitly marked as unavailable-before-contact, never a denial
    assert r.get("unavailable") is True
    assert r.get("provider_attempted") is False
    assert "no ADC" in r["reason"]
    assert r["sent_manifest"] == [] and seen["generates"] == 0


def test_build_router_failure_unified_with_pcfg_fields(monkeypatch):
    """Authorized job where provider_config is ready but the router
    build fails: result carries pcfg provider/project/region (no
    hardcoded provider), still unavailable-before-contact."""
    seen = _patch_ready(monkeypatch, cloud=None)
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    assert r["ok"] is False and r["denied"] is False
    assert r["authorized"] is True
    assert r["unavailable"] is True and r["provider_attempted"] is False
    assert r["provider"] == "vertex"
    assert r["project"] == "swarmtracker-main"
    assert r["region"] == "global"
    assert "no ADC" in r["reason"]
    assert r["attempted_models"] == [] and r["attempts"] == []
    assert r["model_used"] is None and r["provider_status"] is None
    assert seen["generates"] == 0


# ---- denial honesty through the metadata / provenance helpers ----

def test_router_meta_shows_denial_not_provider_failure(monkeypatch):
    seen = _patch_ready(monkeypatch)
    r = governed_analysis.run_text_cloud_assist(_ITEMS)  # no auth signal
    rm = governed_analysis.text_assist_router_meta(r)
    assert rm["escalated"] is True and rm["denied"] is True
    assert rm["escalation_provider"] is None
    assert rm["attempted_models"] == [] and rm["sent_artifacts"] == []
    assert rm["provider_error"] is None
    assert rm["custody"]["sent_to_provider"] == []
    assert rm["custody"]["sent_but_provider_failed"] == []
    assert _DENY in (rm["reason"] or "")
    assert seen["generates"] == 0


def test_router_meta_shows_policy_refusal_as_denial(monkeypatch):
    seen = _patch_ready(monkeypatch, policy_ok=False)
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    rm = governed_analysis.text_assist_router_meta(r)
    assert rm["denied"] is True and rm["escalation_provider"] is None
    assert rm["attempted_models"] == []
    assert "policy lock" in (rm["reason"] or "")
    assert seen["generates"] == 0


def test_router_meta_provider_failure_is_not_a_denial(monkeypatch):
    """Genuine provider error AFTER contact: failed, but never denied —
    and explicitly NOT conflated with the unavailable-before-contact
    case."""
    seen = _patch_ready(monkeypatch, cloud=_VFail())
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    assert r.get("unavailable") is False
    assert r.get("provider_attempted") is True
    assert seen["generates"] == 1
    rm = governed_analysis.text_assist_router_meta(r)
    assert rm["denied"] is False
    assert rm["unavailable"] is False
    assert rm["escalation_provider"] == "vertex"
    assert "boom" in (rm["provider_error"] or "")
    assert rm["attempted_models"] == ["gemini-3.6-flash"]
    assert rm["custody"]["sent_but_provider_failed"]
    assert rm["custody"]["sent_to_provider"] == []


def test_unavailable_propagates_as_no_attempt_not_provider_failure(
        monkeypatch):
    """Authorized + provider unconfigured: metadata and canonical case
    must record 'unavailable — nothing attempted', NOT a provider
    failure and NOT a denial."""
    seen = _patch_ready(monkeypatch, provider_ready=False)
    r = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                operator_authorized=True)
    rm = governed_analysis.text_assist_router_meta(r)
    assert rm["escalated"] is True
    assert rm["denied"] is False and rm["unavailable"] is True
    assert rm["escalation_ok"] is False
    # provider never contacted: no error, no attempted models, no custody
    assert rm["provider_error"] is None
    assert rm["provider_status"] == "unavailable"
    assert rm["attempted_models"] == []
    assert rm["model_used"] is None
    assert rm["sent_artifacts"] == []
    assert rm["custody"]["sent_to_provider"] == []
    assert rm["custody"]["sent_but_provider_failed"] == []
    # configured provider retained for provenance only
    assert rm["escalation_provider"] == "vertex"
    assert "no ADC" in (rm["reason"] or "")
    assert seen["generates"] == 0
    from swarmtracker_node_ui import canonical_case
    rec = canonical_case._escalation_record(rm)
    assert rec["state"] == "unavailable"
    assert rec["authorized"] is True
    assert rec["unavailable"] is True and rec["denied"] is False


def test_canonical_case_classifies_denied_meta(monkeypatch):
    from swarmtracker_node_ui import canonical_case
    seen = _patch_ready(monkeypatch)
    r = governed_analysis.run_text_cloud_assist(_ITEMS)
    rec = canonical_case._escalation_record(
        governed_analysis.text_assist_router_meta(r))
    assert rec["state"] == "denied" and rec["denied"] is True
    # a denied job is NEVER recorded as authorized, even though the
    # escalation intent was present
    assert rec["authorized"] is False
    assert rec["provider"] is None
    assert rec["attempted_models"] == []
    assert _DENY in (rec["reason"] or "")
    assert seen["generates"] == 0


def test_canonical_case_all_escalation_states():
    """Explicit coverage of every canonical escalation state and its
    `authorized` truth."""
    from swarmtracker_node_ui import canonical_case as cc

    def rec(**rm):
        return cc._escalation_record(rm)

    # not authorized: no escalation signal at all
    r = rec(escalated=False, denied=False)
    assert r["state"] == "not_authorized" and r["authorized"] is False
    # denied: refused before any provider engagement — NOT authorized
    r = rec(escalated=True, denied=True, escalation_provider=None)
    assert r["state"] == "denied" and r["authorized"] is False
    # used: provider contacted and succeeded — authorized
    r = rec(escalated=True, denied=False, escalation_ok=True,
            unavailable=False, escalation_provider="vertex",
            model_used="gemini-3.6-flash")
    assert r["state"] == "used" and r["authorized"] is True
    # unavailable: authorized but NO provider was ever contacted
    r = rec(escalated=True, denied=False, escalation_ok=False,
            unavailable=True, escalation_provider="vertex")
    assert r["state"] == "unavailable" and r["authorized"] is True
    # failed: provider contacted (authorized) but assessment failed
    r = rec(escalated=True, denied=False, escalation_ok=False,
            unavailable=False, multimodal_unavailable=True,
            escalation_provider="vertex")
    assert r["state"] == "failed" and r["authorized"] is True
    # authorized_no_effect: authorized, contacted, no usable effect
    r = rec(escalated=True, denied=False, escalation_ok=False,
            unavailable=False, multimodal_unavailable=False,
            escalation_provider="vertex")
    assert r["state"] == "authorized_no_effect" and r["authorized"] is True


# ---- build_router guards the reserved workflow itself ----

def test_build_router_refuses_reserved_workflow(monkeypatch):
    _patch_ready(monkeypatch)
    with pytest.raises(governed_analysis.ReservedWorkflowError):
        governed_analysis.build_router(lambda p: {}, "m",
                                       workflow=governed_analysis.
                                       RESERVED_SENSITIVE_WORKFLOW,
                                       operator_authorized=True)


# ---- no retained authorization across repeated jobs ----

def test_no_retained_authorization_between_jobs(monkeypatch):
    _patch_ready(monkeypatch)
    r1 = governed_analysis.run_text_cloud_assist(_ITEMS,
                                                 operator_authorized=True)
    assert r1["ok"] is True
    # second job WITHOUT the explicit signal must be denied, never sticky
    r2 = governed_analysis.run_text_cloud_assist(_ITEMS)
    assert r2["denied"] is True and _DENY in r2["reason"]
    # and a malformed signal is not grandfathered by the earlier True
    r3 = governed_analysis.run_text_cloud_assist(
        _ITEMS, operator_authorized=json.dumps(True))
    assert r3["denied"] is True
