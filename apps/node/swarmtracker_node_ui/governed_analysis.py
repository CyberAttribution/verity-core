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


_DENY_AUTH_REASON = (
    "operator authorization not granted for this job (deny by default; "
    "an explicit keyword-only operator_authorized=True from the trusted "
    "local caller is required — truthy strings, numbers, collections, "
    "and omitted/false values are refused)")


def _authorization_granted(operator_authorized):
    """Strict per-job operator intent gate. ONLY boolean True counts.

    LIMITATION: this is a trusted local-caller intent signal, NOT
    authenticated human identity, a session proof, or cryptographic
    approval. Anything with runtime/host access can call these functions
    directly with operator_authorized=True and bypass it; the gate exists
    so the governed service itself never escalates on implicit state
    (model choice, installed credentials, readiness, prior jobs)."""
    return operator_authorized is True


def build_router(generate_fn, selected_model=None, *,
                 workflow="general_evidence", operator_authorized=False):
    """(EscalatingRouter | None, reason). Fail-closed at every layer:
    the reserved-workflow guard (independent of callers), the explicit
    per-job operator authorization gate (deny by default — no
    readiness/policy/provider calls are made without it), Keel policy,
    then the active provider's readiness (ADC + project)."""
    guard_workflow(workflow)
    if not _authorization_granted(operator_authorized):
        return None, _DENY_AUTH_REASON
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
    """MANDATORY authorization audit (Task 3): the authorization row must
    be durably persisted (flush + fsync) BEFORE any cloud SDK generate
    call. Raises LedgerWriteError/ValueError on failure — callers must
    fail closed instead of generating."""
    return keel_events.append_event_mandatory(
        "cloud_escalation_authorized",
        {"workflow": workflow, "provider": provider,
         "artifact_count": len(sent_manifest),
         "artifacts": sent_manifest,
         **(extra or {})})


