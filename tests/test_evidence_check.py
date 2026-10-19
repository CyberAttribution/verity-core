import copy
import json

import pytest

from verity_core.trust import evidence_check as evidence
from verity_core.trust import record as trust


def check(outcome="supported"):
    texts = ["The maintenance window is Monday."]
    if outcome == "conflicting":
        texts.append("The maintenance window is Tuesday.")
    claim = "The maintenance window is Tuesday." if outcome == "contradicted" else texts[0]
    if outcome == "insufficient":
        claim = "The service holds ISO 27001 certification."
    sources = [dict(id="source" + str(i), reference="fixture:notice" + str(i),
                    revision="1", retrieved_at="2026-10-19T16:00:00Z",
                    text=text, sha256=evidence.digest(text)) for i, text in enumerate(texts)]
    citations = [] if outcome == "insufficient" else [
        dict(source_id=s["id"], start=0, end=len(s["text"]), passage=s["text"]) for s in sources]
    return dict(schema=evidence.SCHEMA, id="check-01",
        claim=dict(id="claim-01", text=claim, sha256=evidence.digest(claim)), sources=sources,
        assessment=dict(outcome=outcome, citations=citations, reason="Independently labeled synthetic fixture.",
                        unresolved=outcome in ("insufficient", "conflicting")),
        method=dict(kind="model_interpretation", version="1", checker_model="fixture-checker",
                    checker_revision="fixture-revision", request_id="request-01", attempt=1,
                    max_requests=1, latency_ms=2.5, usage=dict(prompt_tokens=12, completion_tokens=5),
                    billing_source="synthetic; no billed request"),
        checked_at="2026-10-19T16:01:00Z", context_ref=None,
        limitations=["Synthetic fixture; source support does not prove factual truth."])


@pytest.mark.parametrize("outcome", ["supported", "contradicted", "insufficient", "conflicting"])
def test_all_outcomes_roundtrip_and_preserve_source_and_unresolved_state(outcome):
    original = check(outcome)
    bundle = evidence.export(original)
    checkpoint = copy.deepcopy(bundle["checkpoint"])
    restored = json.loads(json.dumps(bundle))
    assert evidence.verify(restored, checkpoint)
    payload = restored["record"]["entries"][0]["payload"]["check"]
    assert payload == original
    assert payload["assessment"]["unresolved"] == (outcome in ("insufficient", "conflicting"))
    # Caller-owned inputs cannot mutate the hashed payload.
    original["sources"][0]["text"] = "Changed"
    assert evidence.verify(restored, checkpoint)


def test_bad_model_citation_shape_is_refused_instead_of_normalized_to_supported():
    c = check("contradicted")
    citation = c["assessment"]["citations"][0]
    citation["text"] = citation.pop("passage")
    with pytest.raises(ValueError, match="fields"):
        evidence.export(c)


@pytest.mark.parametrize("alter", ["source_hash", "claim_hash", "passage", "span", "source_ref", "duplicate"])
def test_source_and_claim_links_are_independently_checked(alter):
    c = check()
    citation = c["assessment"]["citations"][0]
    if alter == "source_hash":
        c["sources"][0]["text"] = "The maintenance window is Tuesday."
    elif alter == "claim_hash":
        c["claim"]["text"] = "Altered claim"
    elif alter == "passage":
        citation["passage"] = "The maintenance window is Tuesday."
    elif alter == "span":
        citation["start"] = True
    elif alter == "source_ref":
        citation["source_id"] = "absent"
    else:
        c["assessment"]["citations"].append(copy.deepcopy(citation))
    with pytest.raises(ValueError):
        evidence.validate(c)


def test_conflict_cannot_be_resolved_silently_or_manufactured_from_one_source():
    c = check("conflicting")
    c["assessment"]["unresolved"] = False
    with pytest.raises(ValueError, match="Unresolved"):
        evidence.validate(c)
    c["assessment"]["unresolved"] = True
    c["assessment"]["citations"].pop()
    with pytest.raises(ValueError, match="at least two"):
        evidence.validate(c)


