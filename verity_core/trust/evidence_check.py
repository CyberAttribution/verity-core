"""Portable evidence-check envelopes over the existing Trust Record.

Validation reconstructs source hashes, exact passages and record links. It does
not independently prove model judgments, source truth, freshness or identity.
This module performs no model call, network request, policy update or action.
"""
import copy
import datetime
import hashlib

from . import record as trust

SCHEMA = "evidence_check/v0.1"
BUNDLE_SCHEMA = "evidence_check_bundle/v0.1"
OUTCOMES = {"supported", "contradicted", "insufficient", "conflicting"}
LIMITATION = "Source support is not guaranteed factual truth or authorization."


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text(value, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Invalid bounded evidence text")
    value.encode("utf-8")
    return value


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError("Evidence fields do not match the declared schema")


def timestamp(value):
    text(value, 20)
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("Evidence timestamps must be real UTC dates") from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("Noncanonical evidence date")


def validate(check):
    fields(check, "schema id claim sources assessment method checked_at context_ref limitations")
    if check["schema"] != SCHEMA:
        raise ValueError("Unknown evidence-check schema")
    text(check["id"], 160)
    timestamp(check["checked_at"])
    fields(check["claim"], "id text sha256")
    text(check["claim"]["id"], 160)
    text(check["claim"]["text"])
    if check["claim"]["sha256"] != digest(check["claim"]["text"]):
        raise ValueError("Claim hash mismatch")
    sources = check["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError("One to eight source snapshots required")
    by_id = {}
    for source in sources:
        fields(source, "id reference revision retrieved_at text sha256")
        text(source["id"], 160)
        text(source["reference"], 1000)
        text(source["revision"], 160)
        timestamp(source["retrieved_at"])
        text(source["text"], 16000)
        if source["id"] in by_id or source["sha256"] != digest(source["text"]):
            raise ValueError("Duplicate source ID or source hash mismatch")
        by_id[source["id"]] = source
    if sum(len(s["text"]) for s in sources) > 64000:
        raise ValueError("Source snapshot allowance exceeded")
    assessment = check["assessment"]
    fields(assessment, "outcome citations reason unresolved")
    if not isinstance(assessment["outcome"], str) or assessment["outcome"] not in OUTCOMES:
        raise ValueError("Unknown evidence outcome")
    text(assessment["reason"], 2000)
    expected_unresolved = assessment["outcome"] in {"insufficient", "conflicting"}
    if type(assessment["unresolved"]) is not bool or assessment["unresolved"] != expected_unresolved:
        raise ValueError("Unresolved outcome must remain explicit")
    citations = assessment["citations"]
    if not isinstance(citations, list) or len(citations) > 16:
        raise ValueError("Invalid citation allowance")
    seen = set()
    for citation in citations:
        fields(citation, "source_id start end passage")
        if not isinstance(citation["source_id"], str) or citation["source_id"] not in by_id:
            raise ValueError("Unknown cited source")
        source = by_id[citation["source_id"]]["text"]
        start, end = citation["start"], citation["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
            raise ValueError("Invalid source passage span")
        text(citation["passage"], 4000)
        if source[start:end] != citation["passage"]:
            raise ValueError("Supporting passage does not match source snapshot")
        key = (citation["source_id"], start, end)
        if key in seen:
            raise ValueError("Duplicate citation")
        seen.add(key)
    if assessment["outcome"] in {"supported", "contradicted", "conflicting"} and not citations:
        raise ValueError("Outcome requires checked source passages")
    if assessment["outcome"] == "conflicting" and len({c["source_id"] for c in citations}) < 2:
        raise ValueError("Conflicting sources requires at least two cited snapshots")
    method = check["method"]
    fields(method, "kind version checker_model checker_revision request_id attempt max_requests latency_ms usage billing_source")
    if not isinstance(method["kind"], str) or method["kind"] not in {"model_interpretation", "exact_quote_presence"}:
        raise ValueError("Unknown checking method")
    for key in ("version", "checker_model", "checker_revision", "request_id", "billing_source"):
        text(method[key], 240)
    for key in ("attempt", "max_requests"):
        if type(method[key]) is not int or not 1 <= method[key] <= 2:
            raise ValueError("Checking attempts must be bounded")
    if method["attempt"] > method["max_requests"]:
        raise ValueError("Checking attempt allowance exceeded")
    if type(method["latency_ms"]) not in (int, float) or not 0 <= method["latency_ms"] <= 300000:
        raise ValueError("Invalid check latency")
    fields(method["usage"], "prompt_tokens completion_tokens")
    if any(type(v) is not int or not 0 <= v <= 100000 for v in method["usage"].values()):
        raise ValueError("Invalid observed token usage")
    if method["kind"] == "exact_quote_presence":
        present = any(check["claim"]["text"] in s["text"] for s in sources)
        expected = "supported" if present else "insufficient"
        if assessment["outcome"] != expected:
            raise ValueError("Exact quote outcome does not reconstruct")
        if present and not any(c["passage"] == check["claim"]["text"] for c in citations):
            raise ValueError("Exact quotation must be cited exactly")
    context = check["context_ref"]
    if context is not None:
        fields(context, "profile_id version sha256 approval_status")
        text(context["profile_id"], 160)
        if type(context["version"]) is not int or context["version"] < 1:
            raise ValueError("Invalid context version")
        if not trust._valid_sha256(context["sha256"]) or context["approval_status"] != "approved":
            raise ValueError("Only a separately approved context reference may be recorded")
    limitations = check["limitations"]
    if not isinstance(limitations, list) or not 1 <= len(limitations) <= 8:
        raise ValueError("Evidence limitations required")
    for item in limitations:
        text(item, 1000)
    # Also reject non-JSON values, non-finite floats and invalid Unicode.
    trust.canonical_json(check)
    return check


def export(check):
    validate(check)
    record = trust.new_record({"kind": "evidence_check", "identifier": check["id"]})
    trust.append_entry(record, "evidence_recorded", payload={
        "event": "evidence_check_recorded", "check": copy.deepcopy(check),
        "interpretation_limit": LIMITATION})
    checkpoint = {"record_id": record["record_id"], "entry_count": len(record["entries"]),
                  "head_hash": record["entries"][-1]["entry_hash"]}
    return {"schema": BUNDLE_SCHEMA, "record": record, "checkpoint": checkpoint}


def verify(bundle, retained_checkpoint):
    """Reconstruct an exported check against a separately retained checkpoint.

    The supplied checkpoint is a caller trust input, not authenticated here.
    Semantic model judgments and context approval claims are still assertions.
    """
    try:
        fields(bundle, "schema record checkpoint")
        fields(retained_checkpoint, "record_id entry_count head_hash")
        record = bundle["record"]
        if bundle["schema"] != BUNDLE_SCHEMA or not trust.chain_valid(record):
            return False
        if type(retained_checkpoint["entry_count"]) is not int or retained_checkpoint["entry_count"] != 1:
            return False
        entries = record["entries"]
        if len(entries) != 1 or bundle["checkpoint"] != retained_checkpoint:
            return False
        actual = {"record_id": record["record_id"], "entry_count": len(entries),
                  "head_hash": entries[-1]["entry_hash"]}
        if actual != retained_checkpoint or entries[0]["entry_type"] != "evidence_recorded":
            return False
        payload = entries[0]["payload"]
        fields(payload, "event check interpretation_limit")
        if payload["event"] != "evidence_check_recorded" or payload["interpretation_limit"] != LIMITATION:
            return False
        check = validate(payload["check"])
        return record["subject"] == {"kind": "evidence_check", "identifier": check["id"]}
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, AttributeError):
        return False


def main(argv=None):
    """Verify files offline; the checkpoint must be independently retained."""
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Reconstruct evidence integrity, not factual truth.")
    parser.add_argument("bundle", help="Exported evidence check bundle JSON")
    parser.add_argument("checkpoint", help="Separately retained checkpoint JSON")
    args = parser.parse_args(argv)

    def read_json(path, limit):
        with Path(path).open("rb") as f:
            raw = f.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("File allowance exceeded")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate JSON field")
                value[key] = item
            return value
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    try:
        bundle = read_json(args.bundle, 1048576)
        checkpoint = read_json(args.checkpoint, 2048)
    except (OSError,ValueError,UnicodeError,RecursionError):
        print("Evidence files could not be read as bounded, unambiguous JSON.")
        return 2
    if not verify(bundle, checkpoint):
        print("Evidence integrity does not match the retained checkpoint.")
        return 1
    print("Evidence integrity matches the retained checkpoint. Source truth, model judgments and context approval remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
