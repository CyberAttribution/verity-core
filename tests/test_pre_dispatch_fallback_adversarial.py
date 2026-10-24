import hashlib
import pytest
from test_research_journey import setup
from test_pre_dispatch_fallback import enable
from swarmtracker_node_ui.customer_config import canonical


def _hash(selected):
    return hashlib.sha256(canonical(selected).encode()).hexdigest()


def _capturing(seen):
    def dispatch(cfg, prompt, purpose):
        seen.append(dict(connection_id=cfg["connection_id"], reason=cfg.get("selection_reason"), model=cfg["model"]))
        return dict(model=cfg["model"], decision="allowed", done=True, response="fine output", event_id="ev-1")
    return dispatch


def _failing(seen, error):
    def dispatch(cfg, prompt, purpose):
        seen.append(cfg["connection_id"])
        raise error
    return dispatch


def test_policy_flag_revoked_after_reviewed_snapshot_blocks_fallback_dispatch(setup):
    cfg, j, calls, c, form = setup
    enable(cfg)
    selected = cfg.resolve_pre_dispatch("researcher", "research", "generate_text")
    expected = _hash(selected)
    assert selected["connection_id"] == "fallback"
    agent = cfg.latest("agent", "researcher")
    body = dict(agent["body"])
    body.pop("pre_dispatch_fallback_enabled")
    cfg.save("agent", "researcher", body, expected_digest=agent["digest"])
    run = j.runs.run(agent_id="researcher", task_id="revoked-flag", resource_id="research",
                     purpose="generate_text", prompt="Flag revoked",
                     expected_configuration_sha256=expected)
    assert run["state"] == "denied" and calls == []
    assert "changed since request review" in run["reason"]
    after = cfg.resolve_pre_dispatch("researcher", "research", "generate_text")
    assert after["connection_id"] == "local"
    assert _hash(after) != expected


def test_fallback_connection_version_changed_after_review_refuses(setup):
    cfg, j, calls, c, form = setup
    enable(cfg)
    selected = cfg.resolve_pre_dispatch("researcher", "research", "generate_text")
    expected = _hash(selected)
    old = cfg.latest("connection", "fallback")
    cfg.save("connection", "fallback", dict(old["body"], revision="fallback-revision-2"))
    cfg.observe("connection", "fallback", "reachable", "Re-observed after version change")
    assert cfg.resolve_pre_dispatch("researcher", "research", "generate_text")["connection_hash"] != selected["connection_hash"]
    run = j.runs.run(agent_id="researcher", task_id="fallback-changed", resource_id="research",
                     purpose="generate_text", prompt="Stale fallback review",
                     expected_configuration_sha256=expected)
    assert run["state"] == "denied" and calls == []
    assert "changed since request review" in run["reason"]


def test_dispatched_primary_timeout_never_selects_fallback(setup):
    cfg, j, calls, c, form = setup
    enable(cfg)
    cfg.observe("connection", "local", "reachable", "Primary healthy again")
    selected = cfg.resolve_pre_dispatch("researcher", "research", "generate_text")
    assert selected["connection_id"] == "local"
    expected = _hash(selected)
    seen = []
    run = j.runs.run(agent_id="researcher", task_id="primary-timeout", resource_id="research",
                     purpose="generate_text", prompt="Unknown cancellation",
                     expected_configuration_sha256=expected,
                     dispatch=_failing(seen, TimeoutError("client deadline; server state unknown")))
    assert run["state"] == "failed" and seen == ["local"] and calls == []
    assert j.runs.recent()[0]["state"] == "failed"
    assert j.runs.latest_for_agent("researcher", state="reserved") is None


def test_explicit_additional_selection_keeps_operator_explicit(setup):
    cfg, j, calls, c, form = setup
    enable(cfg)
    agent = cfg.latest("agent", "researcher")
    cfg.save("agent", "researcher", dict(agent["body"], permitted_connection_ids=["fallback"]),
             expected_digest=agent["digest"])
    selected = cfg.effective("researcher", "research", "fallback")
    assert selected["selection_reason"] == "operator_explicit"
    expected = _hash(selected)
    seen = []
    run = j.runs.run(agent_id="researcher", task_id="explicit-additional", resource_id="research",
                     purpose="generate_text", prompt="Operator chose additional",
                     requested_connection_id="fallback",
                     expected_configuration_sha256=expected, dispatch=_capturing(seen))
    assert run["state"] == "completed" and calls == []
    assert seen[0]["connection_id"] == "fallback" and seen[0]["reason"] == "operator_explicit"
    assert run["configuration"]["selection_reason"] == "operator_explicit"
    assert run["configuration"]["primary_connection_id"] == "local"
