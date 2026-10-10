"""Unit tests for verity_core.trust (trust_record/v0.1)."""

import copy
import json

import pytest

from verity_core.trust import (
    ACTOR_SYSTEM,
    GENESIS_PREV_HASH,
    SCHEMA,
    append_entry,
    canonical_json,
    chain_valid,
    entry_hash,
    new_record,
    validate,
)


def _subject(**overrides):
    subject = {
        "kind": "video",
        "identifier": "dQw4w9WgXcQ",
        "source_sha256": "a" * 64,
    }
    subject.update(overrides)
    return {key: value for key, value in subject.items() if value is not None}


def _valid_record():
    record = new_record(_subject(), refs={"case": {"case_id": "case-2024-001"}})
    append_entry(
        record,
        "evidence_recorded",
        actor="media-ingest",
        payload={"evidence_digest": "sha256:" + "b" * 64},
    )
    return record


def test_record_shape():
    record = new_record(_subject())
    assert record["schema"] == SCHEMA == "trust_record/v0.1"
    assert record["entries"] == []
    assert set(record) == {
        "schema",
        "record_id",
        "created_utc",
        "subject",
        "refs",
        "entries",
    }
    assert record["subject"] == _subject()
    assert validate(record) == []
    assert chain_valid(record) is True


def test_record_id_full_entropy():
    first = new_record(_subject())
    second = new_record(_subject())
    for record in (first, second):
        assert record["record_id"].startswith("tr-")
        suffix = record["record_id"][3:]
        assert len(suffix) == 32
        int(suffix, 16)
    assert first["record_id"] != second["record_id"]
    assert validate(first) == []


def test_record_id_override_and_rejection():
    fixed = "tr-" + "0123abcd" * 4
    record = new_record(_subject(), record_id=fixed)
    assert record["record_id"] == fixed
    with pytest.raises(ValueError):
        new_record(_subject(), record_id="tr-short")
    with pytest.raises(ValueError):
        new_record(_subject(), record_id=fixed.upper())
    with pytest.raises(ValueError):
        new_record(_subject(), record_id=fixed + "\n")


def test_append_entry_chain():
    record = _valid_record()
    entries = record["entries"]
    assert len(entries) == 1
    first = entries[0]
    assert first["seq"] == 1
    assert first["prev_entry_hash"] == GENESIS_PREV_HASH == "0" * 64
    assert first["actor"] == "media-ingest"
    assert first["payload"] == {"evidence_digest": "sha256:" + "b" * 64}
    assert first["record_id"] == record["record_id"]
    second = append_entry(
        record, "validation_passed", actor="qa", payload={"checked": True}
    )
    assert second["seq"] == 2
    assert second["prev_entry_hash"] == first["entry_hash"]
    assert len(record["entries"]) == 2
    assert chain_valid(record) is True
    assert validate(record) == []


def test_append_defaults():
    record = new_record(_subject())
    entry = append_entry(record, "note")
    assert entry["actor"] == ACTOR_SYSTEM == "system"
    assert entry["payload"] == {}
    assert entry["entry_type"] == "note"
    assert validate(record) == []


def test_chain_valid_after_each_append():
    record = new_record(_subject())
    for index, entry_type in enumerate(
        [
            "evidence_recorded",
            "analysis_recorded",
            "validation_passed",
            "action_attempted",
        ]
    ):
        append_entry(record, entry_type, payload={"step": index})
        assert chain_valid(record) is True
        assert validate(record) == []


def test_tampered_payload_detected():
    record = _valid_record()
    record["entries"][0]["payload"]["evidence_digest"] = "sha256:" + "c" * 64
    assert chain_valid(record) is False


def test_tampered_hash_detected():
    record = _valid_record()
    record["entries"][0]["entry_hash"] = "d" * 64
    assert validate(record) == []
    assert chain_valid(record) is False


def test_reordered_entries_detected():
    record = _valid_record()
    append_entry(record, "validation_passed", payload={"ok": True})
    record["entries"].reverse()
    assert chain_valid(record) is False


