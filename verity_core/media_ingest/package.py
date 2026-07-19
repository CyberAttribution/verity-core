"""
media_ingest.package — the structured media-evidence package.

ORIENTATION (approved sprint requirement): metadata here is EVIDENCE that
may support or weaken attribution hypotheses; establish source, timing,
device, software, or processing history; reveal inconsistencies between
claimed and observed origin; corroborate or conflict with other evidence;
and shift confidence between competing explanations. It is never, by
itself, proof of authorship or manipulation. Direct observations are
separated from inferences, and inferences carry their uncertainty.

For every source and derived artifact the package records (where
available): identifier, kind, cryptographic hash, acquisition/upload
time, embedded timestamps, extraction tool + version, processing step,
parent artifact, derived-artifact hash, metadata-origin classification,
and cleanup status.

Metadata-origin classification (operator requirement — never implied to
be the uploader's original when retrieved from a platform):
  source_embedded       carried inside the file (tags/EXIF); forgeable
  retrieval_generated   produced by a retrieval adapter (e.g. YouTube)
  processing_generated  produced by this pipeline (hashes, timings, tools)
  model_observations    produced later by model analysis (empty here)

Schema id: media_evidence_package/v1 — runtime-neutral plain dicts,
consumed identically by the hosted backend and a future Node sprint.
"""
import re
import time
import uuid

from . import tools

SCHEMA = "media_evidence_package/v1"

CAVEATS = [
    "Metadata is evidence bearing on attribution hypotheses, not proof of "
    "authorship or manipulation; embedded tags can be absent, stale, "
    "platform-stripped, or forged.",
    "Absence of metadata is itself an observation with multiple innocent "
    "explanations (platform re-encoding commonly strips it).",
    "Direct observations describe what the tools read; inferences are "
    "labeled as such and carry uncertainty.",
]


def _utc(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))


def _new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def new_package(source_kind, source_identifier, acquisition_time=None,
                declared=None):
    """source_kind: upload | youtube. `declared` holds the submitter's
    untrusted claims (filename, mime, claimed date/place/context)."""
    return {
        "schema": SCHEMA,
        "source": {
            "identifier": str(source_identifier)[:300],
            "kind": source_kind,
            "acquisition_time_utc": _utc(acquisition_time),
            "declared_untrusted": dict(declared or {}),
        },
        "artifacts": [],
        "processing_events": [],
        "metadata_by_origin": {
            "source_embedded": {},
            "retrieval_generated": {},
            "processing_generated": {
                "acquisition_time_utc": _utc(acquisition_time),
            },
            "model_observations": {},
        },
        "tooling": {},
        "observations": [],
        "attribution_relevance": {
            "claimed_vs_observed": [],
            "consistency_questions": [],
        },
        "caveats": list(CAVEATS),
        "cleanup": {"status": None, "deleted_artifacts": []},
    }


def add_artifact(pkg, kind, path=None, sha256=None, size_bytes=None,
                 parent=None, step=None, tool=None, label=""):
    """Register a source or derived artifact. Hash computed from `path`
    when given. Returns the artifact id. Paths are NOT stored."""
    if path is not None and sha256 is None:
        sha256 = tools.sha256_file(path)
    art = {
        "artifact_id": _new_id("mart"),
        "kind": kind,                       # source_media|frame|audio_track|…
        "role": "source" if parent is None else "derived",
        "label": label[:120],
        "sha256": sha256,
        "size_bytes": size_bytes,
        "parent_artifact": parent,
        "created_by_step": step,
        "tool": tool,                       # {"name","version"} or None
        "recorded_utc": _utc(),
    }
    pkg["artifacts"].append(art)
    return art["artifact_id"]


