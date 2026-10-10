"""verity_core.trust: append-only, hash-chained trust records (v0.1).

Plain, JSON-serializable dicts that reference existing evidence structures
(media_evidence_package/v1, artifact/observation ids, reports, and optionally
a Canonical Case). Self-contained: imports nothing from other core modules.

Portability contract: standard library only; plain dicts in and out; a record
round-trips through JSON as its storage format.

Integrity limits: records are tamper-evident, not tamper-proof; not
authenticated; not Byzantine-resilient; not immutable storage. A full rewrite
with recomputed hashes (or removal of a trailing suffix of entries) passes
every check in this module absent an externally retained trusted anchor. See
record.py for the full statement of limits.
"""

from verity_core.trust.record import (
    ACTOR_SYSTEM,
    ENTRY_TYPES,
    GENESIS_PREV_HASH,
    SCHEMA,
    append_entry,
    canonical_json,
    chain_valid,
    entry_hash,
    new_record,
    validate,
)

__all__ = [
    "ACTOR_SYSTEM",
    "ENTRY_TYPES",
    "GENESIS_PREV_HASH",
    "SCHEMA",
    "append_entry",
    "canonical_json",
    "chain_valid",
    "entry_hash",
    "new_record",
    "validate",
]
