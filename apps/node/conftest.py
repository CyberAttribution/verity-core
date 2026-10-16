"""Keep product activity from test inference out of development data."""
import pytest
@pytest.fixture(autouse=True)
def isolated_activity_store(monkeypatch,tmp_path):
    from swarmtracker_node_ui import monitoring
    monkeypatch.setattr(monitoring,"STORE",monitoring.MonitorStore(tmp_path/"activity.db"))
