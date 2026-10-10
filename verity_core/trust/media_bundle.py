"""Consumer bridge: media evidence packages bound into Trust Records.

The single serialization artifact is one versioned JSON bundle:

    {
      "schema": "trust_media_bundle/v0.1",
      "package": { ...media_evidence_package/v1... },
      "record":  { ...trust_record/v0.1... }
    }

The record's evidence_recorded entry payload carries the hash-protected
binding; the header refs.evidence field carries an advisory copy. Binding is
the SHA-256 of the FULL canonical JSON of the package (canonical_json sorts
keys), so any byte-relevant change to any field is detected. The bounded
evidence_digest text is intentionally NOT part of the binding: it renders
declared/embedded metadata in dict insertion order (package.py), so it is
not a canonical function of package content and can differ between an
in-memory package and its saved form. It remains the pipeline rendering;
this is recorded as an upstream review finding, not fixed here.

Integrity limits (unchanged from the record module): verification is
relative to hashes retained inside the bundle only. A complete rewrite with
recomputed hashes, or removal of a trailing suffix of entries, passes; a
bundle establishes consistency of the record and package against the hashes
embedded in the bundle itself — not who produced them, and not that the
source media is authentic.
"""

import copy
import json
import os
import tempfile

from verity_core.media_ingest import package
from verity_core.trust import record

BUNDLE_SCHEMA = "trust_media_bundle/v0.1"
DISCLAIMER = (
    "This bundle verifies only that the embedded package and record match "
    "the hashes retained inside the bundle itself. It does not verify "
    "authorship, uploader identity, or the authenticity of the underlying "
    "media."
)

_SUBJECT_KIND = "media_evidence"
_HEX = set("0123456789abcdef")


def _valid_sha256(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _sha256_text(text):
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- strict JSON handling (load_bundle only; never partial) -----------------


def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name!r} is not permitted")


def _finite_float(text):
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"non-finite number {text!r} is not permitted")
    return value


