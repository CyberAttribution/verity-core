"""
governed_analysis — the single governed cloud-assist service (v0.5.0).

One place that owns: authorization state, provider router construction
(via the provider registry), provider-metadata capture, truthful
degradation, custody manifest, Canonical-Case escalation fields, and
audit events. Media Evidence, generic investigations, and the
Intimate-Image Abuse Assessment all call THIS — no second cloud stack,
no duplicated escalation logic.

Local Analysis remains the default everywhere; a cloud router is built
only on explicit per-job authorization AND when the active provider is
ready (Keel policy + ADC + project). Fusion always stays local.
"""
from . import core_bridge, keel_events, keel_policy, providers

# ---- Reserved sensitive-media preparation (v0.5.0) — NOT active ----
# The Intimate-Image Abuse Assessment workflow is not built or exposed in
# this release. These identifiers, the safety-stop state, the reserved
# report fields, and the policy-hook extension point exist so the guards,
# exports, and tests are in place before the workflow is. Attempting to
# run this workflow id is refused (see is_reserved_workflow / guard).
RESERVED_SENSITIVE_WORKFLOW = "intimate_image_abuse_assessment"

# Recognized execution states (Part 5). safety_stopped is reserved for
# the future sensitive-media workflow; no active path emits it in v0.5.0.
EXECUTION_STATES = (
    "local_only", "cloud_authorized", "cloud_succeeded", "cloud_failed",
    "extracted_not_examined", "examined_by_provider", "safety_stopped",
)
SAFETY_STOP_STATE = "safety_stopped"

# Reserved shared report fields for future sensitive-media handling.
# Returned as an inert scaffold; no active workflow populates them.
def reserved_sensitive_fields():
    return {
        "identity_not_established": True,
        "consent_not_established": True,
        "age_not_reliably_established": True,
        "restricted_handling_required": True,
    }


def is_reserved_workflow(workflow):
    return str(workflow or "") == RESERVED_SENSITIVE_WORKFLOW


class ReservedWorkflowError(Exception):
    """Raised if a caller tries to invoke the reserved (inactive)
    sensitive-media workflow in v0.5.0."""


def guard_workflow(workflow):
    """Refuse the reserved sensitive-media workflow (inactive in
    v0.5.0). Call at every governed entry point."""
    if is_reserved_workflow(workflow):
        keel_events.append_event(
            "reserved_workflow_refused",
            {"workflow": workflow, "reason": "inactive in v0.5.0"})
        raise ReservedWorkflowError(
            "the Intimate-Image Abuse Assessment workflow is reserved "
            "and inactive in this release")


# Extension point: future sensitive-media policy hooks register here.
# Empty in v0.5.0; the governed service calls run_policy_hooks() which is
# a no-op until hooks exist.
_POLICY_HOOKS = []


def register_policy_hook(fn):
    _POLICY_HOOKS.append(fn)
    return fn


def run_policy_hooks(context):
    """Run any registered pre-analysis policy hooks. No-op in v0.5.0."""
    for fn in _POLICY_HOOKS:
        fn(context)
    return context


# ---- authorization + router construction ----

def escalation_ready():
    """(ready: bool, reason, provider_config). Policy + provider gate."""
    allowed, why = keel_policy.multimodal_escalation_allowed()
    pcfg = providers.provider_config()
    if not allowed:
        return False, why, pcfg
    if not pcfg["ready"]:
        return False, pcfg["reason"], pcfg
    return True, pcfg["reason"], pcfg


def build_router(generate_fn, selected_model=None):
    """(EscalatingRouter | None, reason). Fail-closed at every layer:
    Keel policy, then the active provider's readiness (ADC + project)."""
    allowed, why = keel_policy.multimodal_escalation_allowed()
    if not allowed:
        return None, why
    cloud, reason = providers.build_cloud_router(selected_model)
    if cloud is None:
        return None, reason
    return core_bridge.EscalatingRouter(generate_fn, cloud), ""


# ---- provider metadata / custody (shared shape for router_meta) ----

