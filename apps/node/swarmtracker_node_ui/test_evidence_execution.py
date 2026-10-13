"""Evidence selection fails before provider access; manifests bind exact excerpts."""
import hashlib
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swarmtracker_node_ui import governed_analysis as ga, canonical_case

def test_total_budget_and_source_lineage():
    items = [{"text": "a" * 4000, "label": "A"},
             {"text": "é" * 4000, "label": "B"},
             {"text": "unselected"}]
    digest, manifest = ga.bounded_text_payload(items)
    assert len(manifest) == 2
    assert manifest[0]["size_bytes"] == 4000
    assert manifest[1]["size_bytes"] == 4000  # 2000 UTF8 two-byte chars
    assert manifest[1]["sha256"] == hashlib.sha256(("é" * 2000).encode()).hexdigest()
    assert manifest[1]["source_sha256"] == hashlib.sha256(("é" * 4000).encode()).hexdigest()
    assert "unselected" not in digest

@pytest.mark.parametrize("items", [None, {}, "abc", [], [None], [{"text": 5}],
    [{"text": ""}], [{"text": "x", "label": []}], [{"text": "x", "evidence_type": {}}],
    [{"text": "\ud800"}], [{"text": "good"}] * 8 + [{"text": object()}]])
def test_invalid_selection_has_zero_provider_access(items, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid selection reached policy/readiness/provider")
    monkeypatch.setattr(ga.keel_policy, "multimodal_escalation_allowed", forbidden)
    monkeypatch.setattr(ga.providers, "provider_config", forbidden)
    monkeypatch.setattr(ga.providers, "build_cloud_router", forbidden)
    result = ga.run_text_cloud_assist(items, operator_authorized=True)
    meta = ga.text_assist_router_meta(result)
    ex = ga.execution_record(meta)
    assert ex["state"] == "input_blocked" and ex["blocked"]
    assert not ex["provider_attempted"] and not meta["sent_artifacts"]

@pytest.mark.parametrize("chars,count", [(True, 8), (0, 8), (6001, 8),
                                         (6000, False), (6000, 9)])
def test_caller_budget_cannot_widen(chars, count):
    with pytest.raises(ValueError):
        ga.bounded_text_payload([{"text": "x"}], max_chars=chars, max_items=count)

def test_headers_are_bounded_and_cannot_inject_digest_rows():
    _, manifest = ga.bounded_text_payload([{"text": "x", "label": "A\n" * 500,
                                          "evidence_type": "B\r" * 500}])
    assert len(manifest[0]["label"]) <= 80 and "\n" not in manifest[0]["label"]
    assert len(manifest[0]["evidence_type"]) <= 80 and "\r" not in manifest[0]["evidence_type"]

def test_attempt_is_not_inferred_from_model_or_authorization():
    ex = ga.execution_record({"escalated": True, "attempted_models": ["x"],
                             "authorization_event_id": "auth"})
    assert ex["state"] == "authorized_not_attempted"
    assert ex["provider_attempted"] is False
    ex = ga.execution_record({"escalated": True, "provider_attempted": True,
                             "escalation_ok": False, "result_write_failed": True,
                             "authorization_event_id": "auth"})
    assert ex["state"] == "cloud_failed" and not ex["blocked"]
    assert ex["result_audit"] == "failed"
    case = canonical_case._escalation_record({"provider_attempted": True,
        "escalated": True, "escalation_ok": True, "authorization_event_id": "auth"})
    assert case["execution"]["state"] == "cloud_succeeded"

def test_canonical_failed_and_invalid_states_keep_intent_honest():
    invalid = ga.run_text_cloud_assist([{"text": 5}], operator_authorized=True)
    meta = ga.text_assist_router_meta(invalid)
    assert meta["operator_authorized"] is True
    case = canonical_case._escalation_record(meta)
    assert case["state"] == "input_blocked" and case["authorized"] is False
    case = canonical_case._escalation_record({
        "escalated": True, "provider_attempted": True, "authorized": True,
        "escalation_ok": False, "authorization_event_id": "auth"})
    assert case["state"] == "failed" and case["execution"]["state"] == "cloud_failed"
