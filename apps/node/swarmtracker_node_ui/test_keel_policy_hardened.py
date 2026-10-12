"""
Focused regression tests for the hardened keel_policy loader (Task 2).

Covers: strict JSON (duplicate keys, NaN/Infinity, invalid UTF-8),
malformed/malicious shapes (non-dict roots, nested containers, unhashable
elements), integer masquerading as boolean, unsupported executable
policies (admin_override), deep nesting, FAILSAFE isolation, missing /
disabled media sections, and unchanged acceptance of the shipped v0.5.0
policy.

Run: python -m pytest swarmtracker_node_ui/tests_keel_policy_hardened.py -q
"""
import contextlib
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_T = tempfile.mkdtemp()
os.environ.setdefault("NODE_UI_DATA_DIR", _T)
os.environ.setdefault("NODE_UI_DB", os.path.join(_T, "t.db"))
os.environ.setdefault("NODE_UI_MEDIA_LEDGER", os.path.join(_T, "k.jsonl"))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "swarmtracker-main")

from swarmtracker_node_ui import config, keel_policy  # noqa: E402


@contextlib.contextmanager
def _write_policy(payload, encoding="utf-8"):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode(encoding)
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    old = keel_policy.POLICY_PATH
    keel_policy.POLICY_PATH = Path(path)
    try:
        yield
    finally:
        keel_policy.POLICY_PATH = old
        os.unlink(path)


def _use(payload, encoding="utf-8"):
    return _write_policy(payload, encoding)


# ---------------------------------------------------------------- shipped
def test_shipped_v050_policy_still_accepted():
    policy, ok, err = keel_policy.load_policy()
    assert ok is True and err is None
    assert policy["policy_profile"] == "read_only_analysis"
    assert policy["default_policy"] == "read_only_analysis"
    assert policy["cloud_escalation_allowed"] is False
    assert policy["network_allowed"] is False
    assert policy["analysis_modes_enabled"] == ["local_only"]
    # governed media section of the shipped file stays scoped
    mp = policy["media_evidence"]
    assert mp["enabled"] is True
    assert set(mp["local_tools_allowed"]) <= keel_policy._MEDIA_TOOLS_KNOWN
    assert mp["temp_dir_only"] is True
    assert mp["delete_media_after_analysis"] is True


def test_failsafe_matches_config_constants():
    assert keel_policy.FAILSAFE["tools_allowed"] == config.TOOLS_ALLOWED
    assert keel_policy.FAILSAFE["tools_denied"] == config.TOOLS_DENIED
    assert keel_policy.FAILSAFE["evidence_role"] == config.EVIDENCE_ROLE
    assert keel_policy.FAILSAFE["data_boundary"] == config.DATA_BOUNDARY


# ------------------------------------------------------- malformed files
def test_missing_file_returns_failsafe():
    keel_policy.POLICY_PATH = Path(
        tempfile.mkdtemp()) / "no_such_policy.json"
    try:
        policy, ok, err = keel_policy.load_policy()
        assert ok is False and "missing" in err
        assert policy["policy_profile"] == "read_only_analysis"
        assert policy["network_allowed"] is False
    finally:
        keel_policy.POLICY_PATH = Path(__file__).parent / "keel_policy.json"


def test_invalid_utf8_fails_closed_with_reason():
    with _use(b'{"policy_profile": "\xff\xfe"}', encoding=None):
        policy, ok, err = keel_policy.load_policy()
    assert ok is False
    assert policy == keel_policy._deep_copy(keel_policy.FAILSAFE)
    assert err and "unreadable or malformed" in err


def test_nan_rejected():
    with _use('{"policy_profile": NaN}'):
        policy, ok, err = keel_policy.load_policy()
    assert ok is False and "unreadable or malformed" in err


def test_infinity_rejected():
    with _use('{"policy_profile": Infinity}'):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "unreadable or malformed" in err