def escalation_metadata(router, *, escalated, media_examined,
                        artifacts_present, sent_manifest,
                        retained_hashes):
    """Return the escalation slice of router_meta, identical for every
    workflow. `provider_failed`/`multimodal_unavailable` follow the
    truthful-degradation rules."""
    mm_ok = bool(escalated and getattr(router, "multimodal_ok", False))
    provider_failed = bool(escalated) and not mm_ok
    multimodal_unavailable = (provider_failed and not media_examined
                              and artifacts_present)
    attempted = list(getattr(router, "attempted_models", []) or []) \
        if escalated else []
    return {
        "escalated": bool(escalated),
        "escalation_provider": (getattr(router, "provider", "vertex")
                                if escalated else None),
        "escalation_ok": mm_ok if escalated else None,
        "multimodal_unavailable": bool(multimodal_unavailable),
        "attempted_models": attempted,
        "model_used": getattr(router, "model_used", None)
        if escalated else None,
        "latency_ms": getattr(router, "latency_ms", None)
        if escalated else None,
        "vertex_project": getattr(router, "project", None)
        if escalated else None,
        "vertex_region": getattr(router, "region", None)
        if escalated else None,
        "attempt_history": getattr(router, "multimodal_attempts", [])
        if escalated else [],
        "provider_error": getattr(router, "multimodal_error", None)
        if escalated else None,
        "provider_status": getattr(router, "multimodal_status", None)
        if escalated else None,
        "sent_artifacts": sent_manifest if escalated else [],
        "custody": {
            "source_file": "processed locally, deleted after analysis; "
                           "never sent to any provider",
            "retained_local": list(retained_hashes or []),
            "sent_to_provider": [s.get("sha256") for s in sent_manifest]
            if (escalated and mm_ok) else [],
            "sent_but_provider_failed": [s.get("sha256") for s in
                                         sent_manifest]
            if provider_failed else [],
        },
    }


def provenance(router, *, escalated, escalate_requested, esc_reason,
               attempted_models, provider_status, sent_manifest,
               local_extraction="local — deterministic",
               local_persistence="local — SQLite store, Keel policy, "
                                  "media ledger"):
    """Shared analysis-provenance dict (which layer produced what)."""
    esc_ok = escalated and getattr(router, "multimodal_ok", False)
    prov_name = getattr(router, "provider", "vertex") if escalated \
        else None
    if escalated and not esc_ok:
        models = ", ".join(attempted_models) or "none"
        esc_state = (f"{prov_name}: authorized but FAILED — provider "
                     "error"
                     + (f" (HTTP {provider_status})"
                        if provider_status else "")
                     + f"; models attempted: {models}; bounded artifacts "
                     "preserved locally, not examined")
    elif esc_ok:
        mu = getattr(router, "model_used", None) or "n/a"
        lat = getattr(router, "latency_ms", None)
        esc_state = (f"{prov_name}: authorized + completed on {mu}"
                     + (f" ({lat} ms)" if lat else ""))
    elif escalate_requested:
        esc_state = "requested but unavailable: " + (esc_reason or "n/a")
    else:
        esc_state = "not requested (Local Only default)"
    return {
        "extraction": local_extraction,
        "artifact_analysis": (
            (getattr(router, "provider", "vertex") + " — authorized "
             "escalation on " + (getattr(router, "model_used", None)
                                 or "?"))
            if esc_ok else "local model (OpenClaw, Keel-locked)"),
        "fusion": "local model (OpenClaw, Keel-locked)",
        "persistence_governance": local_persistence,
        "escalation": esc_state,
        "sent_artifacts": sent_manifest if escalated else [],
        "attempted_models": attempted_models,
        "provider_error": getattr(router, "multimodal_error", None)
        if escalated else None,
        "provider_status": provider_status,
        "multimodal_unavailable": bool(
            escalated and not esc_ok and sent_manifest),
    }


# ---- audit ----

def audit_authorized(workflow, provider, sent_manifest, extra=None):
    keel_events.append_event("cloud_escalation_authorized",
                             {"workflow": workflow, "provider": provider,
                              "artifact_count": len(sent_manifest),
                              "artifacts": sent_manifest,
                              **(extra or {})})


def audit_result(workflow, ok, error=None, model_used=None,
                 latency_ms=None):
    keel_events.append_event("cloud_escalation_result",
                             {"workflow": workflow, "ok": bool(ok),
                              "error": error, "model_used": model_used,
                              "latency_ms": latency_ms})


def audit_denied(workflow, reason):
    keel_events.append_event("cloud_escalation_denied",
                             {"workflow": workflow, "reason": reason})


_GOVERNED_TEXT_PROMPT = (
    "You are a governed cloud-assist analyst for SwarmTracker Node. You "
    "are given a BOUNDED excerpt of investigation evidence (not the full "
    "case). Provide a concise, bounded assessment: key observations, "
    "risk signals, and what additional local corroboration would help. "
    "Use bounded language; never fabricate details not present in the "
    "excerpt; do not claim identity or attribution beyond the evidence. "
    "Return plain text.")