def record_event(pkg, step, outcome, tool=None, inputs=None, outputs=None,
                 note=""):
    """Append one chain-of-processing event."""
    pkg["processing_events"].append({
        "seq": len(pkg["processing_events"]) + 1,
        "step": step,
        "outcome": outcome,                 # ok | failed | skipped | …
        "tool": (tool or {}).get("name") if isinstance(tool, dict) else tool,
        "tool_version": (tool or {}).get("version")
        if isinstance(tool, dict) else None,
        "at_utc": _utc(),
        "input_artifacts": list(inputs or []),
        "output_artifacts": list(outputs or []),
        "note": str(note)[:500],
    })


# Epistemic vocabulary (S3, approved): one observation model for evidence
# from metadata, frames, audio, transcripts, or context — so Origin,
# Beacon, and Node workflows consume the same structure regardless of the
# evidence modality.
#   direct_observation    what a tool or deterministic check read
#   inference             deterministic reasoning over observations
#   model_interpretation  a model's reading of media/text (untrusted,
#                         bounded; always carries competing explanations)
OBSERVATION_BASES = ("direct_observation", "inference",
                     "model_interpretation")


def _observe(pkg, statement, basis, relevance="", competing=None,
             modality="metadata", source=None):
    obs = {
        "id": _new_id("obs"),
        "statement": str(statement)[:600],
        "basis": basis,
        "modality": modality,       # metadata|visual|audio|transcript|context
        "attribution_relevance": str(relevance)[:400],
        "competing_explanations": [str(c)[:300] for c in (competing or [])],
    }
    if source:
        obs["source"] = source      # e.g. {"model": ..., "artifacts": [...]}
    pkg["observations"].append(obs)
    return obs["id"]


# public alias for consumers (backend runner, Node) recording their own
# direct observations / inferences into the shared evidence model
observe = _observe


def add_model_observation(pkg, statement, model, modality,
                          competing_explanations, relevance="",
                          artifact_ids=None):
    """Record a MODEL INTERPRETATION of media evidence. Required to carry
    competing explanations — a vision/audio model reading is never a
    finding of manipulation or authenticity by itself. Also folded into
    metadata_by_origin.model_observations for the four-way classification."""
    oid = _observe(pkg, statement, "model_interpretation",
                   relevance=relevance,
                   competing=competing_explanations or [
                       "The model's reading may be mistaken; media review "
                       "by a qualified examiner is the check."],
                   modality=modality,
                   source={"model": str(model)[:120],
                           "artifacts": list(artifact_ids or [])})
    mo = pkg["metadata_by_origin"]["model_observations"]
    mo.setdefault(modality, []).append(
        {"observation_id": oid, "statement": str(statement)[:600],
         "model": str(model)[:120]})
    return oid


# ---- attaching tool results -------------------------------------------------
def attach_probe(pkg, probe_result, artifact_id=None):
    """Fold an ffprobe result in. Structural facts + container tags are
    source-embedded; the fact we measured them is processing metadata."""
    pkg["tooling"]["ffprobe"] = {
        "available": not probe_result.get("tool_missing", False),
        "version": (probe_result.get("tool") or {}).get("version"),
        "ok": bool(probe_result.get("ok")),
    }
    record_event(pkg, "structural_probe",
                 "ok" if probe_result.get("ok") else "failed",
                 tool=probe_result.get("tool"),
                 inputs=[artifact_id] if artifact_id else [],
                 note=probe_result.get("error") or "")
    if not probe_result.get("ok"):
        if probe_result.get("tool_missing"):
            _observe(pkg, "Structural probing (ffprobe) was not available "
                          "in this runtime; container and codec evidence "
                          "was not examined.", "direct_observation",
                     "Limits the technical-evidence basis of any "
                     "conclusion.")
        return
    se = pkg["metadata_by_origin"]["source_embedded"]
    se["container"] = probe_result.get("container")
    se["streams"] = probe_result.get("streams")
    if probe_result.get("embedded_tags"):
        se["container_tags"] = probe_result["embedded_tags"]
    c = probe_result.get("container") or {}
    if c.get("format_name"):
        _observe(pkg, f"Container format observed: {c.get('format_name')}"
                      f" ({c.get('size_bytes')} bytes, duration "
                      f"{c.get('duration_s')}s).", "direct_observation",
                 "Establishes the file's technical form for comparison "
                 "against its claimed origin and platform norms.")