@pytest.mark.parametrize("literal", ["1e999", "-1e999", "1E400"])
def test_finite_syntax_overflow_rejected(literal):
    """1e999 parses to float inf via finite syntax; must fail closed."""
    with _use(f'{{"policy_profile": {literal}}}'):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "non-finite" in err


def test_finite_syntax_overflow_in_nested_unknown_field_rejected():
    with _use('{"policy_profile": "read_only_analysis", '
              '"future_field": {"x": [-1e999]}}'):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "non-finite" in err


def test_finite_floats_still_accepted():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["future_numeric_field"] = 1.5e-10
    with _use(base):
        _, ok, _ = keel_policy.load_policy()
    assert ok is True


def test_duplicate_keys_rejected():
    doc = ('{"policy_profile": "read_only_analysis", '
           '"policy_profile": "admin_override"}')
    with _use(doc):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "duplicate" in err


def test_truncated_json_rejected():
    with _use('{"policy_profile": "read_only_an'):
        _, ok, err = keel_policy.load_policy()
    assert ok is False


def test_deeply_nested_document_rejected():
    payload = ('{"policy_profile": {"a": ' * 200) + '1' + ("}" * 200) + '}'
    with _use(payload):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "depth" in err or "malformed" in err


# ----------------------------------------------------- structural shapes
def test_root_array_rejected():
    with _use(["read_only_analysis"]):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "root is not a JSON object" in err


def test_root_scalar_rejected():
    with _use('"read_only_analysis"'):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "root is not a JSON object" in err


def test_allowed_policies_with_nested_container_rejected():
    """Regression: unhashable nested elements previously raised TypeError
    inside set()/membership checks; must now fail closed with a reason."""
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["allowed_policies"] = [["admin_override"]]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "allowed_policies" in err


def test_analysis_modes_with_nested_container_rejected():
    """Regression: [[\"x\"]] previously crashed with TypeError: unhashable."""
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["analysis_modes_enabled"] = [["local_only"]]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "analysis_modes_enabled" in err


def test_tools_allowed_with_nested_container_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["tools_allowed"] = [{"shell_exec": True}]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "tools_allowed" in err


def test_tools_allowed_unimplemented_tool_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["tools_allowed"] = ["shell_exec"]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "does not implement: shell_exec" in err


def test_tools_allowed_partial_extension_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["tools_allowed"] = ["generate_text", "file_write"]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "does not implement: file_write" in err


def test_tools_denied_missing_build_required_deny_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["tools_denied"] = ["shell_exec"]  # drops the other three
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "build-required denies" in err and "file_write" in err


def test_tools_allowed_and_denied_overlap_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["tools_denied"] = list(base["tools_denied"]) + ["generate_text"]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "overlap" in err and "generate_text" in err


def test_v050_tools_contract_matches_build_config():
    """The shipped policy's tool fields exactly match this build's
    config contract; this guards accidental doc/drift divergence."""
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    assert base["tools_allowed"] == config.TOOLS_ALLOWED
    assert set(config.TOOLS_DENIED) <= set(base["tools_denied"])
    assert not set(base["tools_allowed"]) & set(base["tools_denied"])
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is True and err is None


def test_media_local_tools_with_nested_container_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["media_evidence"]["local_tools_allowed"] = [["local_captioning"]]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "local_tools_allowed" in err


# --------------------------------------------------- privilege escalation
def test_admin_override_profile_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["policy_profile"] = "admin_override"
    with _use(base):
        policy, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "policy_profile" in err
    assert policy["policy_profile"] == "read_only_analysis"


def test_admin_override_in_allowed_policies_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["allowed_policies"] = ["read_only_analysis", "admin_override"]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "allowed_policies" in err
    assert "cannot grant broader" in err


def test_admin_override_as_default_policy_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["default_policy"] = "admin_override"
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "default_policy" in err


def test_unimplemented_analysis_mode_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["analysis_modes_enabled"] = ["local_only", "cloud_escalation"]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "unimplemented" in err


