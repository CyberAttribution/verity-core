"""
canonical_case — the Canonical Case domain object for SwarmTracker Node
(v0.3.0 media parity sprint).

The Canonical Case is Verity's primary long-lived domain object: an
immutable `DFD-` id owning the evidence reference, report versions,
revision history, confidence history, corroboration status, and reserved
Atlas/Beacon refs. Node persists it into the local SQLite investigation
store (the public side stores cases on the filesystem — the *field set*
is shared, the storage is not).

RECONCILE-ON-ASSET-ARRIVAL: field names below follow the sprint handoff
(§3.1 S7 / §8.3). When `CANONICAL_CASE.md` + `publication.py` from
attributionagent-backend release/v0.3.0 are available, reconcile:
  - the exact DFD- id format,
  - the ownership table / field names,
  - corroboration-status vocabulary.
Search for "RECONCILE" to find every dependent site.
"""
import time
import uuid

# Handoff vocabulary; RECONCILE with CANONICAL_CASE.md when available.
CORROBORATION_STATUSES = ("uncorroborated", "corroboration_recommended",
                          "partially_corroborated", "corroborated",
                          "disputed")


def new_case_id():
    """Immutable case id. RECONCILE exact format with the reference."""
    return "DFD-" + time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8]


def _escalation_record(rm):
    """Derive the immutable escalation custody record from router_meta."""
    escalated = bool(rm.get("escalated"))
    used = bool(rm.get("escalation_ok"))
    unavailable = bool(rm.get("unavailable"))
    if not escalated:
        state = "not_authorized"
    elif rm.get("denied"):
        state = "denied"          # refused before any provider was engaged
    elif used:
        state = "used"
    elif unavailable:
        state = "unavailable"     # authorized, but NO provider was contacted
    elif rm.get("multimodal_unavailable"):
        state = "failed"
    else:
        state = "authorized_no_effect"
    return {
        "authorized": escalated and not rm.get("denied"),
        "provider": rm.get("escalation_provider"),
        "vertex_project": rm.get("vertex_project"),
        "region": rm.get("vertex_region"),
        "model_used": rm.get("model_used"),
        "attempted_models": list(rm.get("attempted_models") or []),
        "latency_ms": rm.get("latency_ms"),
        "state": state,      # not_authorized|denied|used|unavailable|failed|
                             # authorized_no_effect
        "unavailable": unavailable,
        "denied": bool(rm.get("denied")),
        "reason": rm.get("reason"),
        "provider_status": rm.get("provider_status"),
        "provider_error": rm.get("provider_error"),
        "attempt_history": list(rm.get("attempt_history") or []),
        "artifact_custody": dict(rm.get("custody") or {}),
    }


def build_case(*, package, assessment, report_id="", media_label="",
               family="", router_meta=None, node_version="0.3.0"):
    """Assemble the Canonical Case dict from one completed media job.

    Immutable after creation except: revision_history, confidence_history,
    corroboration_status (append/update via the store, never in place).
    """
    src = (package or {}).get("source") or {}
    arts = (package or {}).get("artifacts") or []
    source_art = next((a for a in arts if a.get("role") == "source"), {})
    conclusion = (assessment or {}).get("assessment_conclusion",
                                        "insufficient_media_evidence")
    confidence = (assessment or {}).get("confidence_by_domain") or {}
    case_id = new_case_id()
    return {
        "case_id": case_id,
        "schema": "canonical_case/node-v1",   # RECONCILE with reference
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "media": {
            "label": str(media_label)[:200],
            "family": family,
            "sha256": source_art.get("sha256"),
            "size_bytes": source_art.get("size_bytes"),
            "source_kind": src.get("kind"),
            "source_identifier": src.get("identifier"),
        },
        "evidence_ref": {
            "package_schema": (package or {}).get("schema"),
            "artifact_count": len(arts),
            "observation_count": len((package or {}).get("observations")
                                     or []),
            "cleanup_status": ((package or {}).get("cleanup")
                               or {}).get("status"),
        },
        "assessment_conclusion": conclusion,
        "confidence_by_domain": confidence,
        "runtime": dict(router_meta or {}),
        # Explicit escalation custody record (v0.3.4). Immutable audit of
        # whether cloud multimodal analysis was authorized, which
        # provider/models were attempted, whether it was used/failed, and
        # which artifacts left the node vs stayed local.
        "escalation": _escalation_record(router_meta or {}),
        "corroboration_status": (
            "corroboration_recommended"
            if (assessment or {}).get("recommended_corroboration")
            else "uncorroborated"),
        "versions": [{"version": 1, "report_id": report_id,
                      "node_version": node_version,
                      "created_utc": time.strftime(
                          "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}],
        "confidence_history": [{"version": 1,
                                "confidence_by_domain": confidence}],
        "revision_history": [],
        # Reserved refs (shared Atlas contract; Beacon future):
        "atlas_refs": [],
        "beacon_refs": [],
        # Publication events are PUBLIC-ONLY (hosted curation); the field
        # is reserved so exports stay schema-compatible with the
        # reference, but Node never writes to it.
        "publication_events": [],
    }
