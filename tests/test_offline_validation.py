import copy
import math

import pytest

from verity_core.offline_cost import estimate_cost
from verity_core.manual_inventory import validate_interval
from verity_core.link_binding import validate_snapshot
from verity_core.inference_budget import BudgetLedger, BudgetDenied


def test_cost_distinct_prices_exact_rounding_and_unknown_hold(tmp_path):
    assert estimate_cost(1500000, 500000, input_price=2000000, output_price=4000000)["amount"] == 5000000
    assert estimate_cost(1, 1, input_price=1, output_price=1)["amount"] == 1
    assert estimate_cost(0, 0, input_price=1, output_price=1)["amount"] == 0
    ledger = BudgetLedger(tmp_path / "budget.db", clock=lambda: 1792530000)
    policy = ledger.set_policy("agent", daily_microusd=10, session_microusd=10)
    ledger.reserve("request", "agent", session_id="session", estimate_microusd=10, expected_policy_digest=policy["digest"])
    ledger.mark_dispatched("request")
    ledger.mark_unknown("request")
    before = ledger.path.read_bytes()
    estimate = estimate_cost(1, 1, input_price=1, output_price=1)
    assert estimate["kind"] == "estimate" and not estimate["reported_usage"] and not estimate["provider_budget_enforced"]
    assert ledger.path.read_bytes() == before
    reopened = BudgetLedger(ledger.path, clock=ledger.clock)
    with pytest.raises(BudgetDenied):
        reopened.reserve("second", "agent", session_id="session", estimate_microusd=1, expected_policy_digest=policy["digest"])


@pytest.mark.parametrize("value", [True, -1, 1.5, "1", math.nan, 10**15+1])
def test_invalid_usage_not_coerced(value):
    with pytest.raises(ValueError):
        estimate_cost(value, 0, input_price=1, output_price=1)


def inventory():
    return dict(start="2026-10-20T18:00:00-04:00", end="2026-10-21T00:00:00+00:00",
                session_id="session", provider_id="lambda", resource_id="instance",
                actual_rate=None, reference_rate=22.32, gpu_count=8, gpu_type="A100 80GB",
                model_revision="a"*40, provenance="Operator supplied recovery manifest")


def test_inventory_unknown_actual_equal_rates_and_operator_end():
    source = inventory()
    assert validate_interval(**source)["actual_rate"] is None
    source["actual_rate"] = source["reference_rate"]
    assert validate_interval(**source)["actual_rate"] == 22.32
    assert not validate_interval(**source)["termination_confirmed"]
    source["gpu_count"] = 32
    assert validate_interval(**source)["gpu_count"] == 32


@pytest.mark.parametrize("key,value", [("start", "2026-10-20T18:00:00"),
    ("end", "2026-10-20T22:00:00+00:00"), ("actual_rate", True),
    ("actual_rate", math.nan), ("reference_rate", math.inf), ("gpu_count", True),
    ("gpu_count", 33), ("model_revision", "latest"), ("provenance", ""), ("resource_id", "../instance")])
def test_inventory_rejects_ambiguous_or_invalid_input(key, value):
    source = inventory()
    source[key] = value
    with pytest.raises(ValueError):
        validate_interval(**source)


def test_relationship_binds_both_ordered_endpoints_without_mutation():
    current = [dict(kind="machine", id="mac", version=1, digest="a"*64),
               dict(kind="connection", id="local", version=2, digest="b"*64)]
    original = copy.deepcopy(current)
    assert validate_snapshot(current, current) == "declared_current"
    assert validate_snapshot(current, None) == "unbound"
    old = copy.deepcopy(current)
    old[1]["digest"] = None
    assert validate_snapshot(current, old) == "unbound"
    for index in (0, 1):
        for field, value in (("id", "changed"), ("version", 3), ("digest", "c"*64)):
            old = copy.deepcopy(current)
            old[index][field] = value
            assert validate_snapshot(current, old) == "needs_review"
    assert validate_snapshot(current, list(reversed(current))) == "needs_review"
    assert current == original
    with pytest.raises(ValueError):
        validate_snapshot(current, [current[0], current[0]])
    old = copy.deepcopy(current)
    old[0]["version"] = True
    with pytest.raises(ValueError):
        validate_snapshot(current, old)