def _object_pairs(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON object key {key!r}")
        seen.add(key)
    return dict(pairs)


def strict_loads(text):
    """json.loads with duplicate keys, NaN/Infinity, and overflow floats
    rejected. Raises ValueError on any of these."""
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except RecursionError:
        raise ValueError("JSON nesting exceeds the supported depth") from None


# --- consumer-side package shape checks (additive; upstream validate is
#     consistency-only and untouched) ----------------------------------------


def _package_shape_issues(pkg):
    """Consumer shape checks for the documented package constructor fields.

    Returns issue strings ([] means the shape is usable). Never raises, and
    deliberately stricter than package.validate about container types, since
    this module indexes into these structures.
    """
    issues = []
    if not isinstance(pkg, dict):
        return ["package must be a JSON object"]
    if pkg.get("schema") != package.SCHEMA:
        issues.append(f"package.schema must be {package.SCHEMA!r}")
    src = pkg.get("source")
    if not isinstance(src, dict):
        issues.append("package.source must be an object")
    else:
        for key in ("kind", "identifier", "acquisition_time_utc"):
            if not isinstance(src.get(key), str):
                issues.append(f"package.source.{key} must be a string")
        if not isinstance(src.get("declared_untrusted"), dict):
            issues.append("package.source.declared_untrusted must be an object")
    arts = pkg.get("artifacts")
    if not isinstance(arts, list):
        issues.append("package.artifacts must be a list")
    else:
        for i, art in enumerate(arts):
            if not isinstance(art, dict):
                issues.append(f"package.artifacts[{i}] must be an object")
                continue
            for key in ("artifact_id", "kind", "role"):
                if not isinstance(art.get(key), str):
                    issues.append(f"package.artifacts[{i}].{key} must be a string")
            if art.get("role") not in ("source", "derived"):
                issues.append(
                    f"package.artifacts[{i}].role must be 'source' or 'derived'"
                )
            sha = art.get("sha256")
            if sha is not None and not _valid_sha256(sha):
                issues.append(
                    f"package.artifacts[{i}].sha256 must be null or lowercase 64-hex"
                )
    events = pkg.get("processing_events")
    if not isinstance(events, list):
        issues.append("package.processing_events must be a list")
    else:
        for i, event in enumerate(events):
            if not isinstance(event, dict):
                issues.append(f"package.processing_events[{i}] must be an object")
                continue
            for key in ("step", "outcome", "at_utc"):
                if not isinstance(event.get(key), str):
                    issues.append(
                        f"package.processing_events[{i}].{key} must be a string"
                    )
    origins = pkg.get("metadata_by_origin")
    if not isinstance(origins, dict):
        issues.append("package.metadata_by_origin must be an object")
    else:
        for key in (
            "source_embedded",
            "retrieval_generated",
            "processing_generated",
            "model_observations",
        ):
            if not isinstance(origins.get(key), dict):
                issues.append(f"package.metadata_by_origin.{key} must be an object")
    obs = pkg.get("observations")
    if not isinstance(obs, list):
        issues.append("package.observations must be a list")
    else:
        for i, observation in enumerate(obs):
            if not isinstance(observation, dict):
                issues.append(f"package.observations[{i}] must be an object")
                continue
            for key in ("id", "basis", "statement"):
                if not isinstance(observation.get(key), str):
                    issues.append(f"package.observations[{i}].{key} must be a string")
    relevance = pkg.get("attribution_relevance")
    if not isinstance(relevance, dict):
        issues.append("package.attribution_relevance must be an object")
    else:
        for key in ("claimed_vs_observed", "consistency_questions"):
            if not isinstance(relevance.get(key), list):
                issues.append(
                    f"package.attribution_relevance.{key} must be a list"
                )
    caveats = pkg.get("caveats")
    if not isinstance(caveats, list) or any(
        not isinstance(item, str) for item in caveats
    ):
        issues.append("package.caveats must be a list of strings")
    cleanup = pkg.get("cleanup")
    if not isinstance(cleanup, dict):
        issues.append("package.cleanup must be an object")
    else:
        if not (cleanup.get("status") is None or isinstance(cleanup.get("status"), str)):
            issues.append("package.cleanup.status must be null or a string")
        if not isinstance(cleanup.get("deleted_artifacts"), list):
            issues.append("package.cleanup.deleted_artifacts must be a list")
    generated = origins.get("processing_generated") if isinstance(origins, dict) else None
    if isinstance(generated, dict):
        finalized = generated.get("package_finalized_utc")
        if finalized is not None and not isinstance(finalized, str):
            issues.append(
                "package.metadata_by_origin.processing_generated."
                "package_finalized_utc must be a string when present"
            )
    try:
        record.canonical_json(pkg)
    except (ValueError, RecursionError) as exc:
        issues.append(f"package is not canonically serializable: {exc}")
    return issues


# --- package identity and binding -------------------------------------------


def compute_identity(pkg):
    """SHA-256 over the FULL canonical JSON of the package (keys sorted).

    Distinct from evidence_digest: identity is order-insensitive and covers
    every field; the digest is a bounded rendering. Raises ValueError on
    values JSON cannot represent.
    """
    try:
        return _sha256_text(record.canonical_json(pkg))
    except RecursionError:
        raise ValueError("package is cyclic or exceeds the supported depth") from None


def evidence_digest_sha256(pkg, max_chars=6000):
    """SHA-256 of the bounded evidence_digest text (informational only; not
    part of the binding)."""
    return _sha256_text(package.evidence_digest(pkg, max_chars=max_chars))


def _binding_payload(record_dict):
    """The single evidence_recorded payload carrying a package identity
    hash, or None. Assumes record_dict entries are dicts (gate on
    record.validate before calling)."""
    payloads = []
    for entry in record_dict.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("entry_type") != "evidence_recorded":
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict) and isinstance(
            payload.get("package_identity_sha256"), str
        ):
            payloads.append(payload)
    if len(payloads) != 1:
        return None
    return payloads[0]


