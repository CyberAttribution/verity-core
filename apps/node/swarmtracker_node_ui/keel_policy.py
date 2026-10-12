"""
Keel policy lock (v0.1.3) — Node UI-side policy enforcement. Fail-closed.

Loads swarmtracker_node_ui/keel_policy.json and enforces it before any
request reaches the local runtime. If the file is missing, malformed, or
tries to enable capabilities this version does not implement (cloud
escalation, network, non-local analysis modes), the loader rejects it and
falls back to hard-coded fail-closed defaults: read_only_analysis only.

Honest scope: this locks policy at the Node UI / Keel layer. It does NOT
prevent a direct caller from hitting the OpenClaw runtime with an arbitrary
policy string — runtime-level enforcement is the next hardening step and
the governance scorecard says so.
"""
import json
import os
from pathlib import Path

from . import config


class _PolicyParseError(ValueError):
    """Raised by the strict JSON decoder for non-conforming documents."""


# Maximum structural nesting depth accepted for a policy document. Deeper
# documents are rejected before any recursive processing (copy/validate).
_MAX_DEPTH = 64


def _pairs_no_duplicates(pairs):
    """object_pairs_hook that rejects duplicate JSON object keys."""
    out = {}
    for k, v in pairs:
        if k in out:
            raise _PolicyParseError(f"duplicate JSON object key: {k!r}")
        out[k] = v
    return out


def _reject_nonfinite_constant(name):
    """parse_constant hook: NaN/Infinity/-Infinity are never valid policy."""
    raise _PolicyParseError(f"non-finite JSON number not allowed: {name}")


def _finite_float(value: str) -> float:
    """parse_float hook: finite-syntax overflow (1e999) yields float
    inf in Python; policy numbers must be finite."""
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise _PolicyParseError(f"non-finite JSON number not allowed: "
                                f"{value}")
    return out


def _strict_loads(text: str):
    """json.loads with duplicate-key and non-finite-number rejection."""
    return json.loads(
        text,
        object_pairs_hook=_pairs_no_duplicates,
        parse_constant=_reject_nonfinite_constant,
        parse_float=_finite_float,
    )


def _is_json_container(value) -> bool:
    return isinstance(value, (dict, list))


def _check_depth(value) -> bool:
    """True when the document nests no deeper than _MAX_DEPTH."""
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > _MAX_DEPTH:
            return False
        if isinstance(current, dict):
            stack.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, list):
            stack.extend((v, depth + 1) for v in current)
    return True


def _deep_copy(value):
    """Isolated copy of JSON-shaped data (dicts/lists, scalar leaves)."""
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value

POLICY_PATH = Path(os.environ.get(
    "NODE_UI_KEEL_POLICY", str(config.PKG_DIR / "keel_policy.json")))

# Fail-closed defaults, used whenever the policy file cannot be trusted.
FAILSAFE = {
    "policy_profile": "read_only_analysis",
    "allowed_policies": ["read_only_analysis"],
    "default_policy": "read_only_analysis",
    "deny_unknown_policies": True,
    "analysis_modes_enabled": ["local_only"],
    "cloud_escalation_allowed": False,
    "network_allowed": False,
    "tools_allowed": list(config.TOOLS_ALLOWED),
    "tools_denied": list(config.TOOLS_DENIED),
    "evidence_role": config.EVIDENCE_ROLE,
    "data_boundary": config.DATA_BOUNDARY,
    "policy_source": "failsafe defaults (policy file missing or invalid)",
    "policy_version": "0.1.3-failsafe",
}

_REQUIRED_KEYS = (
    "policy_profile", "allowed_policies", "default_policy",
    "deny_unknown_policies", "analysis_modes_enabled",
    "cloud_escalation_allowed", "network_allowed",
    "tools_allowed", "tools_denied",
)

# Capabilities this build does not implement; a policy file trying to
# enable them is treated as invalid (fail closed, do not partially honor).
_EXECUTABLE_ANALYSIS_MODES = {"local_only"}