def attach_exif(pkg, exif_result, artifact_id=None):
    pkg["tooling"]["exiftool"] = {
        "available": not exif_result.get("tool_missing", False),
        "version": (exif_result.get("tool") or {}).get("version"),
        "ok": bool(exif_result.get("ok")),
    }
    record_event(pkg, "embedded_metadata_extraction",
                 "ok" if exif_result.get("ok") else "failed",
                 tool=exif_result.get("tool"),
                 inputs=[artifact_id] if artifact_id else [],
                 note=exif_result.get("error") or "")
    if not exif_result.get("ok"):
        if exif_result.get("tool_missing"):
            _observe(pkg, "Embedded-metadata extraction (exiftool) was not "
                          "available in this runtime; EXIF/XMP evidence "
                          "was not examined.", "direct_observation",
                     "Timing/device/software evidence unavailable; "
                     "conclusions must not assume its absence from the "
                     "file itself.")
        return
    se = pkg["metadata_by_origin"]["source_embedded"]
    se["embedded_timestamps"] = exif_result.get("timestamps") or {}
    se["device_software"] = exif_result.get("device_software") or {}
    se["embedded_context"] = exif_result.get("context") or {}
    se["gps_present"] = bool(exif_result.get("gps_present"))
    se["embedded_tag_count"] = exif_result.get("total_tags")


# ---- deterministic evidence reasoning (bounded; no model) -------------------
_TS_RE = re.compile(r"(\d{4})[:\-](\d{2})[:\-](\d{2})"
                    r"(?:[ T](\d{2}):(\d{2}):(\d{2}))?")


def _parse_ts(value):
    """Parse EXIF ('2026:07:01 10:00:00') or ISO ('2026-07-05[T...]')
    values; a date without a time is taken as midnight."""
    m = _TS_RE.search(str(value or ""))
    if not m:
        return None
    parts = [int(g) if g else 0 for g in m.groups()]
    try:
        return time.mktime(time.struct_time(tuple(parts) + (0, 0, -1)))
    except (ValueError, OverflowError):
        return None


