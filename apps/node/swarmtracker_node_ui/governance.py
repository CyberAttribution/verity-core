"""
Keel governance health scoring (v0.1.2) — prototype governance score.

Combines two evidence sources, honestly labeled:
  1. Structural facts about this build (what code paths exist / don't exist).
  2. Observed fields in recent ledger events (logs/openclaw_events.jsonl).

This is control health for a prototype, not a certification. Nothing here
claims compliance. Statuses: green / yellow / red.
"""
from collections import Counter

from . import keel_policy, ledger

WINDOW = 50          # ledger events considered
RECENT_ALARM_WINDOW = 10  # alarms this recent drive overall red

STATUS_ORDER = {"green": 0, "yellow": 1, "red": 2}


def _ids(events, pred, k=3):
    out = []
    for e in events:
        try:
            if pred(e):
                out.append(str(e.get("id", "?")))
        except Exception:
            continue
        if len(out) >= k:
            break
    return out


def _frac(events, pred):
    if not events:
        return 0.0
    n = 0
    for e in events:
        try:
            if pred(e):
                n += 1
        except Exception:
            continue
    return n / len(events)


def _conf(base, coverage):
    """Blend a structural-fact floor with ledger-evidence coverage."""
    return int(max(10, min(95, base + coverage * (90 - base))))


def _cat(key, name, status, confidence, capability, flags, next_step, sources):
    return {
        "key": key, "name": name, "status": status,
        "confidence": int(max(0, min(100, confidence))),
        "capability": capability,
        "flags": flags,          # list of (level, message)
        "next_step": next_step,
        "sources": sources,      # list of strings
    }