# Policy vocabulary this release actually implements. The runtime accepts
# only read-only local analysis; any other name (e.g. "admin_override")
# is not a privilege this policy file can grant, so it is rejected here.
# Note: a Node-local policy file is a boundary, not proof of any remote
# action privilege — per-job operator authorization still applies.
_EXECUTABLE_POLICIES = {"read_only_analysis"}

# v0.2.0 media-evidence policy vocabulary. The media section is OPTIONAL
# and fail-closed: absent/invalid section => all media capabilities
# denied. `network_allowed` stays false — governed_egress.media_retrieval
# is the single, narrow, operator-initiated exception, and only through
# registered adapters/tools named here.
_MEDIA_TOOLS_KNOWN = {"ffmpeg", "ffprobe", "exiftool", "yt-dlp"}
_EGRESS_ADAPTERS_KNOWN = {"youtube"}
_EGRESS_TOOLS_KNOWN = {"yt-dlp"}
_ESCALATION_PROVIDERS_KNOWN = {"vertex", "gemini"}
_ESCALATION_KINDS_KNOWN = {"frame", "audio_track", "source_image",
                           "source_audio"}


def _is_str_list(value) -> bool:
    return (isinstance(value, list) and
            all(isinstance(v, str) for v in value))


def _subset_of(value, known) -> bool:
    """Type-safe subset check: elements must be known strings, so
    unhashable nested containers can never reach set() membership."""
    return (_is_str_list(value) and
            all(v in known for v in value))


def _validate_media_section(raw: dict) -> list:
    """Additive validation. Empty list = acceptable (which includes 'no
    media section at all' — that simply disables media capabilities)."""
    me = raw.get("media_evidence")
    if me is None:
        return []
    problems = []
    if not isinstance(me, dict):
        return ["media_evidence must be an object"]
    if not isinstance(me.get("enabled"), bool):
        problems.append("media_evidence.enabled must be boolean")
    tools = me.get("local_tools_allowed")
    if not _subset_of(tools, _MEDIA_TOOLS_KNOWN):
        problems.append("media_evidence.local_tools_allowed must be a "
                        "subset of: " + ", ".join(sorted(_MEDIA_TOOLS_KNOWN)))
    for flag in ("temp_dir_only", "delete_media_after_analysis"):
        value = me.get(flag)
        if not isinstance(value, bool) or value is not True:
            problems.append(f"media_evidence.{flag} must be boolean true")
    ge = me.get("governed_egress")
    if ge is not None and not isinstance(ge, dict):
        problems.append("governed_egress must be an object")
        ge = None
    ge = ge if isinstance(ge, dict) else {}
    eg = ge.get("media_retrieval")
    if eg is not None:
        if not isinstance(eg, dict):
            problems.append("governed_egress.media_retrieval must be an "
                            "object")
        else:
            if not isinstance(eg.get("enabled"), bool):
                problems.append("media_retrieval.enabled must be boolean")
            if eg.get("enabled") is True and \
                    eg.get("operator_initiated_only") is not True:
                problems.append("media_retrieval requires "
                                "operator_initiated_only: true")
            if not _subset_of(eg.get("adapters"), _EGRESS_ADAPTERS_KNOWN):
                problems.append("media_retrieval.adapters must be a "
                                "subset of: "
                                + ", ".join(sorted(_EGRESS_ADAPTERS_KNOWN)))
            if not _subset_of(eg.get("tools"), _EGRESS_TOOLS_KNOWN):
                problems.append("media_retrieval.tools must be a subset "
                                "of: " + ", ".join(sorted(_EGRESS_TOOLS_KNOWN)))
    esc = ge.get("multimodal_escalation")
    if esc is not None:
        if not isinstance(esc, dict):
            problems.append("governed_egress.multimodal_escalation must "
                            "be an object")
        else:
            if not isinstance(esc.get("enabled"), bool):
                problems.append("multimodal_escalation.enabled must be "
                                "boolean")
            if esc.get("enabled") is True:
                provider = esc.get("provider")
                if not isinstance(provider, str) or \
                        provider not in _ESCALATION_PROVIDERS_KNOWN:
                    problems.append(
                        "multimodal_escalation.provider must be one of: "
                        + ", ".join(sorted(_ESCALATION_PROVIDERS_KNOWN)))
                if esc.get("operator_authorization_required") is not True:
                    problems.append("multimodal_escalation requires "
                                    "operator_authorization_required: "
                                    "true")
                if esc.get("artifacts_only") is not True:
                    problems.append("multimodal_escalation requires "
                                    "artifacts_only: true (the source "
                                    "video/evidence package never "
                                    "leaves the node)")
                if esc.get("default_mode") != "local_only":
                    problems.append("multimodal_escalation.default_mode "
                                    "must be local_only")
                if not _subset_of(esc.get("artifact_kinds_allowed"),
                                  _ESCALATION_KINDS_KNOWN):
                    problems.append(
                        "multimodal_escalation.artifact_kinds_allowed "
                        "must be a subset of: "
                        + ", ".join(sorted(_ESCALATION_KINDS_KNOWN)))
    return problems


