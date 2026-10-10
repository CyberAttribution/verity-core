"""trust_record/v0.1: append-only, hash-chained lifecycle records.

Product-neutral evidence plumbing: a record is a plain, JSON-serializable dict
that accumulates entries referencing existing evidence structures --
media_evidence_package/v1, artifact/observation ids, reports, and optionally a
Canonical Case. This module references evidence; it never embeds or mutates it.

Portability contract (mirrors media_ingest): plain dicts in and out, standard
library only, no framework, no network, no filesystem. A record round-trips
through JSON as its storage format.

Integrity limits (explicit, by design):

- The hash chain detects changes only relative to entry hashes produced by
  this module's canonical serialization. It does NOT prove that referenced
  evidence is true, that a recorded actor is authentic, or provide immutable
  storage or Byzantine-fault-tolerance guarantees.
- A writer who rewrites the whole chain and recomputes every entry hash, or
  who removes a trailing suffix of entries, passes every check in this module.
  Detecting those cases requires an externally retained trusted anchor (a
  known head hash and/or entry count), which v0.1 does not provide.
  Signatures, external anchoring, durable storage, and authenticated actors
  are future work; their absence here is documented, not hidden.
- A Python append API is not immutable storage. The module enforces: hash
  correctness of every entry, structural and JSON validation of the whole
  record, and isolation of stored entries from caller-owned mutable inputs
  (deep copies on write; append_entry returns a copy). append_entry refuses to
  touch a record whose existing content fails validation or chain
  verification, so a failed append leaves the record unchanged. It cannot
  enforce append-only usage once the caller holds the record dict: direct
  edits to stored entries are detected by chain_valid(), while subject, refs,
  and created_utc are not hash-protected in v0.1 (no record-level digest), so
  edits to them are undetected. The record_id is bound into every entry's
  hash, so changing it on a record with entries breaks chain_valid; an empty
  record's metadata is unbound and edits to it are undetected.
- Records are therefore tamper-evident, not tamper-proof; not authenticated;
  not Byzantine-resilient; and not proof of the trustworthiness of anything
  they describe.

Canonical serialization: canonical_json() accepts only JSON-supported values
(dict/list/str/int/finite-float/bool/None, with string object keys), rejecting
non-string object keys, non-finite floats (NaN/Infinity), and strings that are
not encodable as UTF-8 (lone surrogates) instead of emitting ambiguous or
non-standard JSON. Encoding is deterministic UTF-8 with sorted keys and
compact separators. No numeric normalization is performed: 1 and 1.0
serialize distinctly, by design.

Timestamps use the core UTC format YYYY-MM-DDTHH:MM:SSZ and are validated as
real calendar dates, not merely by shape.
"""

import copy
import datetime
import hashlib
import json
import math
import re
import time
import uuid

SCHEMA = "trust_record/v0.1"

ENTRY_TYPES = (
    "evidence_recorded",
    "analysis_recorded",
    "validation_passed",
    "validation_failed",
    "approval_granted",
    "action_attempted",
    "action_result",
    "action_reversed",
    "note",
)

GENESIS_PREV_HASH = "0" * 64
ACTOR_SYSTEM = "system"