def test_suffix_removal_limit_is_documented():
    record = _valid_record()
    append_entry(record, "validation_passed", payload={"ok": True})
    record["entries"].pop()
    assert chain_valid(record) is True


def test_unknown_entry_type_rejected():
    record = new_record(_subject())
    with pytest.raises(ValueError):
        append_entry(record, "bogus_entry_type")
    assert record["entries"] == []


def test_payload_rejects_unsupported_values():
    record = new_record(_subject())
    cases = [
        "not-a-dict",
        ["not", "a", "dict"],
        {1: "non-string key"},
        {"nested": {1: "non-string key"}},
        {"x": float("nan")},
        {"x": float("inf")},
        {"nested": {"y": float("-inf")}},
        {"x": (1, 2)},
        {"x": b"bytes"},
        {"x": {"y": {2, 3}}},
        {"x": "\ud800"},
    ]
    for payload in cases:
        with pytest.raises(ValueError):
            append_entry(record, "note", payload=payload)
    assert record["entries"] == []


def test_finite_floats_supported():
    record = new_record(_subject())
    append_entry(record, "analysis_recorded", payload={"score": 0.75, "count": 3})
    assert chain_valid(record) is True
    assert validate(record) == []


def test_canonical_json_deterministic():
    one = canonical_json({"b": 1, "a": {"y": 2, "x": [3, 4]}})
    two = canonical_json({"a": {"x": [3, 4], "y": 2}, "b": 1})
    assert one == two == '{"a":{"x":[3,4],"y":2},"b":1}'
    assert one == canonical_json(json.loads(one))


def test_canonical_json_rejects_ambiguous_json():
    with pytest.raises(ValueError):
        canonical_json({1: "non-string key"})
    with pytest.raises(ValueError):
        canonical_json({"nested": {2.5: "non-string key"}})
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
    with pytest.raises(ValueError):
        canonical_json({"x": float("inf")})
    with pytest.raises(ValueError):
        canonical_json({"x": "\ud800"})
    with pytest.raises(ValueError):
        canonical_json({"\ud800": "key"})


def test_entry_hash_stable_across_key_order():
    base = {
        "record_id": "tr-" + "ab" * 16,
        "seq": 1,
        "at_utc": "2024-01-01T00:00:00Z",
        "actor": "system",
        "entry_type": "note",
        "payload": {"b": 1, "a": 2},
        "prev_entry_hash": GENESIS_PREV_HASH,
    }
    reordered = dict(reversed(list(base.items())))
    assert entry_hash(base) == entry_hash(reordered)
    swapped = dict(base, payload={"a": 2, "b": 1})
    assert entry_hash(base) == entry_hash(swapped)
    int_payload = dict(base, payload={"n": 1})
    float_payload = dict(base, payload={"n": 1.0})
    assert canonical_json({"n": 1}) == '{"n":1}'
    assert canonical_json({"n": 1.0}) == '{"n":1.0}'
    assert entry_hash(int_payload) != entry_hash(float_payload)


def test_validate_never_raises():
    garbage = [
        None,
        42,
        "a string",
        [],
        {},
        {"schema": "trust_record/v0.1"},
        {"schema": SCHEMA, "record_id": "tr-x", "subject": 7, "refs": 9, "entries": "no"},
    ]
    for item in garbage:
        issues = validate(item)
        assert isinstance(issues, list)
        assert all(isinstance(issue, str) for issue in issues)


def test_chain_valid_false_on_garbage():
    garbage = [
        None,
        42,
        "a string",
        [],
        {},
        {"record_id": "tr-" + "ab" * 16, "entries": "no"},
    ]
    for item in garbage:
        assert chain_valid(item) is False


def test_chain_valid_requires_full_structural_validity():
    record = new_record(_subject())
    broken = copy.deepcopy(record)
    broken["schema"] = "bogus"
    assert validate(broken) != []
    assert chain_valid(broken) is False
    assert chain_valid({}) is False
    nan_record = copy.deepcopy(record)
    nan_record["subject"]["extra"] = float("nan")
    assert validate(nan_record) != []
    assert chain_valid(nan_record) is False
    bool_seq = _valid_record()
    bool_seq["entries"][0]["seq"] = True
    assert chain_valid(bool_seq) is False