def _validate_structure(raw: dict) -> list:
    """Return a list of problems; empty list means the file is acceptable."""
    problems = []
    if not isinstance(raw, dict):
        return ["policy root is not a JSON object"]
    if not _check_depth(raw):
        return [f"policy nesting exceeds maximum depth {_MAX_DEPTH}"]
    for k in _REQUIRED_KEYS:
        if k not in raw:
            problems.append(f"missing key: {k}")
    if problems:
        return problems

    profile = raw["policy_profile"]
    if not isinstance(profile, str) or profile not in _EXECUTABLE_POLICIES:
        problems.append(
            "policy_profile must be one of: "
            + ", ".join(sorted(_EXECUTABLE_POLICIES))
            + " (this release implements local read-only analysis only)")

    allowed = raw["allowed_policies"]
    if (not isinstance(allowed, list) or not allowed or
            not _subset_of(allowed, _EXECUTABLE_POLICIES)):
        problems.append(
            "allowed_policies must be a non-empty list of policy names "
            "from: " + ", ".join(sorted(_EXECUTABLE_POLICIES))
            + " (a local policy file cannot grant broader policies)")
    elif not isinstance(raw["default_policy"], str) or \
            raw["default_policy"] not in allowed:
        problems.append("default_policy is not in allowed_policies")

    if raw.get("deny_unknown_policies") is not True:
        problems.append("deny_unknown_policies must be true in v0.1.3")
    if raw.get("cloud_escalation_allowed") is not False:
        problems.append("cloud_escalation_allowed must be false in v0.1.3")
    if raw.get("network_allowed") is not False:
        problems.append("network_allowed must be false in v0.1.3")

    modes = raw.get("analysis_modes_enabled")
    if not isinstance(modes, list) or not modes or \
            not all(isinstance(m, str) for m in modes):
        problems.append("analysis_modes_enabled must be a non-empty list "
                        "of mode names")
    elif not set(modes) <= _EXECUTABLE_ANALYSIS_MODES:
        extra = sorted(set(modes) - _EXECUTABLE_ANALYSIS_MODES)
        problems.append("analysis_modes_enabled enables unimplemented "
                        f"modes: {', '.join(extra)}")

    for k in ("tools_allowed", "tools_denied"):
        if not _is_str_list(raw.get(k)) or not raw[k]:
            problems.append(f"{k} must be a non-empty list of tool names")
    tools_allowed = raw.get("tools_allowed")
    tools_denied = raw.get("tools_denied")
    if _is_str_list(tools_allowed) and tools_allowed and \
            not set(tools_allowed) <= set(config.TOOLS_ALLOWED):
        extra = sorted(set(tools_allowed) - set(config.TOOLS_ALLOWED))
        problems.append("tools_allowed names tools this build does not "
                        f"implement: {', '.join(extra)}")
    if _is_str_list(tools_denied) and tools_denied and \
            not set(config.TOOLS_DENIED) <= set(tools_denied):
        missing = sorted(set(config.TOOLS_DENIED) - set(tools_denied))
        problems.append("tools_denied must keep all build-required denies: "
                        f"{', '.join(missing)}")
    if _is_str_list(tools_allowed) and _is_str_list(tools_denied) and \
            tools_allowed and tools_denied and \
            set(tools_allowed) & set(tools_denied):
        overlap = sorted(set(tools_allowed) & set(tools_denied))
        problems.append("tools_allowed and tools_denied overlap: "
                        f"{', '.join(overlap)}")
    problems.extend(_validate_media_section(raw))
    return problems


