"""Real optional Prefect SDK validation; no flow execution or database opens."""
import pytest


def test_optional_private_tool_plan_accepts_none_and_path(monkeypatch):
    pytest.importorskip('prefect')
    monkeypatch.setenv('PREFECT_API_URL','http://127.0.0.1:4200/api')
    from tools.prefect_session import make_flow
    f=make_flow()
    for plan in (None,'/tmp/operator-plan-not-opened.json'):
        params=f.validate_parameters(dict(db_path='/tmp/queue-not-opened.db',tool_plan=plan,max_active=1))
        assert params['tool_plan']==plan and params['max_active']==1