def run_text_cloud_assist(items, *, workflow="general_evidence",
                          selected_model=None, system=None,
                          prompt=None, max_chars=6000):
    """Governed cloud-assist over a BOUNDED text payload. Local fusion is
    the caller's separate, always-local step; this adds a clearly-labeled
    cloud supplement. Fail-soft + truthful. Returns a dict:
    {escalated, ok, text, error, provider, model_used, latency_ms,
     project, region, attempted_models, provider_status, attempts,
     sent_manifest, reason}."""
    ready, reason, pcfg = escalation_ready()
    if not ready:
        audit_denied(workflow, reason)
        return {"escalated": True, "ok": False, "available": False,
                "reason": reason, "text": "", "sent_manifest": [],
                "provider": pcfg.get("provider"), "model_used": None,
                "attempted_models": [], "attempts": [],
                "provider_status": None, "latency_ms": None,
                "project": pcfg.get("project"),
                "region": pcfg.get("region"), "error": reason}
    cloud, why = providers.build_cloud_router(selected_model)
    if cloud is None:
        audit_denied(workflow, why)
        return {"escalated": True, "ok": False, "available": False,
                "reason": why, "text": "", "sent_manifest": [],
                "provider": "vertex", "model_used": None,
                "attempted_models": [], "attempts": [],
                "provider_status": None, "latency_ms": None,
                "error": why}
    digest, manifest = bounded_text_payload(items, max_chars=max_chars)
    audit_authorized(workflow, "vertex", manifest)
    full_prompt = (prompt or _GOVERNED_TEXT_PROMPT) + "\n\n" + digest
    out = cloud.generate(full_prompt) or {}
    ok = bool(out.get("ok"))
    audit_result(workflow, ok, error=out.get("error"),
                 model_used=out.get("model_used"),
                 latency_ms=out.get("latency_ms"))
    return {
        "escalated": True, "available": True, "ok": ok,
        "text": out.get("response", "") if ok else "",
        "error": out.get("error") if not ok else None,
        "provider": out.get("provider", "vertex"),
        "model_used": out.get("model_used"),
        "latency_ms": out.get("latency_ms"),
        "project": out.get("project") or getattr(cloud, "project", None),
        "region": out.get("region") or getattr(cloud, "location", None),
        "attempted_models": out.get("attempted_models") or [],
        "provider_status": out.get("provider_status"),
        "attempts": out.get("attempts") or [],
        "sent_manifest": manifest,
        "reason": "",
    }


def text_assist_router_meta(res):
    """Shape a run_text_cloud_assist result into the shared router_meta
    escalation fields, so generic investigations record identical
    provider provenance + custody as media."""
    if not res or not res.get("escalated"):
        return {"escalated": False}
    ok = bool(res.get("ok"))
    sent = res.get("sent_manifest") or []
    return {
        "escalated": True,
        "escalation_provider": res.get("provider") or "vertex",
        "escalation_ok": ok,
        "multimodal_unavailable": False,   # text assist, not multimodal
        "attempted_models": res.get("attempted_models") or [],
        "model_used": res.get("model_used"),
        "latency_ms": res.get("latency_ms"),
        "vertex_project": res.get("project"),
        "vertex_region": res.get("region"),
        "attempt_history": res.get("attempts") or [],
        "provider_error": res.get("error"),
        "provider_status": res.get("provider_status"),
        "sent_artifacts": sent,
        "custody": {
            "source_evidence": "retained locally; only bounded excerpts "
                               "sent",
            "retained_local": "full investigation, evidence package, and "
                              "canonical case",
            "sent_to_provider": [s.get("sha256") for s in sent]
            if ok else [],
            "sent_but_provider_failed": [s.get("sha256") for s in sent]
            if not ok else [],
        },
    }


# ---- bounded evidence selection (Part 2) ----

def bounded_text_payload(items, max_chars=6000, max_items=8):
    """Build the MINIMUM useful cloud payload from text-type evidence:
    per-item label + a bounded excerpt, never the whole investigation.
    Returns (digest_text, manifest[]) where manifest records exactly what
    was selected (type, sha256, size, reason) for the custody record."""
    import hashlib
    lines = ["GOVERNED CLOUD-ASSIST — BOUNDED EVIDENCE DIGEST",
             "(only the selected excerpts below were sent; the full "
             "investigation, evidence package, and case remain local)", ""]
    manifest = []
    for it in (items or [])[:max_items]:
        text = str(it.get("text", ""))
        etype = it.get("evidence_type", "freeform_text")
        label = str(it.get("label", etype))[:80]
        excerpt = text[:max_chars]
        reason = ("full text (within bound)" if len(text) <= max_chars
                  else f"first {max_chars} chars of {len(text)} "
                       "(bounded)")
        sha = hashlib.sha256(excerpt.encode("utf-8", "replace")).hexdigest()
        manifest.append({"kind": "text_excerpt", "evidence_type": etype,
                         "label": label, "sha256": sha,
                         "size_bytes": len(excerpt.encode("utf-8",
                                                          "replace")),
                         "reason": reason})
        lines.append(f"--- {label} [{etype}] (sha256 {sha[:12]}…) ---")
        lines.append(excerpt)
        lines.append("")
    return "\n".join(lines), manifest