def derive_observations(pkg, declared_extension=None):
    """Deterministic observations + labeled inferences from what was
    attached. Inferences preserve uncertainty and competing explanations."""
    se = pkg["metadata_by_origin"]["source_embedded"]
    tags = dict(se.get("container_tags") or {})
    dev = dict(se.get("device_software") or {})
    ts = dict(se.get("embedded_timestamps") or {})

    # encoder / software processing history
    software = {k: v for k, v in {**tags, **dev}.items()
                if k.lower() in ("encoder", "software", "creatortool",
                                 "writerapplication", "compressorname",
                                 "handlervendorid")}
    if software:
        _observe(pkg, "Software/encoder identifiers embedded in the file: "
                      + "; ".join(f"{k}={v}" for k, v in
                                  sorted(software.items())),
                 "direct_observation",
                 "Processing-history evidence: may corroborate or conflict "
                 "with the claimed capture device and workflow.")
        _observe(pkg, "The named software touched this file at some point; "
                      "this is consistent with re-encoding or editing but "
                      "does not establish what was changed, by whom, or "
                      "whether changes were substantive.", "inference",
                 "Bears on whether the processing history is consistent "
                 "with the stated narrative.")

    # embedded timestamp coherence
    parsed = {k: _parse_ts(v) for k, v in ts.items()}
    parsed = {k: v for k, v in parsed.items() if v}
    create = parsed.get("CreateDate") or parsed.get("DateTimeOriginal") \
        or parsed.get("MediaCreateDate")
    modify = parsed.get("ModifyDate") or parsed.get("MediaModifyDate")
    if create and modify and abs(modify - create) > 60:
        _observe(pkg, f"Embedded create and modify timestamps differ by "
                      f"~{int(abs(modify - create))}s.",
                 "direct_observation",
                 "Timing evidence: a gap is consistent with later "
                 "processing; also consistent with routine transcoding or "
                 "clock drift — corroborate against claimed dates.")
    if ts:
        se.setdefault("embedded_timestamps", ts)
    else:
        _observe(pkg, "No embedded creation/modification timestamps were "
                      "found in the examined metadata.",
                 "direct_observation",
                 "Consistent with platform re-encoding or deliberate "
                 "stripping; also consistent with formats and workflows "
                 "that never carried them. Weakens timing evidence in "
                 "either direction.")

    # extension vs observed container
    fmt = ((se.get("container") or {}).get("format_name") or "")
    if declared_extension and fmt and \
            declared_extension.lower() not in fmt.lower().replace(
                "quicktime", "mov"):
        _observe(pkg, f"Declared extension '.{declared_extension}' vs "
                      f"observed container '{fmt}'.", "direct_observation",
                 "A mismatch is evidence of renaming or re-wrapping; "
                 "verify before weighting other extension-based claims.")

    # claimed vs observed timing (declared claims are untrusted)
    claimed_date = (pkg["source"]["declared_untrusted"] or {}).get(
        "claimed_date")
    if claimed_date and create:
        c = _parse_ts(claimed_date)
        if c and abs(c - create) > 86400:
            pkg["attribution_relevance"]["claimed_vs_observed"].append(
                "Claimed date differs from the embedded creation "
                "timestamp by more than a day; embedded timestamps are "
                "forgeable, but the inconsistency warrants corroboration.")
    if not pkg["attribution_relevance"]["consistency_questions"]:
        pkg["attribution_relevance"]["consistency_questions"] = [
            "Is the observed container/codec profile consistent with the "
            "claimed source platform and device?",
            "Is the processing history (encoder tags, timestamp deltas) "
            "consistent with the stated narrative of capture and "
            "distribution?",
            "Do technical, contextual, and provenance evidence corroborate "
            "one another, and which competing explanations remain "
            "plausible?",
        ]


def derive_recommended_corroboration(pkg):
    """Deterministic evidence-collection guidance (S4, approved): the
    highest-value NEXT evidence that would strengthen or weaken the
    competing hypotheses. Guidance only — never conclusions. Stored on
    the package and merged (labeled) into the report."""
    recs = []
    se = pkg["metadata_by_origin"]["source_embedded"]
    declared = pkg["source"].get("declared_untrusted") or {}
    kind = pkg["source"].get("kind")

    def rec(item, rationale, bearing):
        recs.append({"item": item, "rationale": rationale,
                     "bearing": bearing, "origin": "deterministic"})

    if kind == "youtube":
        rec("The original camera/source file from the claimed creator",
            "Platform-retrieved media is re-encoded; the original carries "
            "metadata and signal characteristics the platform copy lost.",
            "Strengthens or weakens the claimed-origin hypothesis "
            "directly.")
        rec("Earlier or alternate uploads of the same content",
            "Upload chronology can reveal the earliest known appearance "
            "and cross-platform provenance.",
            "Bears on whether this posting is the origin or a repost.")
    else:
        rec("Confirmation that the uploaded file is the original, "
            "un-re-encoded capture",
            "A first-generation file preserves device metadata and "
            "signal characteristics that derivatives lose.",
            "Raises or lowers the weight of all technical findings.")
    if not (se.get("embedded_timestamps") or {}):
        rec("Any sidecar, platform, or account records establishing "
            "capture/upload time",
            "The file carries no usable embedded timestamps.",
            "Restores timing evidence for or against the claimed date.")
    if declared.get("claimed_date") or declared.get("claimed_place"):
        rec("Authoritative records for the claimed time and place "
            "(weather, schedules, official records, corroborating "
            "eyewitness media)",
            "Claimed time/place is currently supported only by the "
            "submitter's statement.",
            "Independently corroborates or contradicts the claimed "
            "context.")
    rec("Cryptographic hashes of any other copies in circulation",
        "Hash comparison distinguishes identical redistribution from "
        "re-encoded or altered variants.",
        "Separates distribution questions from alteration questions.")
    pkg["recommended_corroboration"] = recs
    return recs