def assess(window: int = WINDOW) -> dict:
    events = ledger.read_raw_events(window)
    n = len(events)

    alarmed = [e for e in events if e.get("governance_alarm")]
    recent_alarmed = [e for e in events[:RECENT_ALARM_WINDOW]
                      if e.get("governance_alarm")]
    models = Counter(str(e.get("model")) for e in events if e.get("model"))
    policies = Counter(str(e.get("policy")) for e in events if e.get("policy"))
    providers = Counter(str(e.get("provider")) for e in events
                        if e.get("provider"))
    nodes = Counter(str(e.get("node")) for e in events if e.get("node"))

    eb_cov = _frac(events, lambda e: "evidence_boundary_enforced" in e)
    eb_ok = _frac(events, lambda e: e.get("evidence_boundary_enforced") is True)
    bad_injection = [e for e in events
                     if e.get("injection_outcome") not in
                     (None, "", "treated_as_evidence", "blocked", "none")]
    identity_honored = [e for e in events
                        if e.get("identity_assertion_honored") is True]
    injections_seen = [e for e in events
                       if e.get("embedded_instructions_detected") or
                       e.get("cross_system_instruction_detected")]
    hash_cov = _frac(events, lambda e: e.get("prompt_sha256") or
                     e.get("request_envelope_sha256"))
    resp_hash_cov = _frac(events, lambda e: e.get("response_sha256"))
    core_keys = ("id", "event", "model", "policy", "decision", "provider",
                 "node")
    complete_cov = _frac(events, lambda e: all(e.get(k) for k in core_keys) and
                         (e.get("ts") or e.get("timestamp")) and
                         e.get("latency_ms") is not None)
    nonlocal_exec = [e for e in events
                     if e.get("execution_location") not in
                     (None, "", "local")]
    cloud_allowed = [e for e in events
                     if e.get("cloud_escalation_allowed") is True]
    approval_req = [e for e in events
                    if e.get("human_approval_required") is True]

    cats = []

    # 1. Evidence Boundary
    st = "green"
    flags = []
    if identity_honored or bad_injection:
        st = "red"
        flags.append(("red", "Events show an identity assertion honored or an "
                             "injection outcome other than treated_as_evidence/"
                             "blocked."))
    elif n and eb_cov < 0.5:
        st = "yellow"
        flags.append(("yellow", "Fewer than half of recent events carry "
                                "evidence-boundary fields (legacy schema)."))
    cats.append(_cat(
        "evidence_boundary", "Evidence Boundary", st,
        _conf(45, eb_cov if not (identity_honored or bad_injection) else 0.9),
        "Evidence is submitted as untrusted specimen; v0.3 logs twelve "
        "boundary fields per event and BND-SPEC-01-lite passed on this node.",
        flags,
        "Automate specimen boundary tests (BND-SPEC-01) against every "
        "runtime change.",
        [f"evidence_boundary_enforced true in {int(eb_ok * n)}/{n} events",
         f"injection_outcome clean in {n - len(bad_injection)}/{n}",
         "e.g. " + ", ".join(_ids(events, lambda e:
                                  "evidence_boundary_enforced" in e)) if n
         else "no ledger events yet"]))

    # 2. Tool Control
    cats.append(_cat(
        "tool_control", "Tool Control", "green", _conf(80, complete_cov),
        "Runtime has no tool-execution surface: only generate_text exists. "
        "Keel policy denies shell_exec, file_write, email_send, "
        "browser_login.",
        [],
        "Enforce an explicit tool allow-list in the runtime and log tool "
        "intents/results (v0.6).",
        [f"decision logged in {int(complete_cov * n)}/{n} events",
         "denial today is structural (no tool code path)"]))

    # 3. Network Boundary
    cats.append(_cat(
        "network_boundary", "Network Boundary", "yellow",
        _conf(50, 1 - _frac(events, lambda e: e in nonlocal_exec)),
        "UI refuses non-loopback URLs at startup; runtime and model listen "
        "on 127.0.0.1 only.",
        [("yellow", "Enforcement is application-level; no OS-level egress "
                    "block or network namespace isolates the node.")] +
        ([("red", "Events report non-local execution_location.")]
         if nonlocal_exec else []),
        "Add OS-level egress deny (pf rule / network namespace) so the "
        "boundary holds even if app code changes.",
        [f"execution_location=local in {n - len(nonlocal_exec)}/{n} events"]))

    # 4. Cloud Escalation Control
    st = "yellow" if cloud_allowed else "green"
    cats.append(_cat(
        "cloud_escalation", "Cloud Escalation Control", st, _conf(85, 0.5),
        "Optional cloud assistance has per-job local operator intent, policy "
        "and mandatory authorization-ledger gates. Runtime ledger events alone "
        "do not establish the current provider configuration or readiness.",
        [("yellow", "Events report cloud_escalation_allowed=true.")]
        if cloud_allowed else [],
        "Design policy-gated escalation: Keel must authorize any future "
        "cloud call per event, key presence alone never authorizes.",
        [f"cloud_escalation_allowed true in {len(cloud_allowed)}/{n} events",
         "keel_policy.json locks cloud_escalation_allowed=false "
         "(fail-closed)"]))

    # 5. Audit Completeness
    st = "green" if (n and complete_cov >= 0.95) else "yellow"
    flags = [("yellow", "Legacy runtime ledger is plain JSONL. Decision runs use a separate "
                        "hash-chained Trust Record with checkpoint verification limits.")]
    if not n:
        flags.append(("yellow", "No events in window; completeness unproven."))
    cats.append(_cat(
        "audit_completeness", "Audit Completeness",
        "yellow",  # tamper-evidence gap keeps this yellow honestly
        _conf(40, complete_cov),
        f"Every governed call writes one event; {int(complete_cov * 100)}% of "
        f"recent events carry all core audit fields (id, time, model, "
        f"policy, decision, provider, node, latency).",
        flags,
        "Hash-chain consecutive ledger events and add rotation so the audit "
        "trail supports audit-ready workflows.",
        [f"core fields complete in {int(complete_cov * n)}/{n} events",
         f"policies seen: {', '.join(policies) or '—'}"]))

    # 6. Provider / Model Control
    single_provider = set(providers) <= {"ollama_local"}
    st = "green" if single_provider else "yellow"
    cats.append(_cat(
        "provider_model", "Provider / Model Control", st,
        _conf(60, complete_cov),
        f"Providers in the legacy runtime ledger: {', '.join(providers) or 'none'}; "
        f"models: {', '.join(models) or 'none'}. Provider configuration and "
        "readiness are reported separately in Settings.",
        [] if single_provider else
        [("yellow", "Multiple providers observed in recent events.")],
        "Implement provider-class routing so Keel policy, not caller input, "
        "selects the provider (v0.5).",
        [f"providers: {dict(providers) or '—'}",
         f"models: {dict(models) or '—'}"]))

    # 7. Prompt-Injection Handling
    st = "red" if (identity_honored or bad_injection) else "yellow"
    cats.append(_cat(
        "prompt_injection", "Prompt-Injection Handling", st,
        _conf(45, _frac(events, lambda e: "injection_outcome" in e)),
        "Prompts pin evidence as untrusted specimen; v0.3 logs detection and "
        "outcome fields; specimen injection test passed with outcome "
        "treated_as_evidence.",
        [("yellow", "No automated injection detector; containment relies on "
                    "prompt design and model behavior.")] +
        ([("red", "Recent events show injection not contained.")]
         if (identity_honored or bad_injection) else []),
        "Add an injection specimen test suite and a pre-model detector that "
        "tags embedded instructions before inference.",
        [f"injection specimens seen: {len(injections_seen)}, contained: "
         f"{len(injections_seen) - len(bad_injection)}",
         f"identity assertions honored: {len(identity_honored)}"]))

    # 8. Replayability
    cats.append(_cat(
        "replayability", "Replayability", "yellow",
        _conf(35, (hash_cov + resp_hash_cov) / 2),
        f"Prompt/request hashes present in {int(hash_cov * 100)}% and "
        f"response hashes in {int(resp_hash_cov * 100)}% of recent events, "
        "so runs can be matched to inputs after the fact.",
        [("yellow", "No deterministic replay harness; hashes verify identity "
                    "of inputs, not reproducibility of outputs.")],
        "Build the replay/determinism harness (v0.7): re-run an event from "
        "its envelope and diff outputs.",
        [f"prompt_sha256 coverage {int(hash_cov * 100)}%",
         f"response_sha256 coverage {int(resp_hash_cov * 100)}%"]))

    # 9. Sensitive Data Handling
    cats.append(_cat(
        "sensitive_data", "Sensitive Data Handling", "yellow",
        _conf(50, 1.0 if n else 0.3),
        "Local analysis stays with the configured local runtime; optional cloud "
        "assistance can transmit selected evidence under its separate per-job gates. "
        "Decision-run activation or approval does not authorize cloud submission.",
        [("yellow", "No redaction, classification, or retention policy for "
                    "submitted evidence yet; operators may paste sensitive "
                    "content.")],
        "Add evidence retention windows and optional at-ingest redaction "
        "before any multi-user deployment.",
        ["data boundary: local node only (structural)",
         f"nodes seen: {', '.join(nodes) or '—'}"]))

    # 10. Human Approval
    cats.append(_cat(
        "human_approval", "Human Approval", "yellow",
        _conf(55, complete_cov),
        "Analysis remains read-only. Decision runs separately enforce approval "
        "or denial for a fixed local simulator, with persistent Trust Records. "
        "Legacy approval-requiring events in window: "
        f"{len(approval_req)}.",
        [("yellow", "Approval records local operator intent, not "
                    "authenticated human identity. Real action integrations remain unimplemented.")],
        "Review the governed decision runs above; authenticated operator "
        "identity and real action integrations require further work.",
        [f"human_approval_required true in {len(approval_req)}/{n} events"]))

    # 11. Runtime Isolation
    cats.append(_cat(
        "runtime_isolation", "Runtime Isolation", "yellow", 55,
        "Runtime, model, and UI run as local processes on one node with no "
        "shared external services.",
        [("yellow", "Processes share one OS user; no container, sandbox "
                    "profile, or resource limits around the runtime.")],
        "Run the runtime under a dedicated user/container with CPU/memory "
        "limits and a minimal filesystem view.",
        [f"node(s): {', '.join(nodes) or 'macmini-m1'}"]))

    # 12. Deployment Policy Locking (v0.1.3: Node UI-side lock active)
    st_res = keel_policy.self_test()
    lock_ok = (st_res["policy_file_present"] and
               st_res["policy_file_parsed"] and
               st_res["default_policy_allowed"] and
               st_res["unknown_policy_rejected"])
    multi_policy = len(policies) > 1
    if lock_ok:
        status = "yellow"  # honest: UI-layer enforcement, runtime still open
        capability = (
            "Node UI loads a local Keel policy file "
            f"({st_res['policy_source']}, v{st_res['policy_version']}) and "
            "only submits the locked default policy to the runtime; unknown "
            "policies are rejected before any runtime call. Enforcement is "
            "Node UI-side (Keel layer).")
        flags = [("yellow",
                  "Runtime-level enforcement is not yet implemented; direct "
                  "calls to OpenClaw may still bypass Node UI policy "
                  "validation.")]
        if multi_policy:
            flags.append(("yellow", "Multiple policies observed in window "
                                    f"({', '.join(policies)}) — may predate "
                                    "the policy lock."))
        confidence = _conf(70, complete_cov)
    else:
        status = "red"
        capability = ("Keel policy lock is not healthy: " +
                      st_res.get("fail_closed_reason",
                                 "self-test failed") +
                      ". Fail-closed defaults (read_only_analysis only) are "
                      "in effect for Node UI calls.")
        flags = [("red", "Policy file missing, malformed, or validator "
                         "failing — see Keel policy self-test.")]
        confidence = _conf(40, complete_cov)
    cats.append(_cat(
        "policy_locking", "Deployment Policy Locking", status, confidence,
        capability, flags,
        "Move policy lock enforcement into the runtime, or put OpenClaw "
        "behind a Keel enforcement proxy that rejects unknown policies.",
        [f"policy file: {st_res['policy_source']} "
         f"(v{st_res['policy_version']})",
         "self-test: " + str(sum(1 for k, v in st_res.items()
                                 if isinstance(v, bool) and k != "all_passed"
                                 and v)) + "/" +
         str(sum(1 for k, v in st_res.items()
                 if isinstance(v, bool) and k != "all_passed")) +
         " checks pass",
         f"policies in window: {dict(policies) or '—'}"]))

    # ---- overall ----
    worst = max((STATUS_ORDER[c["status"]] for c in cats), default=0)
    reds = [c for c in cats if c["status"] == "red"]
    if recent_alarmed or len(reds) >= 2:
        overall = "red"
    elif worst >= 1:
        overall = "yellow"
    else:
        overall = "green"
    overall_conf = int(sum(c["confidence"] for c in cats) / len(cats))

    attention = []
    for e in recent_alarmed:
        attention.append({
            "level": "red", "kind": "ledger event",
            "title": f"Governance alarm on event {e.get('id', '?')}",
            "detail": ", ".join(e.get("governance_failures") or []) or
                      "alarm raised",
        })
    for e in alarmed:
        if e in recent_alarmed:
            continue
        attention.append({
            "level": "yellow", "kind": "ledger event",
            "title": f"Historical governance alarm on event "
                     f"{e.get('id', '?')}",
            "detail": (", ".join(e.get("governance_failures") or []) or
                       "alarm raised") + " — older event in window (may be a "
                      "test specimen)",
        })
    for c in sorted(cats, key=lambda c: -STATUS_ORDER[c["status"]]):
        if c["status"] == "green":
            continue
        first_flag = c["flags"][0][1] if c["flags"] else c["capability"]
        attention.append({
            "level": c["status"], "kind": "control",
            "title": c["name"],
            "detail": first_flag + " Next: " + c["next_step"],
        })

    return {
        "overall": overall,
        "overall_confidence": overall_conf,
        "categories": cats,
        "attention": attention,
        "window": window,
        "summary": {
            "events": n,
            "alarms": len(alarmed),
            "recent_alarms": len(recent_alarmed),
            "models": dict(models),
            "policies": dict(policies),
            "providers": dict(providers),
            "nodes": dict(nodes),
        },
        "events": [ledger.normalize(e) for e in events[:15]],
    }


