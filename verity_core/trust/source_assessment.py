"""Strict model-assessment intake into the existing portable evidence contract.

This adapter performs no dispatch, retry, source retrieval or authorization.
The caller supplies observed method metadata; model text cannot choose it.
"""
import copy

from ..json_safety import bounded_json_loads
from . import evidence_check as evidence

MAX_ASSESSMENT_BYTES = 16384
MAX_CITATIONS = 2


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate assessment JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Nonfinite assessment JSON value")


def from_model_response(*, identifier, claim_id, claim_text, sources, content,
                        method, checked_at):
    """Bind a strict assessment to caller-selected claim, sources and method.

    Passage offsets use the first exact occurrence in the retained snapshot.
    This proves exact quotation binding, not semantic relevance or source truth.
    Missing usage or checker identity is rejected, never invented as zero.
    """
    evidence.text(claim_text)
    if not isinstance(content, str):
        raise ValueError("Model assessment must be plain JSON text")
    if len(content.encode("utf-8")) > MAX_ASSESSMENT_BYTES:
        raise ValueError("Model assessment exceeds byte allowance")
    assessment = bounded_json_loads(content, max_depth=32,
        object_pairs_hook=_unique, parse_constant=_constant)
    evidence.fields(assessment, "outcome citations reason")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError("Caller-selected source snapshots required")
    by_id = {}
    for source in sources:
        evidence.fields(source, "id reference revision retrieved_at text sha256")
        evidence.text(source["id"], 160)
        evidence.text(source["text"], 16000)
        if source["id"] in by_id:
            raise ValueError("Duplicate caller source ID")
        by_id[source["id"]] = source["text"]
    if not isinstance(assessment["citations"], list) or len(assessment["citations"]) > MAX_CITATIONS:
        raise ValueError("Model citation allowance exceeded")
    citations = []
    for citation in assessment["citations"]:
        evidence.fields(citation, "source_id passage")
        source_id, passage = citation["source_id"], citation["passage"]
        if not isinstance(source_id, str) or source_id not in by_id:
            raise ValueError("Model cited an unknown source")
        evidence.text(passage, 4000)
        start = by_id[source_id].find(passage)
        if start < 0:
            raise ValueError("Model passage is not an exact source substring")
        citations.append(dict(source_id=source_id, start=start,
                              end=start + len(passage), passage=passage))
    if not isinstance(method, dict) or method.get("kind") != "model_interpretation":
        raise ValueError("Observed model-check method metadata required")
    check = dict(schema=evidence.SCHEMA, id=identifier,
        claim=dict(id=claim_id, text=claim_text, sha256=evidence.digest(claim_text)),
        sources=sources,
        assessment=dict(outcome=assessment["outcome"], citations=citations,
            reason=assessment["reason"],
            unresolved=assessment["outcome"] in ("insufficient", "conflicting")),
        method=method, checked_at=checked_at, context_ref=None,
        limitations=[evidence.LIMITATION,
            "Exact passages and hashes do not independently verify model judgments or source freshness.",
            "Checker identity and billing metadata are caller observations, not authenticated attestation.",
            "Evidence does not authorize company work or establish a verified task outcome."])
    # Reject malformed caller metadata before recursively copying it. The
    # validated schema has bounded depth/size, and the result owns its snapshot.
    evidence.validate(check)
    return copy.deepcopy(check)