def test_duplicate_and_broken_seq_detected():
    record = _valid_record()
    append_entry(record, "note")
    record["entries"][1]["seq"] = 1
    assert chain_valid(record) is False
    assert any("seq" in issue for issue in validate(record))
    record["entries"][1]["seq"] = True
    assert any("seq" in issue for issue in validate(record))
    assert chain_valid(record) is False


def test_transplanted_entry_breaks_chain():
    one = _valid_record()
    two = _valid_record()
    assert one["record_id"] != two["record_id"]
    two["entries"] = [copy.deepcopy(one["entries"][0])]
    assert two["entries"][0]["seq"] == 1
    assert chain_valid(two) is False
    assert any("record_id" in issue for issue in validate(two))


def test_new_record_rejects_malformed_subject_and_refs():
    with pytest.raises(ValueError):
        new_record({"kind": "", "identifier": "x"})
    with pytest.raises(ValueError):
        new_record({"kind": "video"})
    with pytest.raises(ValueError):
        new_record({"kind": "video", "identifier": ""})
    with pytest.raises(ValueError):
        new_record({"kind": "x" * 101, "identifier": "x"})
    with pytest.raises(ValueError):
        new_record({"kind": "video", "identifier": "x" * 301})
    with pytest.raises(ValueError):
        new_record({"kind": "video", "identifier": "x", "source_sha256": "A" * 64})
    with pytest.raises(ValueError):
        new_record("video")
    with pytest.raises(ValueError):
        new_record(_subject(), refs={"case": "not-a-dict"})
    with pytest.raises(ValueError):
        new_record(_subject(), created_utc="2024-01-01 00:00:00")
    with pytest.raises(ValueError):
        new_record(_subject(), created_utc="2024-02-30T00:00:00Z")


def test_new_record_rejects_bad_ref_digests():
    with pytest.raises(ValueError):
        new_record(_subject(), refs={"evidence": {"sha256": "bad"}})
    with pytest.raises(ValueError):
        new_record(_subject(), refs={"evidence": {"source_sha256": "b" * 63}})
    with pytest.raises(ValueError):
        new_record(_subject(), refs={"evidence": {"sha256": "b" * 64 + "\n"}})
    with pytest.raises(ValueError):
        new_record(_subject(), refs={"evidence": {"note": float("inf")}})


def test_new_record_rejects_non_json_values():
    with pytest.raises(ValueError):
        new_record(_subject(extra=float("nan")))
    with pytest.raises(ValueError):
        new_record(_subject(), refs={"evidence": {"sha256": "b" * 64}, "extra": "\ud800"})


def test_validate_flags_malformed_subject_and_refs():
    record = new_record(_subject())
    record["subject"]["source_sha256"] = "zz"
    assert any("source_sha256" in issue for issue in validate(record))
    record["refs"]["case"] = "not-a-dict"
    assert any("refs" in issue for issue in validate(record))
    record["refs"] = {"case": {"sha256": "b" * 63}}
    assert any("sha256" in issue for issue in validate(record))


def test_validation_failed_entry_is_valid():
    record = new_record(_subject())
    append_entry(
        record, "validation_failed", actor="qa", payload={"reason": "checksum mismatch"}
    )
    assert chain_valid(record) is True
    assert validate(record) == []


def test_mutation_isolation():
    subject = _subject()
    refs = {"case": {"case_id": "case-2024-001"}}
    payload = {"evidence_digest": "sha256:" + "b" * 64}
    record = new_record(subject, refs=refs)
    subject["identifier"] = "MUTATED"
    refs["case"]["case_id"] = "MUTATED"
    assert record["subject"]["identifier"] == "dQw4w9WgXcQ"
    assert record["refs"]["case"]["case_id"] == "case-2024-001"

    entry = append_entry(record, "evidence_recorded", actor="ingest", payload=payload)
    payload["evidence_digest"] = "MUTATED"
    assert record["entries"][0]["payload"] == {"evidence_digest": "sha256:" + "b" * 64}
    assert chain_valid(record) is True

    entry["payload"]["evidence_digest"] = "MUTATED"
    entry["entry_type"] = "note"
    assert record["entries"][0]["entry_type"] == "evidence_recorded"
    assert record["entries"][0]["payload"]["evidence_digest"] == "sha256:" + "b" * 64
    assert chain_valid(record) is True

    record["entries"][0]["actor"] = "attacker"
    assert chain_valid(record) is False


