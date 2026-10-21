import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from swarmtracker_node_ui import app as ui, customer_routes, decision_routes
from swarmtracker_node_ui.customer_config import CustomerConfig, canonical


@pytest.fixture
def environment(tmp_path, monkeypatch):
    cfg=CustomerConfig(tmp_path/'configuration.db')
    for kind,identifier in [('machine','mac'),('resource','report')]:
        cfg.save(kind,identifier,dict(description='Disposable '+identifier,provenance='operator-entered',status='unknown'))
    cfg.save('connection','local',dict(provider='openclaw',endpoint='http://127.0.0.1:9',model='synthetic',revision='',scope='local',credential_ref='none'))
    monkeypatch.setattr(customer_routes,'database',lambda:cfg)
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    return cfg,client


def endpoint(cfg,kind,identifier):
    entry=cfg.latest(kind,identifier)
    return {key:entry[key] for key in ('kind','id','version','digest')}


def relationship_form(cfg,identifier='hosts',expected=''):
    return dict(operator_token=decision_routes.TOKEN,id=identifier,expected_digest=expected,
                from_endpoint=canonical(endpoint(cfg,'machine','mac')),
                to_endpoint=canonical(endpoint(cfg,'connection','local')),permission='hosts')


def test_relationship_customer_save_change_review_and_history(environment):
    cfg,client=environment
    page=client.get('/environment/relationships/new')
    assert page.status_code==200 and endpoint(cfg,'connection','local')['digest'] in page.text
    form=relationship_form(cfg)
    assert client.post('/environment/relationships',data=form,follow_redirects=False).status_code==303
    first=cfg.latest('link','hosts');saved=json.loads(canonical(first))
    assert 'declared current' in client.get('/environment').text
    conn=cfg.latest('connection','local')
    cfg.save('connection','local',dict(conn['body'],model='changed'))
    assert 'needs review' in client.get('/environment').text
    assert cfg.history('link','hosts')[0]==saved
    # Endpoint edits and link edits are independently guarded.
    assert client.post('/environment/relationships',data=form).status_code==409
    review=relationship_form(cfg,expected=first['digest'])
    assert client.post('/environment/relationships',data=review,follow_redirects=False).status_code==303
    assert client.post('/environment/relationships',data=review).status_code==409
    page=client.get('/environment/relationships/hosts').text
    assert 'Version 1' in page and 'Version 2' in page and 'needs review' in page
    assert first['body']['endpoint_binding'][1]['digest'] in page
    assert cfg.history('link','hosts')[1]==saved


def test_stale_endpoint_selection_refused_before_new_relationship(environment):
    cfg,client=environment;form=relationship_form(cfg)
    machine=cfg.latest('machine','mac')
    cfg.save('machine','mac',dict(machine['body'],description='New machine declaration'))
    assert client.post('/environment/relationships',data=form).status_code==409
    assert cfg.latest('link','hosts') is None
    fresh=relationship_form(cfg)
    fresh['operator_token']='wrong'
    assert client.post('/environment/relationships',data=fresh).status_code==403
    reversed_form=relationship_form(cfg)
    reversed_form['from_endpoint'],reversed_form['to_endpoint']=reversed_form['to_endpoint'],reversed_form['from_endpoint']
    assert client.post('/environment/relationships',data=reversed_form).status_code==409
    assert cfg.latest('link','hosts') is None


def test_legacy_relationship_unbound_without_rewriting_history(environment):
    cfg,client=environment
    client.post('/environment/relationships',data=relationship_form(cfg))
    # Retained historical schema predating endpoint snapshots.
    with cfg._db() as db:
        entry=cfg.latest('link','hosts');body=dict(entry['body']);body.pop('endpoint_binding')
        import hashlib
        raw=canonical(body)
        db.execute("UPDATE versions SET body=?,digest=? WHERE kind='link'",(raw,hashlib.sha256(raw.encode()).hexdigest()))
    before=cfg.latest('link','hosts')
    assert 'unbound' in client.get('/environment').text
    assert cfg.latest('link','hosts')==before
    form=relationship_form(cfg,expected=before['digest'])
    assert client.post('/environment/relationships',data=form,follow_redirects=False).status_code==303
    assert cfg.history('link','hosts')[1]==before