def _binding_issues(pkg, record_dict):
    """All checks tying record_dict to pkg. Callers must gate on package
    shape/validate and record chain first (this function assumes both
    individually valid). Returns issue strings ([] means record_dict is
    exactly the binding this module builds for pkg)."""
    issues = []
    payload = _binding_payload(record_dict)
    if payload is None:
        return [
            "expected exactly one evidence_recorded entry payload carrying "
            "package_identity_sha256"
        ]
    identity = compute_identity(pkg)
    if payload.get("package_identity_sha256") != identity:
        issues.append(
            "package identity mismatch: the package bound into the entry "
            f"payload ({payload.get('package_identity_sha256')}) does not "
            f"match this package ({identity})"
        )
    if payload.get("schema") != package.SCHEMA:
        issues.append(
            f"entry payload schema must be {package.SCHEMA!r}, got "
            f"{payload.get('schema')!r}"
        )
    artifact_id = payload.get("source_artifact_id")
    artifact = next(
        (a for a in pkg["artifacts"] if a.get("artifact_id") == artifact_id),
        None,
    )
    if artifact is None:
        issues.append(
            f"entry payload source_artifact_id {artifact_id!r} does not "
            "exist in the package"
        )
    elif artifact.get("role") != "source":
        issues.append(
            f"entry payload source_artifact_id {artifact_id!r} references a "
            f"{artifact.get('role')!r} artifact, not a source artifact"
        )
    elif not _valid_sha256(artifact.get("sha256")):
        issues.append(
            f"bound source artifact {artifact_id!r} has no valid sha256"
        )
    else:
        subject = record_dict.get("subject") or {}
        source = pkg["source"]
        if subject.get("kind") != _SUBJECT_KIND:
            issues.append(
                f"record subject.kind must be {_SUBJECT_KIND!r}, got "
                f"{subject.get('kind')!r}"
            )
        if subject.get("identifier") != source["identifier"]:
            issues.append(
                "record subject.identifier does not match the package "
                "source identifier"
            )
        if subject.get("source_sha256") != artifact["sha256"]:
            issues.append(
                "record subject.source_sha256 does not match the bound "
                "source artifact sha256"
            )
        refs = record_dict.get("refs")
        refs_evidence = refs.get("evidence") if isinstance(refs, dict) else None
        if not isinstance(refs_evidence, dict):
            issues.append("record refs.evidence advisory binding is missing")
        else:
            expected = {
                "schema": package.SCHEMA,
                "source_artifact_id": artifact_id,
                "artifact_sha256": artifact["sha256"],
                "package_identity_sha256": identity,
            }
            for key, value in expected.items():
                if refs_evidence.get(key) != value:
                    issues.append(
                        f"record refs.evidence.{key} diverges from the "
                        "hash-protected entry payload (advisory header was "
                        "edited after the fact)"
                    )
    return issues


def binding_for_package(pkg):
    """The binding values for pkg: payload fields plus the advisory refs
    copy. Raises ValueError if pkg is not bindable."""
    issues = _package_shape_issues(pkg)
    if not issues:
        issues = package.validate(pkg)
    if issues:
        raise ValueError("cannot bind an invalid package: " + "; ".join(issues))
    source_artifact = next(
        (
            a
            for a in pkg["artifacts"]
            if a.get("role") == "source" and _valid_sha256(a.get("sha256"))
        ),
        None,
    )
    if source_artifact is None:
        raise ValueError(
            "package has no source artifact with a valid sha256; cannot bind"
        )
    identity = compute_identity(pkg)
    binding = {
        "schema": package.SCHEMA,
        "source_artifact_id": source_artifact["artifact_id"],
        "package_identity_sha256": identity,
        "artifact_sha256": source_artifact["sha256"],
    }
    return binding


# --- public API --------------------------------------------------------------


def bind_package(pkg, actor="media-ingest", at_utc=None, record_id=None,
                 created_utc=None):
    """Create a trust_record/v0.1 whose evidence_recorded entry payload
    binds pkg by full canonical identity. Raises ValueError on invalid
    packages or unbindable ones."""
    if not isinstance(pkg, dict):
        raise ValueError("package must be a media_evidence_package/v1 dict")
    issues = _package_shape_issues(pkg) + package.validate(pkg)
    if issues:
        raise ValueError("cannot bind an invalid package: " + "; ".join(issues))
    binding = binding_for_package(pkg)
    rec = record.new_record(
        subject={
            "kind": _SUBJECT_KIND,
            "identifier": pkg["source"]["identifier"],
            "source_sha256": binding["artifact_sha256"],
        },
        refs={"evidence": binding},
        record_id=record_id,
        created_utc=created_utc,
    )
    record.append_entry(
        rec,
        "evidence_recorded",
        actor=actor,
        payload={
            "schema": binding["schema"],
            "source_artifact_id": binding["source_artifact_id"],
            "package_identity_sha256": binding["package_identity_sha256"],
        },
        at_utc=at_utc,
    )
    problems = _binding_issues(pkg, rec)
    if problems:
        raise ValueError(
            "internal error: constructed record failed binding checks: "
            + "; ".join(problems)
        )
    return rec