def test_context_is_a_pinned_assertion_and_cannot_be_rewritten_under_old_anchor():
    c = check()
    c["context_ref"] = dict(profile_id="approved-profile", version=1,
                            sha256="a" * 64, approval_status="proposed")
    with pytest.raises(ValueError, match="separately approved"):
        evidence.validate(c)
    c["context_ref"]["approval_status"] = "approved"
    original = evidence.export(c)
    anchor = copy.deepcopy(original["checkpoint"])
    c["context_ref"]["version"] = 2
    replacement = evidence.export(c)
    assert evidence.verify(replacement, replacement["checkpoint"])
    assert not evidence.verify(replacement, anchor)
    altered = copy.deepcopy(original)
    altered["record"]["entries"][0]["payload"]["check"]["context_ref"]["version"] = 9
    assert not evidence.verify(altered, anchor)


def test_exact_quote_method_reconstructs_only_presence_and_never_semantic_contradiction():
    c = check()
    c["method"]["kind"] = "exact_quote_presence"
    bundle = evidence.export(c)
    assert evidence.verify(bundle, bundle["checkpoint"])
    c["assessment"]["outcome"] = "contradicted"
    with pytest.raises(ValueError, match="does not reconstruct"):
        evidence.validate(c)
    missing = check("insufficient")
    missing["method"]["kind"] = "exact_quote_presence"
    evidence.validate(missing)


def test_record_rewrite_truncation_and_unprotected_subject_edits_are_refused():
    original = evidence.export(check())
    anchor = copy.deepcopy(original["checkpoint"])
    for mutation in ("subject", "payload", "truncate"):
        bundle = copy.deepcopy(original)
        if mutation == "subject":
            bundle["record"]["subject"]["identifier"] = "other-check"
            assert trust.chain_valid(bundle["record"])
        elif mutation == "payload":
            bundle["record"]["entries"][0]["payload"]["check"]["assessment"]["reason"] = "Rewritten"
        else:
            bundle["record"]["entries"].clear()
        assert not evidence.verify(bundle, anchor)


@pytest.mark.parametrize("alter", ["requests", "attempt", "usage", "date", "limitations"])
def test_checking_limits_and_required_provenance_fail_closed(alter):
    c = check()
    if alter == "requests":
        c["method"]["max_requests"] = 3
    elif alter == "attempt":
        c["method"]["attempt"] = 2
    elif alter == "usage":
        c["method"]["usage"]["prompt_tokens"] = -1
    elif alter == "date":
        c["sources"][0]["retrieved_at"] = "2026-02-30T00:00:00Z"
    else:
        c["limitations"] = []
    with pytest.raises(ValueError):
        evidence.export(c)


def test_offline_cli_reconstructs_and_refuses_changed_payload_and_wrong_anchor(tmp_path):
    import subprocess
    import sys
    bundle=evidence.export(check())
    path=tmp_path/'bundle.json';anchor=tmp_path/'checkpoint.json'
    path.write_text(json.dumps(bundle));anchor.write_text(json.dumps(bundle['checkpoint']))
    command=[sys.executable,'-m','verity_core.trust.evidence_check',str(path),str(anchor)]
    valid=subprocess.run(command,capture_output=True,text=True)
    assert valid.returncode==0 and 'remain unverified' in valid.stdout
    altered=copy.deepcopy(bundle)
    altered['record']['entries'][0]['payload']['check']['sources'][0]['text']='Altered'
    path.write_text(json.dumps(altered))
    assert subprocess.run(command,capture_output=True).returncode==1
    path.write_text(json.dumps(bundle))
    anchor.write_text(json.dumps(evidence.export(check())['checkpoint']))
    assert subprocess.run(command,capture_output=True).returncode==1
    anchor.write_text('{"record_id":"a","record_id":"b"}')
    assert subprocess.run(command,capture_output=True).returncode==2
    path.write_bytes(b'x'*1048577)
    assert subprocess.run(command,capture_output=True).returncode==2
