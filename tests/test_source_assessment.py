import json
import pytest
from verity_core.trust import evidence_check as evidence
from verity_core.trust.source_assessment import from_model_response, MAX_ASSESSMENT_BYTES


def inputs():
    text = "The notice states the token secret was not rotated."
    return dict(identifier="assessment-01", claim_id="research-01:source-claim",
        claim_text="The notice reports no token rotation.",
        sources=[dict(id="notice",reference="https://example.com/notice",
            revision="saved-v1",retrieved_at="2026-10-19T19:00:00Z",
            text=text,sha256=evidence.digest(text))],
        content=json.dumps(dict(outcome="supported",
            citations=[dict(source_id="notice",passage="the token secret was not rotated")],
            reason="The supplied notice directly states this.")),
        method=dict(kind="model_interpretation",version="1",checker_model="review-model",
            checker_revision="pinned-revision",request_id="bounded-request",attempt=1,
            max_requests=1,latency_ms=20,usage=dict(prompt_tokens=100,completion_tokens=30),
            billing_source="Observed development rental; provider charge unknown"),
        checked_at="2026-10-19T19:01:00Z")


def test_exact_binding_roundtrip_and_caller_metadata_are_preserved():
    kwargs=inputs();check=from_model_response(**kwargs)
    citation=check["assessment"]["citations"][0]
    assert kwargs["sources"][0]["text"][citation["start"]:citation["end"]]==citation["passage"]
    assert check["claim"]["id"]=="research-01:source-claim"
    bundle=evidence.export(check);anchor=dict(bundle["checkpoint"])
    assert evidence.verify(bundle,anchor)
    kwargs["sources"][0]["text"]="Changed after intake"
    kwargs["method"]["checker_model"]="Changed after intake"
    assert check["sources"][0]["text"]!="Changed after intake"
    assert check["method"]["checker_model"]=="review-model"
    bundle["record"]["entries"][0]["payload"]["check"]["assessment"]["reason"]="Altered"
    assert not evidence.verify(bundle,anchor)


@pytest.mark.parametrize("content", [
    '```json\n{}\n```',
    '{"outcome":"supported","outcome":"insufficient","citations":[],"reason":"Ambiguous"}',
    '{"outcome":"insufficient","citations":[],"reason":NaN}',
    "["*33+"0"+"]"*33,
    " "* (MAX_ASSESSMENT_BYTES+1),
    json.dumps(dict(outcome="supported",citations=[],reason="No citation")),
    json.dumps(dict(outcome="supported",citations=[dict(source_id="other",passage="token")],reason="Unknown source")),
    json.dumps(dict(outcome="supported",citations=[dict(source_id="notice",passage="the secret was rotated")],reason="Not exact")),
    json.dumps(dict(outcome="supported",citations=[dict(source_id="notice",passage="token",start=0)],reason="Model-supplied span")),
    json.dumps(dict(outcome="insufficient",citations=[],reason="No evidence",checker_model="spoofed")),
])
def test_untrusted_model_contract_failures_are_not_repaired(content):
    kwargs=inputs();kwargs["content"]=content
    with pytest.raises(ValueError):from_model_response(**kwargs)


@pytest.mark.parametrize("outcome", ["supported","contradicted","insufficient","conflicting"])
def test_all_outcomes_preserve_unresolved_state_without_claiming_semantic_truth(outcome):
    kwargs=inputs();assessment=json.loads(kwargs["content"]);assessment["outcome"]=outcome
    if outcome=="insufficient":assessment["citations"]=[]
    if outcome=="conflicting":
        other=dict(kwargs["sources"][0],id="other",text="The notice states the token was rotated.")
        other["sha256"]=evidence.digest(other["text"]);kwargs["sources"].append(other)
        assessment["citations"].append(dict(source_id="other",passage="the token was rotated"))
    kwargs["content"]=json.dumps(assessment);check=from_model_response(**kwargs)
    assert check["assessment"]["outcome"]==outcome
    assert check["assessment"]["unresolved"]==(outcome in {"insufficient","conflicting"})
    assert evidence.LIMITATION in check["limitations"]


@pytest.mark.parametrize("change", ["missing_usage","wrong_method","wrong_source_hash","duplicate_source"])
def test_caller_observations_and_source_integrity_must_also_validate(change):
    kwargs=inputs()
    if change=="missing_usage":kwargs["method"].pop("usage")
    elif change=="wrong_method":kwargs["method"]["kind"]="exact_quote_presence"
    elif change=="wrong_source_hash":kwargs["sources"][0]["sha256"]="0"*64
    else:kwargs["sources"].append(dict(kwargs["sources"][0]))
    with pytest.raises(ValueError):from_model_response(**kwargs)