def save_bundle(path, pkg, rec, overwrite=False):
    """Serialize {schema, package, record} to path as canonical JSON.

    Validates the package, the record chain, and the complete binding
    between them before touching the target. Never silently overwrites:
    without overwrite=True the completed file is published with os.link,
    which fails atomically if path exists (including a concurrent
    creation); with overwrite=True the publish is os.replace. Raises
    ValueError on invalid inputs, FileExistsError on an occupied path
    without overwrite.
    """
    if not isinstance(pkg, dict):
        raise ValueError("package must be a media_evidence_package/v1 dict")
    if not isinstance(rec, dict):
        raise ValueError("record must be a trust_record/v0.1 dict")
    issues = _package_shape_issues(pkg) + package.validate(pkg)
    if issues:
        raise ValueError("cannot save an invalid package: " + "; ".join(issues))
    issues = record.validate(rec)
    if issues:
        raise ValueError("cannot save an invalid record: " + "; ".join(issues))
    if not record.chain_valid(rec):
        raise ValueError("cannot save: the record's hash chain fails verification")
    issues = _binding_issues(pkg, rec)
    if issues:
        raise ValueError("cannot save: record is not bound to this package: "
                         + "; ".join(issues))
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "package": copy.deepcopy(pkg),
        "record": copy.deepcopy(rec),
    }
    text = record.canonical_json(bundle)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".tmp-", dir=os.path.dirname(path) or "."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise FileExistsError(
                    f"{path} already exists (or was created concurrently); "
                    "pass overwrite=True to replace it atomically"
                ) from None
            try:
                os.unlink(tmp)
            except OSError:
                pass
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return path


def load_bundle(path):
    """Load and shape-check a bundle file. Raises ValueError (or
    FileNotFoundError) on anything that is not a well-formed bundle."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        bundle = strict_loads(text)
    except ValueError as exc:
        raise ValueError(f"{path}: malformed JSON bundle: {exc}") from None
    _check_bundle_shape(bundle, context=path)
    return bundle


def _check_bundle_shape(bundle, context="bundle"):
    if not isinstance(bundle, dict):
        raise ValueError(f"{context}: bundle must be a JSON object")
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise ValueError(
            f"{context}: bundle schema must be {BUNDLE_SCHEMA!r}, got "
            f"{bundle.get('schema')!r}"
        )
    if not isinstance(bundle.get("package"), dict):
        raise ValueError(f"{context}: bundle.package must be an object")
    if not isinstance(bundle.get("record"), dict):
        raise ValueError(f"{context}: bundle.record must be an object")


def verify_bundle(bundle):
    """Independent verification of an in-memory bundle. Never raises; every
    problem becomes an issue string. Reports three verdicts separately:
    package validity, record chain integrity, and package binding.

    Binding is 'not evaluated' (binding_valid False with a reason) whenever
    the envelope, package, or chain fail, so a binding verdict is never
    reported successful without its prerequisites holding.
    """
    issues = {"envelope": [], "package": [], "chain": [], "binding": []}
    if not isinstance(bundle, dict):
        issues["envelope"].append("bundle must be a JSON object")
    else:
        if bundle.get("schema") != BUNDLE_SCHEMA:
            issues["envelope"].append(
                f"bundle schema must be {BUNDLE_SCHEMA!r}, got "
                f"{bundle.get('schema')!r}"
            )
        for key in ("package", "record"):
            if not isinstance(bundle.get(key), dict):
                issues["envelope"].append(f"bundle.{key} must be an object")
    pkg = bundle.get("package") if isinstance(bundle, dict) else None
    rec = bundle.get("record") if isinstance(bundle, dict) else None
    if isinstance(pkg, dict):
        issues["package"].extend(_package_shape_issues(pkg))
        issues["package"].extend(package.validate(pkg))
    else:
        issues["package"].append("package missing or not an object")
    if isinstance(rec, dict):
        issues["chain"].extend(record.validate(rec))
        if not record.chain_valid(rec):
            issues["chain"].append("entry hash chain does not recompute")
    else:
        issues["chain"].append("record missing or not an object")
    if issues["envelope"]:
        issues["binding"].append(
            "not evaluated because the bundle envelope is invalid"
        )
    elif issues["package"]:
        issues["binding"].append("not evaluated because the package is invalid")
    elif issues["chain"]:
        issues["binding"].append(
            "not evaluated because the record chain does not verify"
        )
    else:
        try:
            issues["binding"].extend(_binding_issues(pkg, rec))
        except Exception as exc:
            issues["binding"].append(
                f"binding evaluation failed: {exc.__class__.__name__}: {exc}"
            )
    return {
        "package_valid": not issues["package"],
        "chain_valid": not issues["chain"],
        "binding_valid": not issues["binding"],
        "issues": issues,
        "verified": (
            not issues["envelope"]
            and not issues["package"]
            and not issues["chain"]
            and not issues["binding"]
        ),
        "disclaimer": DISCLAIMER,
    }


def verify_file(path):
    """load_bundle + verify_bundle."""
    return verify_bundle(load_bundle(path))