def test_append_refuses_invalid_record_and_leaves_it_unchanged():
    record = _valid_record()
    record["entries"][0]["payload"]["evidence_digest"] = "sha256:" + "c" * 64
    snapshot = copy.deepcopy(record)
    with pytest.raises(ValueError):
        append_entry(record, "note")
    assert record == snapshot
    assert canonical_json(record) == canonical_json(snapshot)
    record["entries"][0]["payload"]["evidence_digest"] = "sha256:" + "b" * 64
    append_entry(record, "note")
    assert chain_valid(record) is True

    other = _valid_record()
    other["subject"]["identifier"] = ""
    other_snapshot = copy.deepcopy(other)
    with pytest.raises(ValueError):
        append_entry(other, "note")
    assert other == other_snapshot


def test_utc_timestamps_validated_as_calendar_dates():
    with pytest.raises(ValueError):
        new_record(_subject(), created_utc="2023-02-29T00:00:00Z")
    record = new_record(_subject(), created_utc="2024-02-29T12:34:56Z")
    assert record["created_utc"] == "2024-02-29T12:34:56Z"
    with pytest.raises(ValueError):
        new_record(_subject(), created_utc="2024-13-01T00:00:00Z")
    with pytest.raises(ValueError):
        new_record(_subject(), created_utc="2024-01-01T24:00:00Z")
    with pytest.raises(ValueError):
        new_record(_subject(), created_utc="2024-01-01T00:00:00Z\n")
    with pytest.raises(ValueError):
        append_entry(record, "note", at_utc="2024-04-31T00:00:00Z")
    with pytest.raises(ValueError):
        append_entry(record, "note", at_utc="2024-01-01T00:00:00Z\n")


def test_fullmatch_rejects_trailing_newline_in_hashes():
    record = _valid_record()
    record["entries"][0]["entry_hash"] += "\n"
    assert any("entry_hash" in issue for issue in validate(record))
    assert chain_valid(record) is False


def test_metadata_edit_detection_boundaries():
    record = _valid_record()
    original_id = record["record_id"]
    record["record_id"] = "tr-" + "ff" * 16
    assert chain_valid(record) is False
    record["record_id"] = original_id
    assert chain_valid(record) is True
    record["subject"]["identifier"] = "edited"
    assert chain_valid(record) is True
    record["created_utc"] = "2020-01-01T00:00:00Z"
    assert chain_valid(record) is True
    empty = new_record(_subject())
    empty["record_id"] = "tr-" + "ee" * 16
    assert chain_valid(empty) is True


def test_json_roundtrip_and_evidence_non_mutated():
    evidence = {"schema": "media_evidence_package/v1", "package_id": "mep-1"}
    record = new_record(_subject(), refs={"evidence": evidence})
    append_entry(
        record, "evidence_recorded", payload={"evidence_digest": "sha256:" + "b" * 64}
    )
    snapshot = copy.deepcopy(record)
    restored = json.loads(json.dumps(record))
    assert restored == snapshot
    assert validate(restored) == []
    assert chain_valid(restored) is True
    assert evidence == {"schema": "media_evidence_package/v1", "package_id": "mep-1"}


def test_actor_verbatim_and_limits():
    record = new_record(_subject())
    actor = "service:media-ingest@host-7"
    append_entry(record, "note", actor=actor)
    assert record["entries"][0]["actor"] == actor
    with pytest.raises(ValueError):
        append_entry(record, "note", actor="a" * 121)
    with pytest.raises(ValueError):
        append_entry(record, "note", actor="")
    with pytest.raises(ValueError):
        append_entry(record, "note", at_utc="2024-01-01 00:00:00")
    assert validate(record) == []