_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_RECORD_ID_RE = re.compile(r"tr-[0-9a-f]{32}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

_SUBJECT_KIND_MAX = 100
_SUBJECT_IDENTIFIER_MAX = 300
_ACTOR_MAX = 120

_HASH_FIELDS = (
    "record_id",
    "seq",
    "at_utc",
    "actor",
    "entry_type",
    "payload",
    "prev_entry_hash",
)


def _utc():
    """Current time in the core UTC format (second resolution)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _valid_utc(value):
    """True only for a real calendar date in the core UTC format."""
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _valid_record_id(value):
    return isinstance(value, str) and bool(_RECORD_ID_RE.fullmatch(value))


def _valid_sha256(value):
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _check_string(value, path):
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{path}: string is not encodable as UTF-8") from exc


def _check_json_value(value, path):
    """Raise ValueError unless value is a supported JSON value.

    Supported: None, bool, int, finite float, str (UTF-8 encodable), list,
    and dict with string keys. Rejected: non-string object keys, non-finite
    floats, lone-surrogate strings, and every non-JSON type (set, tuple,
    bytes, ...) rather than accepting ambiguous or non-standard JSON.
    """
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        _check_string(value, path)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite float is not valid JSON")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"{path}: object keys must be strings, got {type(key).__name__}"
                )
            _check_string(key, path)
            _check_json_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path}: unsupported type {type(value).__name__}")


def canonical_json(obj):
    """Deterministic UTF-8 JSON encoding: sorted keys, compact separators."""
    _check_json_value(obj, "$")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(entry):
    """SHA-256 over the canonical JSON of the exact hashed field set.

    Covers record_id (binds the entry to its record: entry transplant across
    records breaks the chain), seq, at_utc, actor, entry_type, payload in
    full, and prev_entry_hash. Excludes entry_hash itself and any unknown
    extra keys, so a future writer may add entry fields without breaking
    v0.1 verification.
    """
    fields = {name: entry[name] for name in _HASH_FIELDS}
    return hashlib.sha256(canonical_json(fields).encode("utf-8")).hexdigest()


def new_record(subject, refs=None, record_id=None, created_utc=None):
    """Create an empty trust record. Fails fast on malformed input.

    subject: {"kind": str, "identifier": str, "source_sha256": optional
    lowercase 64-hex SHA-256}. refs: optional dict of open reference dicts
    (e.g. {"evidence": {...}, "case": {...}, "report": {...}}); values must be
    dicts whose optional source_sha256/sha256 values are lowercase 64-hex
    SHA-256 digests; all values must be JSON-supported. record_id: minted as
    "tr-" + full uuid4 hex (32 chars) unless supplied. created_utc must be a
    real UTC datetime. Subject and refs are deep-copied, so later mutation of
    the caller's dicts cannot alter the record. Constructors only return
    records that validate() accepts and chain_valid() verifies.
    """
    if not isinstance(subject, dict):
        raise ValueError("subject must be a dict")
    kind = subject.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError("subject.kind must be a non-empty string")
    if len(kind) > _SUBJECT_KIND_MAX:
        raise ValueError(f"subject.kind must be at most {_SUBJECT_KIND_MAX} characters")
    identifier = subject.get("identifier")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("subject.identifier must be a non-empty string")
    if len(identifier) > _SUBJECT_IDENTIFIER_MAX:
        raise ValueError(
            f"subject.identifier must be at most {_SUBJECT_IDENTIFIER_MAX} characters"
        )
    source_sha256 = subject.get("source_sha256")
    if source_sha256 is not None and not _valid_sha256(source_sha256):
        raise ValueError("subject.source_sha256 must be a lowercase 64-hex SHA-256 digest")
    _check_json_value(subject, "subject")

    if refs is None:
        refs = {}
    if not isinstance(refs, dict):
        raise ValueError("refs must be a dict")
    for key, value in refs.items():
        if not isinstance(value, dict):
            raise ValueError(f"refs.{key} must be a dict")
        for digest_key in ("source_sha256", "sha256"):
            digest = value.get(digest_key)
            if digest is not None and not _valid_sha256(digest):
                raise ValueError(
                    f"refs.{key}.{digest_key} must be a lowercase 64-hex SHA-256 digest"
                )
    _check_json_value(refs, "refs")

    if record_id is None:
        record_id = f"tr-{uuid.uuid4().hex}"
    elif not _valid_record_id(record_id):
        raise ValueError('record_id must match "tr-" plus 32 lowercase hex characters')

    if created_utc is None:
        created_utc = _utc()
    elif not _valid_utc(created_utc):
        raise ValueError(
            "created_utc must be a real UTC datetime formatted YYYY-MM-DDTHH:MM:SSZ"
        )

    return {
        "schema": SCHEMA,
        "record_id": record_id,
        "created_utc": created_utc,
        "subject": copy.deepcopy(subject),
        "refs": copy.deepcopy(refs),
        "entries": [],
    }


def _structural_issues(record):
    """Structural and JSON validation; returns issue strings ([] means valid).

    Never raises. Unknown top-level and entry keys are tolerated (forward
    compatibility) but must hold JSON-supported values. The entry vocabulary
    is pinned to the schema version, so a future reader identifies the format
    and rejects unsupported data clearly.
    """
    if not isinstance(record, dict):
        return ["record is not an object"]
    issues = []
    try:
        _check_json_value(record, "$")
    except ValueError as exc:
        issues.append(f"record is not valid JSON: {exc}")
    if record.get("schema") != SCHEMA:
        issues.append(f"schema must be {SCHEMA!r}")
    record_id = record.get("record_id")
    record_id_ok = _valid_record_id(record_id)
    if not record_id_ok:
        issues.append('record_id must match "tr-" plus 32 lowercase hex characters')
    if not _valid_utc(record.get("created_utc")):
        issues.append(
            "created_utc must be a real UTC datetime formatted YYYY-MM-DDTHH:MM:SSZ"
        )

    subject = record.get("subject")
    if not isinstance(subject, dict):
        issues.append("subject must be an object")
    else:
        kind = subject.get("kind")
        if not isinstance(kind, str) or not kind:
            issues.append("subject.kind must be a non-empty string")
        elif len(kind) > _SUBJECT_KIND_MAX:
            issues.append(f"subject.kind must be at most {_SUBJECT_KIND_MAX} characters")
        identifier = subject.get("identifier")
        if not isinstance(identifier, str) or not identifier:
            issues.append("subject.identifier must be a non-empty string")
        elif len(identifier) > _SUBJECT_IDENTIFIER_MAX:
            issues.append(
                f"subject.identifier must be at most {_SUBJECT_IDENTIFIER_MAX} characters"
            )
        source_sha256 = subject.get("source_sha256")
        if source_sha256 is not None and not _valid_sha256(source_sha256):
            issues.append("subject.source_sha256 must be a lowercase 64-hex SHA-256 digest")

    refs = record.get("refs")
    if not isinstance(refs, dict):
        issues.append("refs must be an object")
    else:
        for key, value in refs.items():
            if not isinstance(value, dict):
                issues.append(f"refs.{key} must be an object")
                continue
            for digest_key in ("source_sha256", "sha256"):
                digest = value.get(digest_key)
                if digest is not None and not _valid_sha256(digest):
                    issues.append(
                        f"refs.{key}.{digest_key} must be a lowercase 64-hex SHA-256 digest"
                    )

    entries = record.get("entries")
    if not isinstance(entries, list):
        issues.append("entries must be a list")
    else:
        for index, entry in enumerate(entries):
            prefix = f"entries[{index}]"
            if not isinstance(entry, dict):
                issues.append(f"{prefix} must be an object")
                continue
            seq = entry.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq != index + 1:
                issues.append(f"{prefix}.seq must equal {index + 1}")
            if record_id_ok and entry.get("record_id") != record_id:
                issues.append(f"{prefix}.record_id must match the record's record_id")
            if not _valid_utc(entry.get("at_utc")):
                issues.append(
                    f"{prefix}.at_utc must be a real UTC datetime formatted YYYY-MM-DDTHH:MM:SSZ"
                )
            actor = entry.get("actor")
            if not isinstance(actor, str) or not actor:
                issues.append(f"{prefix}.actor must be a non-empty string")
            elif len(actor) > _ACTOR_MAX:
                issues.append(f"{prefix}.actor must be at most {_ACTOR_MAX} characters")
            if entry.get("entry_type") not in ENTRY_TYPES:
                issues.append(f"{prefix}.entry_type must be one of {', '.join(ENTRY_TYPES)}")
            if not isinstance(entry.get("payload"), dict):
                issues.append(f"{prefix}.payload must be an object")
            for hash_key in ("prev_entry_hash", "entry_hash"):
                if not _valid_sha256(entry.get(hash_key)):
                    issues.append(f"{prefix}.{hash_key} must be a lowercase 64-hex digest")
    return issues


def _chain_recomputes(record):
    """True only if every entry hash and linkage recomputes exactly."""
    previous = GENESIS_PREV_HASH
    for entry in record["entries"]:
        if entry.get("prev_entry_hash") != previous:
            return False
        if entry_hash(entry) != entry.get("entry_hash"):
            return False
        previous = entry["entry_hash"]
    return True


def append_entry(record, entry_type, actor=ACTOR_SYSTEM, payload=None, at_utc=None):
    """Append one entry to record's hash chain; return a deep copy of it.

    Fails fast on malformed input: unknown entry types, malformed records,
    unsupported payload values, and malformed actors/timestamps raise
    ValueError, so a record can never hold an entry its own chain cannot
    validate. The record's existing content is fully validated and verified
    before any mutation, so a failed append leaves the record unchanged, and
    only records that validate() accepts and chain_valid() verifies accept
    new entries.

    Enforcement boundary: the stored payload is deep-copied and the returned
    entry is a deep copy, so mutating caller inputs or the returned entry
    cannot alter what was hashed. The record dict itself remains caller-owned;
    direct edits to stored entries are detectable via chain_valid(), while
    edits to subject, refs, and created_utc are not hash-protected in v0.1
    (see module docstring; record_id is bound into every entry hash, so
    editing it on a record with entries is detected).
    """
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"entry_type must be one of {', '.join(ENTRY_TYPES)}")
    if not isinstance(actor, str) or not actor:
        raise ValueError("actor must be a non-empty string")
    if len(actor) > _ACTOR_MAX:
        raise ValueError(f"actor must be at most {_ACTOR_MAX} characters")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    _check_json_value(payload, "payload")
    if at_utc is None:
        at_utc = _utc()
    elif not _valid_utc(at_utc):
        raise ValueError("at_utc must be a real UTC datetime formatted YYYY-MM-DDTHH:MM:SSZ")

    if not isinstance(record, dict):
        raise ValueError("record must be a trust_record/v0.1 dict")
    issues = _structural_issues(record)
    if issues:
        raise ValueError("cannot append to an invalid record: " + "; ".join(issues))
    if not _chain_recomputes(record):
        raise ValueError("cannot append: the record's existing chain fails verification")

    entries = record["entries"]
    prev_entry_hash = entries[-1]["entry_hash"] if entries else GENESIS_PREV_HASH
    entry = {
        "record_id": record["record_id"],
        "seq": len(entries) + 1,
        "at_utc": at_utc,
        "actor": actor,
        "entry_type": entry_type,
        "payload": copy.deepcopy(payload),
        "prev_entry_hash": prev_entry_hash,
    }
    entry["entry_hash"] = entry_hash(entry)
    entries.append(entry)
    return copy.deepcopy(entry)


def validate(record):
    """Structural and JSON validation; returns issue strings ([] means valid).

    Never raises: malformed input yields issues, mirroring package.validate.
    Unknown top-level and entry keys are tolerated (forward compatibility)
    but must hold JSON-supported values. The entry vocabulary is pinned to
    the schema version, so a future reader identifies the format and rejects
    unsupported data clearly.
    """
    try:
        return _structural_issues(record)
    except Exception as exc:
        return [f"validation error: {exc}"]


def chain_valid(record):
    """Cryptographic chain verification; True only if the record passes full
    structural and JSON validation AND every entry hash and linkage recomputes
    exactly. Never raises; False on any doubt.

    Limit: detects tampering only relative to hashes produced by this module.
    A complete rewrite with recomputed hashes, or removal of a trailing
    suffix of entries, passes here absent an externally retained trusted
    anchor (see module docstring).
    """
    try:
        if not isinstance(record, dict):
            return False
        if _structural_issues(record):
            return False
        return _chain_recomputes(record)
    except Exception:
        return False