def load_policy() -> tuple:
    """Load and validate the policy file.

    Returns (policy: dict, ok: bool, error: str | None).
    On any problem the returned policy is the fail-closed FAILSAFE copy.
    """
    try:
        text = POLICY_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _deep_copy(FAILSAFE), False, f"policy file missing: {POLICY_PATH}"
    except (OSError, UnicodeDecodeError) as e:
        return _deep_copy(FAILSAFE), False, \
            f"policy file unreadable or malformed: {e}"
    try:
        raw = _strict_loads(text)
    except (ValueError, RecursionError) as e:
        # JSONDecodeError, duplicate keys, non-finite numbers, and
        # pathologically deep documents all fail closed here.
        return _deep_copy(FAILSAFE), False, \
            f"policy file unreadable or malformed: {e}"

    problems = _validate_structure(raw)
    if problems:
        return _deep_copy(FAILSAFE), False, \
            "invalid policy file: " + "; ".join(problems)

    policy = _deep_copy(FAILSAFE)
    policy.update(raw)
    return policy, True, None


def default_policy() -> str:
    policy, _, _ = load_policy()
    return policy["default_policy"]


def validate_policy(policy_name: str) -> tuple:
    """(allowed: bool, reason: str). Unknown policies are always rejected."""
    policy, ok, err = load_policy()
    name = (policy_name or "").strip()
    if not name:
        return True, f"no policy requested; locked default " \
                     f"'{policy['default_policy']}' applies"
    if name in policy["allowed_policies"]:
        return True, f"policy '{name}' is in the Keel allow-list"
    reason = f"unknown policy '{name}' rejected by Keel policy lock"
    if not ok:
        reason += " (fail-closed defaults in effect: " + str(err) + ")"
    return False, reason


def enforced_policy(requested: str = None) -> str:
    """The policy that may actually be sent to the runtime.

    Allowed requested policies pass through; anything else resolves to the
    locked default. Callers that must hard-reject (the analyze flow) use
    validate_policy() first.
    """
    allowed, _ = validate_policy(requested or "")
    policy, _, _ = load_policy()
    if requested and allowed and requested.strip():
        return requested.strip()
    return policy["default_policy"]


def is_analysis_mode_enabled(mode: str) -> bool:
    policy, _, _ = load_policy()
    return mode in policy["analysis_modes_enabled"]