@pytest.fixture
def session_workflow(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from swarmtracker_node_ui import sessions
    database=sessions.SessionStore(tmp_path/'sessions.db')
    monkeypatch.setattr(sessions,'SessionStore',lambda:database)
    app=FastAPI();sessions.install(app,ui.render,ui.base_ctx)
    return database,TestClient(app,base_url='http://127.0.0.1')


def inventory_form(expected=''):
    return dict(operator_token=decision_routes.TOKEN,expected_inventory_id=expected,
        provider_id='manual-provider',resource_id='disposable-resource',region='unknown',gpu_count='0',gpu_type='Mac CPU',
        model_name='Installed local test model',model_revision='a'*40,start='2026-10-21T00:00:00-04:00',end='',
        actual_rate='',reference_rate='0.00',rate_provenance='Synthetic supplied rate checked2026-10-21',
        provenance='Operator disposable test2026-10-21',uncertainty='Provider end and usage unverified')


def test_manual_inventory_customer_create_correct_export_without_billing_authority(session_workflow):
    from swarmtracker_node_ui.sessions import derive, SessionStore
    db,client=session_workflow
    assert client.get('/sessions/new').status_code==200
    assert client.post('/sessions',data=dict(operator_token=decision_routes.TOKEN,id='manual-session',purpose='Disposable local acceptance',provider='manual'),follow_redirects=False).status_code==303
    assert client.post('/sessions/manual-session/inventory',data=inventory_form(),follow_redirects=False).status_code==303
    first=db.get('manual-session')['events'][0]
    assert first['data']['actual_rate'] is None and first['data']['reference_rate']==0
    form=inventory_form(first['id']);form.update(end='2026-10-21T01:00:00-04:00',actual_rate='0.10',uncertainty='Operator reports end; provider receipt missing')
    assert client.post('/sessions/manual-session/inventory',data=form,follow_redirects=False).status_code==303
    assert client.post('/sessions/manual-session/inventory',data=form).status_code==409
    record=type(db)(db.path).get('manual-session')
    assert record['events'][0]==first and len(record['events'])==2
    derived=derive(record)
    assert derived['provider']=='unknown' and derived['reported_charge'] is None and derived['estimated_charge'] is None
    assert derived['model']=='unknown' # Manual inventory is not a fresh health probe.
    export=client.get('/sessions/manual-session/export.json').json()
    assert export['events'][0]['data']['actual_rate'] is None
    assert export['events'][1]['data']['termination_confirmed'] is False
    page=client.get('/sessions/manual-session/inventory').text
    assert first['id'] in page and 'provider receipt missing' in page and 'not confirm provider termination' in page
    with db.connection() as con:
        assert {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}=={'sessions','session_events'}


def test_invalid_inventory_and_csrf_preserve_history(session_workflow):
    db,client=session_workflow
    db.create(dict(id='manual-session',purpose='Test',provider='manual',macs=[],branch='test',commits=[]))
    for key,value in [('start','2026-10-21T00:00:00'),('end','2026-10-20T00:00:00Z'),('actual_rate','NaN'),('gpu_count','33'),('model_revision','latest')]:
        form=inventory_form();form[key]=value
        assert client.post('/sessions/manual-session/inventory',data=form).status_code==409
    form=inventory_form();form['operator_token']='wrong'
    assert client.post('/sessions/manual-session/inventory',data=form).status_code==403
    assert db.get('manual-session')['events']==[]
    valid=inventory_form();valid['model_revision']='sha256:'+'b'*64
    assert client.post('/sessions/manual-session/inventory',data=valid,follow_redirects=False).status_code==303
    assert db.get('manual-session')['events'][0]['data']['model_revision']==valid['model_revision']


def test_spending_calculator_retains_provenance_and_unknown_reservation(environment,tmp_path,monkeypatch):
    from verity_core.inference_budget import BudgetLedger, BudgetDenied
    cfg,client=environment
    cfg.save('agent','agent',dict(connection_id='local',instructions='Synthetic',allowed_tools=['generate_text'],resources=['report'],external_sharing='none',fallback_id=''))
    ledger=BudgetLedger(tmp_path/'budget.db');monkeypatch.setattr(customer_routes,'budget_database',lambda:ledger)
    policy=ledger.set_policy('agent',daily_microusd=10,session_microusd=10)
    ledger.reserve('uncertain','agent',session_id='test',estimate_microusd=10,expected_policy_digest=policy['digest'])
    ledger.mark_dispatched('uncertain');ledger.mark_unknown('uncertain')
    with ledger.db() as con:before=[tuple(row) for row in con.execute('SELECT * FROM inference_budget_entries')]
    form=dict(operator_token=decision_routes.TOKEN,input_tokens='1500000',output_tokens='500000',input_rate_usd='2',output_rate_usd='4',pricing_source='Synthetic supplied prices',pricing_as_of='2026-10-21',basis='Planned input/output counts; not actual usage')
    assert client.post('/configuration/spending/agent/estimate',data=form,follow_redirects=False).status_code==303
    reopened=BudgetLedger(ledger.path)
    assert reopened.estimates('agent')[0]['body']['amount']==5000000
    page=client.get('/configuration/spending').text
    assert 'Estimate · 5.000000 USD' in page and 'Synthetic supplied prices' in page and '2026-10-21' in page
    assert 'not provider-reported usage' in page and 'No live provider-budget enforcement' in page
    with ledger.db() as con:assert [tuple(row) for row in con.execute('SELECT * FROM inference_budget_entries')]==before
    with pytest.raises(BudgetDenied):reopened.release_undispatched('uncertain',reason='Cannot cancel a possibly billed request')
    for key,value in [('input_tokens','1.5'),('output_tokens','-1'),('input_rate_usd','unlimited'),('pricing_source','')]:
        bad=dict(form);bad[key]=value
        assert client.post('/configuration/spending/agent/estimate',data=bad).status_code==422
    assert len(reopened.estimates('agent'))==1