# ------------------------------------------------------- type confusion
@pytest.mark.parametrize("field", ["enabled", "temp_dir_only",
                                   "delete_media_after_analysis"])
def test_integer_masquerading_as_boolean_rejected(field):
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["media_evidence"][field] = 1  # truthy int, not a real bool
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "boolean" in err


def test_integer_true_on_media_retrieval_enabled_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["media_evidence"]["governed_egress"]["media_retrieval"][
        "enabled"] = 1
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False and "boolean" in err


def test_governed_egress_as_list_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["media_evidence"]["governed_egress"] = ["media_retrieval"]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "governed_egress must be an object" in err


@pytest.mark.parametrize("provider", [["vertex"], {"provider": 1}, 3.14,
                                      None])
def test_unhashable_or_nonscalar_escalation_provider_rejected(provider):
    """Regression: a non-string provider previously raised TypeError on
    set membership; must fail closed with a reason instead."""
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    esc = base["media_evidence"]["governed_egress"]["multimodal_escalation"]
    esc["enabled"] = True
    esc["provider"] = provider
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "multimodal_escalation.provider" in err


def test_escalation_artifact_kinds_with_nested_container_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    esc = base["media_evidence"]["governed_egress"]["multimodal_escalation"]
    esc["enabled"] = True
    esc["artifact_kinds_allowed"] = [["transcript"]]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "artifact_kinds_allowed" in err


def test_media_retrieval_adapters_with_nested_container_rejected():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    ret = base["media_evidence"]["governed_egress"]["media_retrieval"]
    ret["adapters"] = [{"url": "https://example.invalid"}]
    with _use(base):
        _, ok, err = keel_policy.load_policy()
    assert ok is False
    assert "media_retrieval.adapters" in err


# --------------------------------------------------- missing media section
def test_missing_media_section_loads_with_media_disabled():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base.pop("media_evidence")
    with _use(base):
        policy, ok, err = keel_policy.load_policy()
        assert ok is True and err is None
        mp = keel_policy.media_policy()
    assert mp["enabled"] is False
    assert mp["local_tools_allowed"] == []
    assert mp["retrieval_enabled"] is False
    assert mp["escalation_enabled"] is False


def test_media_evidence_enabled_false_disables_media():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    base["media_evidence"]["enabled"] = False
    with _use(base):
        _, ok, _ = keel_policy.load_policy()
        assert ok is True
        assert keel_policy.media_policy()["enabled"] is False


# -------------------------------------------------------- FAILSAFE safety
def test_failsafe_return_not_mutable_shared_state():
    p1, _, _ = keel_policy.load_policy()  # missing file -> failsafe
    saved = keel_policy.POLICY_PATH
    keel_policy.POLICY_PATH = Path(tempfile.mkdtemp()) / "absent.json"
    try:
        p2, _, _ = keel_policy.load_policy()
    finally:
        keel_policy.POLICY_PATH = saved
    p1["tools_denied"].append("mutated")
    assert "mutated" not in p2["tools_denied"]
    assert p2["tools_denied"] == config.TOOLS_DENIED


def test_loaded_policy_mutating_list_does_not_affect_failsafe():
    """dict(FAILSAFE) shallow copy used to share nested lists; deep copy
    must isolate them."""
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    with _use(base):
        loaded, ok, _ = keel_policy.load_policy()
    assert ok is True
    loaded["tools_denied"].append("not_a_real_tool")
    missing = Path(tempfile.mkdtemp()) / "absent.json"
    saved = keel_policy.POLICY_PATH
    keel_policy.POLICY_PATH = missing
    try:
        failsafe, _, _ = keel_policy.load_policy()
    finally:
        keel_policy.POLICY_PATH = saved
    assert "not_a_real_tool" not in failsafe["tools_denied"]
    assert keel_policy.FAILSAFE["tools_denied"] == config.TOOLS_DENIED


def test_validator_does_not_mutate_input():
    base = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    snapshot = copy.deepcopy(base)
    keel_policy._validate_structure(base)
    assert base == snapshot