def media_policy() -> dict:
    """The effective media-evidence policy. Fail-closed: anything absent
    or invalid reads as disabled."""
    policy, ok, _ = load_policy()
    me = policy.get("media_evidence") if ok else None
    if not isinstance(me, dict) or me.get("enabled") is not True:
        return {"enabled": False, "local_tools_allowed": [],
                "temp_dir_only": True,
                "delete_media_after_analysis": True,
                "retrieval_enabled": False, "retrieval_adapters": [],
                "retrieval_tools": [], "scope_note": "",
                "escalation_enabled": False, "escalation_provider": "",
                "escalation_kinds": [], "escalation_scope_note": ""}
    eg = (me.get("governed_egress") or {}).get("media_retrieval") or {}
    esc = (me.get("governed_egress") or {}).get(
        "multimodal_escalation") or {}
    return {
        "enabled": True,
        "local_tools_allowed": list(me.get("local_tools_allowed") or []),
        "temp_dir_only": me.get("temp_dir_only") is True,
        "delete_media_after_analysis":
            me.get("delete_media_after_analysis") is True,
        "retrieval_enabled": eg.get("enabled") is True,
        "retrieval_adapters": list(eg.get("adapters") or []),
        "retrieval_tools": list(eg.get("tools") or []),
        "scope_note": str(eg.get("scope_note") or ""),
        "escalation_enabled": esc.get("enabled") is True,
        "escalation_provider": str(esc.get("provider") or ""),
        "escalation_kinds": list(esc.get("artifact_kinds_allowed")
                                 or []),
        "escalation_scope_note": str(esc.get("scope_note") or ""),
    }


def multimodal_escalation_allowed() -> tuple:
    """(allowed: bool, reason: str). Policy-level gate only — the
    per-job operator authorization and provider-key checks happen at
    the call site. Fail-closed."""
    mp = media_policy()
    if not mp["enabled"]:
        return False, ("media evidence capabilities are disabled by the "
                       "Keel policy (fail-closed)")
    if not mp["escalation_enabled"]:
        return False, ("multimodal cloud escalation is disabled by the "
                       "Keel policy; analysis stays Local Only")
    return True, ("available on explicit per-job operator authorization: "
                  "extracted artifacts only, to "
                  + (mp["escalation_provider"] or "?")
                  + "; evidence package, case, and audit trail stay "
                    "local")


def sensitive_media_policy() -> dict:
    """Reserved sensitive-media namespace (v0.5.0 preparation). Fail-safe
    defaults: workflow disabled, publication hard-disabled, exports must
    exclude embedded media. Present so the guards/exports/tests exist
    before the workflow does."""
    policy, ok, _ = load_policy()
    sm = policy.get("sensitive_media") if ok else None
    if not isinstance(sm, dict):
        sm = {}
    return {
        "enabled": sm.get("enabled") is True,   # default False
        "workflow_id": sm.get("workflow_id",
                              "intimate_image_abuse_assessment"),
        "publication_disabled": sm.get("publication_disabled", True)
        is not False,
        "exports_exclude_embedded_media":
            sm.get("exports_exclude_embedded_media", True) is not False,
        "restricted_handling_required_default":
            sm.get("restricted_handling_required_default", True)
            is not False,
    }


def sensitive_media_publication_allowed() -> bool:
    """Always False in v0.5.0 — publication of sensitive media is
    hard-disabled regardless of any normal gallery setting."""
    return False


def sensitive_media_enabled() -> bool:
    return sensitive_media_policy()["enabled"]


def media_retrieval_allowed() -> tuple:
    """(allowed: bool, reason: str) for operator-initiated media-URL
    retrieval through a registered adapter."""
    mp = media_policy()
    if not mp["enabled"]:
        return False, ("media evidence capabilities are disabled by the "
                       "Keel policy (fail-closed)")
    if not mp["retrieval_enabled"]:
        return False, ("governed media-retrieval egress is disabled by "
                       "the Keel policy; upload the file instead")
    return True, ("governed egress permitted: operator-initiated media "
                  "retrieval via " + ", ".join(mp["retrieval_adapters"])
                  + " adapter(s) only")


def media_tool_allowed(name: str) -> bool:
    return name in media_policy()["local_tools_allowed"]


