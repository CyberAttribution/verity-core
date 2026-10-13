"""Tests for verity_core.json_safety.bounded_json_loads."""

import json

import pytest

from verity_core.json_safety import (
    MAX_SUPPORTED_DEPTH,
    bounded_json_loads,
)


def _nested(depth, inner="1"):
    return "[" * depth + inner + "]" * depth


def test_plain_and_mixed_containers_parse():
    doc = '{"a": [1, {"b": [[2]]}], "c": {"d": []}}'
    assert bounded_json_loads(doc) == json.loads(doc)


def test_braces_and_escaped_quotes_inside_strings_do_not_count():
    value = {"s": "[{" * 1500 + chr(34) + chr(92) + "}}]]",
             "t": "a" + chr(92) * 2 + chr(34) + "b"}
    doc = json.dumps(value)
    assert bounded_json_loads(doc, max_depth=1) == value


def test_escaped_backslash_before_quote_handled():
    value = {"k": "x" + chr(92), "j": chr(34)}
    doc = json.dumps(value)
    assert bounded_json_loads(doc, max_depth=1) == value


def test_depth_at_boundary_allowed():
    assert bounded_json_loads(_nested(128), max_depth=128) == json.loads(_nested(128))
    assert bounded_json_loads(_nested(MAX_SUPPORTED_DEPTH)) is not None


def test_depth_boundary_plus_one_rejected_before_loads():
    with pytest.raises(ValueError, match="nesting exceeds"):
        bounded_json_loads(_nested(129))
    with pytest.raises(ValueError, match="nesting exceeds"):
        bounded_json_loads(_nested(2000))


def test_bytes_input_utf8_strict():
    assert bounded_json_loads(b'{"a": "\xc3\xa9"}') == {"a": "é"}
    with pytest.raises(UnicodeDecodeError):
        bounded_json_loads(b'{"a": "\xff"}')


def test_invalid_max_depth_rejected():
    for bad in (0, -1, True, 1.5, "8", 129, 1000):
        with pytest.raises((TypeError, ValueError)):
            bounded_json_loads("[]", max_depth=bad)


def test_hooks_forwarded():
    def pairs(p):
        raise ValueError("dup")
    def const(name):
        raise ValueError("const")
    with pytest.raises(ValueError, match="dup"):
        bounded_json_loads('{"a":1,"a":2}', object_pairs_hook=pairs)
    with pytest.raises(ValueError, match="const"):
        bounded_json_loads("NaN", parse_constant=const)


def test_ledger_tail_depth1500_blocked(tmp_path, monkeypatch):
    """Mandatory append rejects excessive nesting without changing bytes."""
    import pathlib
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[2] / "apps/node"))
    from swarmtracker_node_ui import keel_events
    target = tmp_path / "ledger.jsonl"
    before = b"{}" + bytes([10]) + _nested(1500).encode() + bytes([10])
    target.write_bytes(before)
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)
    with pytest.raises(keel_events.LedgerWriteError, match="UNREADABLE"):
        keel_events._locked_append("{}" + chr(10), require_dir_sync=True)
    assert target.read_bytes() == before


def test_cli_media_verify_depth2000_error():
    """Regression: trust media CLI verify of a depth-2000 bundle must
    return an error containing 'nesting exceeds'."""
    from verity_core.trust import media_bundle
    with pytest.raises(ValueError, match="nesting exceeds"):
        media_bundle.strict_loads(_nested(2000))
