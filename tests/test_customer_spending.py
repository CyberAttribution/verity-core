import threading
import pytest
from verity_core.inference_budget import BudgetLedger,BudgetDenied
from swarmtracker_node_ui.customer_routes import parse_usd,format_usd


@pytest.mark.parametrize('text,amount',[('0',0),('0.000001',1),('25.50',25500000),('1000000',10**12),('unlimited',None)])
def test_usd_conversion_uses_exact_integer_units(text,amount):
    assert parse_usd(text)==amount
    assert parse_usd(format_usd(amount))==amount


@pytest.mark.parametrize('text',['',' ','-1','+1','NaN','1e3','0.0000001','1000000.000001','Unlimited',True,None])
def test_ambiguous_or_invalid_amounts_are_not_unlimited(text):
    with pytest.raises(ValueError):parse_usd(text)


def test_policy_compare_and_save_is_serialized(tmp_path):
    path=tmp_path/'budget.db';l=BudgetLedger(path);p=l.set_policy('agent',daily_microusd=100,session_microusd=100,expected_policy_digest='')
    barrier=threading.Barrier(2);out=[]
    def update(value):
        other=BudgetLedger(path);barrier.wait()
        try:other.set_policy('agent',daily_microusd=value,session_microusd=value,expected_policy_digest=p['digest']);out.append('saved')
        except BudgetDenied:out.append('stale')
    ts=[threading.Thread(target=update,args=(v,)) for v in (200,300)]
    for t in ts:t.start()
    for t in ts:t.join(5)
    assert sorted(out)==['saved','stale'] and l.policy('agent')['version']==2
    with pytest.raises(BudgetDenied):l.set_policy('agent',daily_microusd=1,session_microusd=1,expected_policy_digest='')


def test_customer_spending_ui_denies_stale_edits_and_preserves_explicit_limits(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app,store,decision_routes,customer_routes
    from swarmtracker_node_ui.customer_config import CustomerConfig
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    config=CustomerConfig(tmp_path/'customer-config.db')
    config.save('resource','report',dict(description='Synthetic report',provenance='operator-entered',status='unknown'))
    config.save('connection','local',dict(provider='openclaw',endpoint='http://127.0.0.1:8090',model='synthetic',revision='',scope='local',credential_ref='none'))
    config.save('agent','agent',dict(connection_id='local',instructions='Synthetic',allowed_tools=['generate_text'],resources=['report'],external_sharing='none',fallback_id=''))
    client=TestClient(app.app,base_url='http://127.0.0.1')
    page=client.get('/configuration/spending');assert 'paid provider routing is not enabled' in page.text
    assert page.headers['cache-control']=='no-store' and '0.000000' in page.text
    form=dict(operator_token=decision_routes.TOKEN,daily_usd='0',session_usd='unlimited',expected_policy_digest='')
    assert client.post('/configuration/spending/agent',data=dict(form,operator_token='bad')).status_code==403
    assert client.post('/configuration/spending/agent',data=form,follow_redirects=False).status_code==303
    policy=customer_routes.budget_database().policy('agent')
    assert policy['daily_microusd']==0 and policy['session_microusd'] is None
    assert client.post('/configuration/spending/agent',data=form).status_code==409
    assert client.post('/configuration/spending/agent',data=dict(form,daily_usd='',expected_policy_digest=policy['digest'])).status_code==422
    assert customer_routes.budget_database().policy('agent')==policy
    assert client.post('/configuration/spending/missing',data=form).status_code==404