def policy_summary() -> dict:
    """Display fields for Settings / Governance / Keel Trace."""
    policy, ok, err = load_policy()
    return {
        "ok": ok,
        "error": err,
        "profile": policy["policy_profile"],
        "default_policy": policy["default_policy"],
        "allowed_policies": policy["allowed_policies"],
        "deny_unknown": policy["deny_unknown_policies"],
        "analysis_modes_enabled": policy["analysis_modes_enabled"],
        "cloud_escalation_allowed": policy["cloud_escalation_allowed"],
        "network_allowed": policy["network_allowed"],
        "tools_allowed": policy["tools_allowed"],
        "tools_denied": policy["tools_denied"],
        "evidence_role": policy.get("evidence_role", config.EVIDENCE_ROLE),
        "data_boundary": policy.get("data_boundary", config.DATA_BOUNDARY),
        "source": policy.get("policy_source", str(POLICY_PATH)),
        "version": policy.get("policy_version", "unknown"),
        "media": media_policy(),
    }


def self_test() -> dict:
    """Node UI-side policy lock self-test. No network, no cloud, no runtime
    calls — pure local checks against the policy file and validator."""
    file_present = POLICY_PATH.exists()
    parsed = False
    if file_present:
        try:
            json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            parsed = True
        except (json.JSONDecodeError, OSError):
            parsed = False

    policy, ok, err = load_policy()
    default_ok, _ = validate_policy(policy["default_policy"])
    unknown_rejected = not validate_policy("__keel_selftest_unknown__")[0]

    results = {
        "policy_file_present": bool(file_present),
        "policy_file_parsed": bool(parsed and ok),
        "default_policy_allowed": bool(default_ok),
        "unknown_policy_rejected": bool(unknown_rejected),
        "cloud_escalation_disabled":
            policy["cloud_escalation_allowed"] is False,
        "network_disabled": policy["network_allowed"] is False,
        "local_only_enabled":
            set(policy["analysis_modes_enabled"]) == {"local_only"},
        "tools_allow_deny_present":
            bool(policy["tools_allowed"]) and bool(policy["tools_denied"]),
    }
    # v0.2.0 media-evidence checks. Pass when the section is absent
    # (media simply disabled) or present-and-scoped; fail only when a
    # policy tries to widen beyond the known-safe vocabulary (which
    # load_policy already rejects into failsafe).
    mp = media_policy()
    results["media_tools_scoped"] = (
        set(mp["local_tools_allowed"]) <= _MEDIA_TOOLS_KNOWN)
    results["media_temp_dir_and_cleanup_locked"] = (
        mp["temp_dir_only"] and mp["delete_media_after_analysis"])
    results["media_egress_scoped_or_disabled"] = (
        (not mp["retrieval_enabled"]) or
        (set(mp["retrieval_adapters"]) <= _EGRESS_ADAPTERS_KNOWN and
         set(mp["retrieval_tools"]) <= _EGRESS_TOOLS_KNOWN))
    # v0.3.0: multimodal escalation must be disabled, or scoped to a
    # known provider with artifacts-only + per-job operator gating
    # (structural validation already fail-closes wider policies).
    esc_raw = ((policy.get("media_evidence") or {}).get(
        "governed_egress") or {}).get("multimodal_escalation") or {}
    results["media_escalation_scoped_or_disabled"] = (
        (not mp["escalation_enabled"]) or
        (mp["escalation_provider"] in _ESCALATION_PROVIDERS_KNOWN and
         set(mp["escalation_kinds"]) <= _ESCALATION_KINDS_KNOWN and
         esc_raw.get("artifacts_only") is True))
    results["media_escalation_operator_gated_local_default"] = (
        (not mp["escalation_enabled"]) or
        (esc_raw.get("operator_authorization_required") is True and
         esc_raw.get("default_mode") == "local_only"))
    results["general_network_still_disabled"] = (
        policy["network_allowed"] is False)
    results["all_passed"] = all(results.values())
    results["enforcement_layer"] = "node_ui_keel"
    results["runtime_enforcement"] = "pending"
    results["policy_source"] = policy.get("policy_source", str(POLICY_PATH))
    results["policy_version"] = policy.get("policy_version", "unknown")
    if err:
        results["fail_closed_reason"] = err
    return results