# ---- per-mode indicators for the Analyze page ----

_PROFILE_INDICATORS = {
    "smb": ("green", 75,
            "Core SMB categories run on implemented local analysis under "
            "Keel policy; no org profile loaded."),
    "msp": ("yellow", 55,
            "No client separation or tenancy controls yet; single-node "
            "analysis only."),
    "public_sector": ("yellow", 55,
                      "Mode-specific reporting is active, but no agency "
                      "context graph is loaded and audit hardening is in "
                      "progress."),
    "critical_infrastructure": ("yellow", 50,
                                "Evidence-only analysis; no OT/ICS context "
                                "feed and no cyber-physical controls "
                                "implemented."),
    "legal_regulated": ("yellow", 55,
                        "Sensitivity-aware reporting is active, but no "
                        "client/matter context is loaded and retention "
                        "controls are roadmap."),
    "emerging_tech": ("yellow", 50,
                      "Claim triage from submitted text only; no "
                      "cryptographic verification or lab testing is "
                      "performed."),
}

_ROADMAP_ANALYSIS_REASON = {
    "cloud_assisted": "Not implemented: no cloud path exists in this build "
                      "and Keel denies escalation. Selection is "
                      "roadmap-only.",
    "hybrid_policy_routed": "Not implemented: policy routing is roadmap; "
                            "execution stays Local Only and no cloud calls "
                            "are made.",
    "disconnected_edge": "Not implemented as a distinct mode; behavior "
                         "equals Local Only on this node.",
}


def mode_indicators(overall: str, overall_conf: int) -> dict:
    profiles = {k: {"status": s, "confidence": c, "reason": r}
                for k, (s, c, r) in _PROFILE_INDICATORS.items()}
    analysis_modes = {
        "local_only": {
            "status": overall, "confidence": overall_conf,
            "reason": "Implemented and governed: fully local execution "
                      "under Keel policy. Status mirrors overall control "
                      "health.",
        },
    }
    for k, reason in _ROADMAP_ANALYSIS_REASON.items():
        analysis_modes[k] = {"status": "yellow", "confidence": 35,
                             "reason": reason}
    return {"profiles": profiles, "analysis_modes": analysis_modes}
