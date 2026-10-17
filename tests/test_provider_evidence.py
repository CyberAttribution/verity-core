"""Provider health must be supported by observations in the whole window."""
import pytest
from swarmtracker_node_ui import governance


@pytest.mark.parametrize("events,status,flag", [
    ([], "yellow", "unproven"),
    ([{}], "yellow", "unproven"),
    ([{"provider": "ollama_local"}, {}], "yellow", "incomplete"),
    ([{"provider": "ollama_local"}], "green", None),
    ([{"provider": "external"}], "yellow", "outside"),
    ([{"provider": "ollama_local"}, {"provider": "external"}], "yellow", "outside"),
])
def test_provider_health_requires_complete_local_evidence(monkeypatch, events, status, flag):
    monkeypatch.setattr(governance.ledger, "read_raw_events", lambda window: events)
    result = governance.assess()
    categories = result.get("categories", result.get("controls", []))
    category = next(c for c in categories if c["key"] == "provider_model")
    assert category["status"] == status
    if flag:
        assert any(flag in message for level, message in category["flags"])
    else:
        assert category["flags"] == []