def evidence_digest(pkg, max_chars=6000):
    """Deterministic, bounded text digest of the evidence package — the
    textual form the analysis pipeline receives. Same digest for hosted
    and Node consumption."""
    lines = []
    src = pkg["source"]
    lines.append(f"MEDIA EVIDENCE PACKAGE ({pkg['schema']})")
    lines.append(f"Source kind: {src['kind']} | identifier: "
                 f"{src['identifier']} | acquired: "
                 f"{src['acquisition_time_utc']}")
    d = src.get("declared_untrusted") or {}
    if any(d.values()):
        lines.append("Submitter-declared (UNTRUSTED CLAIMS): "
                     + "; ".join(f"{k}={v}" for k, v in d.items() if v))
    for a in pkg["artifacts"]:
        lines.append(f"Artifact {a['artifact_id']} [{a['kind']}/"
                     f"{a['role']}] sha256={a['sha256']} "
                     f"size={a.get('size_bytes')} "
                     f"parent={a.get('parent_artifact') or '-'} "
                     f"step={a.get('created_by_step')}")
    se = pkg["metadata_by_origin"]["source_embedded"]
    c = se.get("container") or {}
    if c:
        lines.append(f"Container (observed): {c.get('format_name')} "
                     f"duration={c.get('duration_s')}s "
                     f"size={c.get('size_bytes')} "
                     f"bit_rate={c.get('bit_rate')}")
    for s in se.get("streams") or []:
        lines.append("Stream (observed): " + ", ".join(
            f"{k}={v}" for k, v in s.items() if v is not None
            and k != "stream_tags"))
    for label, key in (("Container tags", "container_tags"),
                       ("Embedded timestamps", "embedded_timestamps"),
                       ("Device/software", "device_software")):
        vals = se.get(key) or {}
        if vals:
            lines.append(f"{label} (source-embedded, forgeable): "
                         + "; ".join(f"{k}={v}" for k, v in vals.items()))
    rg = pkg["metadata_by_origin"]["retrieval_generated"]
    if rg:
        lines.append("Retrieval-generated metadata (from the platform "
                     "adapter, NOT the uploader's original): "
                     + "; ".join(f"{k}={v}" for k, v in
                                 sorted(rg.items())[:20]))
    lines.append("Processing events: " + " -> ".join(
        f"{e['step']}({e['outcome']})" for e in pkg["processing_events"]))
    for o in pkg["observations"]:
        tag = o["basis"].upper()
        lines.append(f"[{tag}/{o.get('modality', 'metadata')}] "
                     f"{o['statement']}")
        if o.get("competing_explanations"):
            lines.append("  competing: "
                         + " | ".join(o["competing_explanations"]))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 22] + "\n[digest truncated]"
    return text


def finalize(pkg, cleanup_status, deleted_artifact_ids=None):
    pkg["cleanup"]["status"] = cleanup_status
    pkg["cleanup"]["deleted_artifacts"] = list(deleted_artifact_ids or [])
    record_event(pkg, "temporary_media_cleanup",
                 "ok" if str(cleanup_status).startswith("completed")
                 else "failed", note=str(cleanup_status))
    pg = pkg["metadata_by_origin"]["processing_generated"]
    pg["cleanup_status"] = cleanup_status
    pg["package_finalized_utc"] = _utc()
    return pkg