def audit_result(workflow, ok, error=None, model_used=None,
                 latency_ms=None, authorization_event_id=None,
                 provider=None):
    """Result audit. MANDATORY for honest custody, but a post-call write
    failure cannot undo an already-executed provider call: on failure
    this raises and the caller keeps the truthful provider outcome and
    records the audit failure separately (never reruns inference).
    Correlates the result row with its authorization row via
    authorization_event_id (and provider for provenance)."""
    return keel_events.append_event_mandatory(
        "cloud_escalation_result",
        {"workflow": workflow, "ok": bool(ok),
         "error": error, "model_used": model_used,
         "latency_ms": latency_ms,
         "authorization_event_id": authorization_event_id,
         "provider": provider})


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
                          prompt=None, max_chars=6000,
                          operator_authorized=False):
    """Governed cloud-assist over a BOUNDED text payload. Local fusion is
    the caller's separate, always-local step; this adds a clearly-labeled
    cloud supplement. Fail-soft + truthful. Returns a dict:
    {escalated, ok, text, error, provider, model_used, latency_ms,
     project, region, attempted_models, provider_status, attempts,
      sent_manifest, reason}.

    `provider_attempted` means the provider SDK's generate method was
    INVOKED (`cloud.generate(...)` called). It is NOT proof that bytes
    reached the remote provider: credential resolution, request
    signing, or transport setup can fail before any HTTP call leaves
    the host, and such failures are still reported under
    provider_attempted=True with the SDK error in `error`. Conversely
    provider_attempted=False means no generate call was made at all
    (denied, unconfigured, or router build failed).

    Requires an explicit keyword-only per-job operator authorization
    signal: `operator_authorized=True` (ONLY boolean True counts).
    Deny-by-default — with missing/false/malformed authorization this
    makes ZERO readiness, provider-build, or generate calls and returns
    an honest denied result (no sent artifacts, no provider claim).
    LIMITATION: this is a trusted local-caller intent gate, not
    authenticated human identity or cryptographic approval; direct
    runtime/host callers with code execution can pass True and bypass
    it. Reserved workflows are refused here independent of UI guards
    (ReservedWorkflowError), even when authorized."""
    guard_workflow(workflow)
    if not _authorization_granted(operator_authorized):
        audit_denied(workflow, _DENY_AUTH_REASON)
        return {"escalated": True, "ok": False, "available": False,
                "operator_authorized": False,
                "authorized": False, "denied": True,
                "reason": _DENY_AUTH_REASON, "text": "",
                "sent_manifest": [], "provider": None, "model_used": None,
                "attempted_models": [], "attempts": [],
                "provider_status": None, "latency_ms": None,
                "project": None, "region": None,
                "error": _DENY_AUTH_REASON}
    # Execution authorization is DISTINCT from operator intent: the
    # intent signal only unlocks this check; Keel policy still decides.
    allowed, why = keel_policy.multimodal_escalation_allowed()
    if not allowed:
        audit_denied(workflow, why)
        return {"escalated": True, "ok": False, "available": False,
                "operator_authorized": True,
                "authorized": False, "denied": True,
                "reason": why, "text": "", "sent_manifest": [],
                "provider": None, "model_used": None,
                "attempted_models": [], "attempts": [],
                "provider_status": None, "latency_ms": None,
                "project": None, "region": None, "error": why}
    pcfg = providers.provider_config()
    if not pcfg["ready"]:
        # Unavailability / provider refusal after execution
        # authorization — reported as such, NOT as an authorization
        # denial and NOT as a sent/attempted cloud call.
        audit_denied(workflow, pcfg["reason"])
        return {"escalated": True, "ok": False, "available": False,
                "operator_authorized": True,
                "authorized": True, "denied": False,
                "unavailable": True, "provider_attempted": False,
                "reason": pcfg["reason"], "text": "", "sent_manifest": [],
                    "provider": pcfg.get("provider"), "model_used": None,
                    "attempted_models": [], "attempts": [],
                    "provider_status": None, "latency_ms": None,
                    "project": pcfg.get("project"),
                    "region": pcfg.get("region"), "error": pcfg["reason"]}
    cloud, why = providers.build_cloud_router(selected_model)
    if cloud is None:
        audit_denied(workflow, why)
        return {"escalated": True, "ok": False, "available": False,
                "operator_authorized": True,
                "authorized": True, "denied": False,
                "unavailable": True, "provider_attempted": False,
                "reason": why, "text": "", "sent_manifest": [],
                "provider": pcfg.get("provider"), "model_used": None,
                "attempted_models": [], "attempts": [],
                "provider_status": None, "latency_ms": None,
                "project": pcfg.get("project"),
                "region": pcfg.get("region"), "error": why}
    digest, manifest = bounded_text_payload(items, max_chars=max_chars)
    # Task 3 — MANDATORY ledger authorization BEFORE any generate call.
    # A write failure here means ZERO cloud SDK invocations: the job
    # returns honest audit-write-failed metadata with no sent artifacts
    # and no provider-attempt claim (not "unavailable" in the provider
    # sense, and not a policy denial either).
    provider_name = pcfg.get("provider") or "vertex"
    try:
        auth_eid = audit_authorized(workflow, provider_name, manifest)
    except (keel_events.LedgerWriteError, ValueError, OSError) as exc:
        return {"escalated": True, "ok": False, "available": False,
                "operator_authorized": True,
                "authorized": True, "denied": False,
                "unavailable": False, "provider_attempted": False,
                "audit_write_failed": True,
                "reason": ("authorization ledger write failed — cloud "
                           "assist not attempted: " + str(exc)[:200]),
                "error": "audit_write_failed", "text": "",
                "sent_manifest": [], "provider": provider_name,
                "authorization_event_id": None,
                "model_used": None, "attempted_models": [],
                "attempts": [], "provider_status": None,
                "latency_ms": None,
                "project": pcfg.get("project"),
                "region": pcfg.get("region")}
    full_prompt = (prompt or _GOVERNED_TEXT_PROMPT) + "\n\n" + digest
    try:
        out = cloud.generate(full_prompt) or {}
    except Exception as exc:
        # A raised SDK/transport error is still one attempted call.
        # Record its failed outcome; never rerun it merely for auditing.
        out = {"ok": False, "response": "",
               "error": f"provider SDK raised {type(exc).__name__}: {str(exc)[:300]}",
               "provider": provider_name,
               "attempted_models": [], "attempts": []}
    ok = bool(out.get("ok"))
    # Post-call result audit: a write failure CANNOT undo the provider
    # call that already happened. Keep the truthful provider outcome and
    # surface the audit-recording failure as a separate flag; never
    # rerun inference to "fix" the ledger.
    audit_write_failed = False
    try:
        audit_result(workflow, ok, error=out.get("error"),
                     model_used=out.get("model_used"),
                     latency_ms=out.get("latency_ms"),
                     authorization_event_id=auth_eid,
                     provider=out.get("provider") or provider_name)
    except (keel_events.LedgerWriteError, ValueError, OSError):
        audit_write_failed = True
    return {
        "escalated": True, "available": True, "ok": ok,
        "operator_authorized": True, "authorized": True, "denied": False,
        "unavailable": False, "provider_attempted": True,
        "audit_write_failed": audit_write_failed,
        "authorization_event_id": auth_eid,
        "text": out.get("response", "") if ok else "",
        "error": out.get("error") if not ok else None,
        "provider": out.get("provider") or provider_name,
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
    provider provenance + custody as media.

    DENIAL HONESTY: a denied job (missing operator authorization, or a
    policy refusal) is NEVER shaped as a provider attempt or provider
    failure — escalation_provider stays None, attempted_models and
    sent_artifacts stay empty, and `denied`/`reason` carry the truth so
    reports and the canonical case classify it as a denial.

    AVAILABILITY HONESTY: an authorized job that failed BEFORE any
    provider contact (provider unconfigured / router build failed) is
    marked `unavailable: True` with `provider_error: None`, no
    attempted models, and empty custody — explicitly distinct from a
    provider that was actually contacted and returned an error. The
    configured provider name is retained for provenance only.
    `provider_attempted` (from the raw result) means the provider SDK
    generate method was invoked — not that bytes reached the remote
    provider; credential/transport setup can fail pre-HTTP.

    AUDIT-WRITE HONESTY (Task 3): if the mandatory authorization-ledger
    write failed BEFORE any cloud call, the raw result carries
    `audit_write_failed: True` and provider_attempted False; that is
    shaped here as an explicitly local-only outcome — provider stays
    None, `audit_write_failed` propagates so reports and the canonical
    record can state that no cloud assist was attempted because the
    authorization could not be persisted. A post-call result-ledger
    failure (`result_write_failed`) propagates alongside the truthful
    provider outcome: the call already happened and is not rerun or
    hidden."""
    if not res or not res.get("escalated"):
        return {"escalated": False}
    if res.get("audit_write_failed") and not res.get("provider_attempted"):
        # Pre-call ledger failure: fail-closed local-only. Not denied,
        # not unavailable — the authorization record could not be
        # persisted, so nothing was attempted.
        return {
            "escalated": False,    # nothing was engaged; not a real
                                   # escalation attempt
            "audit_write_failed": True,
            "authorization_event_id": res.get("authorization_event_id"),
            "escalation_provider": None,
            "escalation_ok": False,
            "denied": False,
            "unavailable": False,
            "attempted_models": [],
            "model_used": None,
            "latency_ms": None,
            "vertex_project": None,
            "vertex_region": None,
            "attempt_history": [],
            "provider_error": None,
            "provider_status": None,
            "reason": res.get("reason") or "",
            "sent_artifacts": [],
            "custody": {
                "source_evidence": "retained locally; nothing sent",
                "retained_local": "full investigation, evidence package, "
                                  "and canonical case",
                "sent_to_provider": [],
                "sent_but_provider_failed": [],
            },
        }
    ok = bool(res.get("ok"))
    denied = bool(res.get("denied"))
    unavailable = bool(res.get("unavailable"))
    sent = res.get("sent_manifest") or []
    reason = res.get("reason") or ""
    if denied:
        return {
            "escalated": True,
            "denied": True,
            "escalation_denied": True,
            "authorization_event_id": res.get("authorization_event_id"),
            "audit_write_failed": False,
            "result_write_failed":
                bool(res.get("audit_write_failed")),
            "escalation_provider": None,   # no provider was ever engaged
            "escalation_ok": False,
            "multimodal_unavailable": False,
            "attempted_models": [],
            "model_used": None,
            "latency_ms": None,
            "vertex_project": None,
            "vertex_region": None,
            "attempt_history": [],
            "provider_error": None,
            "provider_status": None,
            "reason": reason,
            "sent_artifacts": [],
            "custody": {
                "source_evidence": "retained locally; nothing sent",
                "retained_local": "full investigation, evidence package, "
                                  "and canonical case",
                "sent_to_provider": [],
                "sent_but_provider_failed": [],
            },
        }
    if unavailable:
        return {
            "escalated": True,
            "denied": False,
            "unavailable": True,       # no provider was ever contacted
            "authorization_event_id": res.get("authorization_event_id"),
            "audit_write_failed": False,
            "result_write_failed":
                bool(res.get("audit_write_failed")),
            "escalation_ok": False,
            "escalation_provider": res.get("provider") or "vertex",
            "multimodal_unavailable": False,   # text assist, not multimodal
            "attempted_models": [],
            "model_used": None,
            "latency_ms": None,
            "vertex_project": res.get("project"),
            "vertex_region": res.get("region"),
            "attempt_history": [],
            "provider_error": None,    # nothing failed: no attempt was made
            "provider_status": "unavailable",
            "reason": res.get("reason") or "",
            "sent_artifacts": [],
            "custody": {
                "source_evidence": "retained locally; nothing sent",
                "retained_local": "full investigation, evidence package, "
                                  "and canonical case",
                "sent_to_provider": [],
                "sent_but_provider_failed": [],
            },
        }
    return {
        "escalated": True,
        "denied": False,
        "unavailable": False,
        "escalation_provider": res.get("provider") or "vertex",
        "escalation_ok": ok,
        "authorization_event_id": res.get("authorization_event_id"),
        "audit_write_failed": False,
        "result_write_failed": bool(res.get("audit_write_failed")),
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
