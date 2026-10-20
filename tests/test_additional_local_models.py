"""Explicit local selection must preserve the request, revocation and receipt boundary."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from swarmtracker_node_ui.customer_config import CustomerConfig, canonical
from swarmtracker_node_ui.local_inference import LocalInference
from verity_core.trust import inference_receipt


@pytest.fixture
def env(tmp_path):
    c=CustomerConfig(tmp_path/'config.db')
    c.save('resource','report',dict(description='Synthetic report',provenance='operator-entered',status='unknown'))
    for name,port in [('primary',8090),('alternate',8091),('ungranted',8092)]:
        c.save('connection',name,dict(provider='openclaw',endpoint=f'http://127.0.0.1:{port}',model='same-model',revision=name,scope='local',credential_ref='none'))
        c.observe('connection',name,'reachable','Synthetic fixture health')
    c.save('agent','agent',dict(connection_id='primary',instructions='Summarize.',allowed_tools=['generate_text'],resources=['report'],external_sharing='none',fallback_id='',adapter_kind='openclaw_v0',require_fresh_connection_observation=True,permitted_connection_ids=['alternate']))
    return c,LocalInference(tmp_path/'runs.db',c)


def request(ledger,task='task',**kw):
    return ledger.run(agent_id='agent',resource_id='report',purpose='generate_text',prompt='Synthetic specimen',task_id=task,**kw)


def response(c,*_):
    return dict(model=c['model'],response='Synthetic fixture response',event_id='fixture-event',decision='allowed',done=True,done_reason='stop')


def test_same_model_name_never_grants_another_connection_and_primary_stays_default(env):
    c,r=env;seen=[]
    def dispatch(cfg,*args):seen.append(cfg['connection_id']);return response(cfg)
    first=request(r,'primary',dispatch=dispatch)
    alternate=request(r,'alternate',requested_connection_id='alternate',dispatch=dispatch)
    denied=request(r,'forged',requested_connection_id='ungranted',dispatch=dispatch)
    assert seen==['primary','alternate']
    assert first['configuration']['connection_id']=='primary'
    assert alternate['configuration']['primary_connection_id']=='primary'
    assert alternate['configuration']['selection_reason']=='operator_explicit'
    assert denied['state']=='denied' and denied['requested_connection_id']=='ungranted'
    bundle=r.request_export(alternate['id']);cfg=bundle['record']['entries'][0]['payload']['configuration']
    assert cfg['selected_connection_id']=='alternate' and cfg['connection_hash']==c.latest('connection','alternate')['digest']
    assert inference_receipt.verify(bundle,bundle['checkpoint'])
    reopened=LocalInference(r.path,CustomerConfig(c.path));assert reopened.request_export(alternate['id'])==bundle


@pytest.mark.parametrize('bad',[['primary'],['alternate','alternate'],['missing'],'alternate',[False],['ungranted']*9])
def test_invalid_permissions_cannot_replace_existing_version(env,bad):
    c,r=env;prior=c.latest('agent','agent')
    with pytest.raises(ValueError):c.set_permitted_connections('agent',bad,prior['digest'])
    assert c.latest('agent','agent')==prior


def test_external_connection_is_not_an_additional_local_permission(env):
    c,r=env;c.save('connection','cloud',dict(provider='openai',endpoint='https://api.openai.com',model='unverified',revision='',scope='external',credential_ref='none'))
    with pytest.raises(ValueError):c.set_permitted_connections('agent',['cloud'],c.latest('agent','agent')['digest'])
    # Reclassifying a previously permitted ID cannot authorize cloud egress.
    c.save('connection','alternate',dict(provider='openai',endpoint='https://api.openai.com',model='unverified',revision='',scope='external',credential_ref='none'))
    seen=[];run=request(r,requested_connection_id='alternate',dispatch=lambda *_:seen.append(True))
    assert run['state']=='denied' and not seen


@pytest.mark.parametrize('condition',['missing','stale','disabled','unknown'])
def test_selected_connection_health_is_checked_not_primary_health(env,condition):
    c,r=env
    if condition=='disabled':c.disable_connection('alternate')
    elif condition=='missing':
        with c._db() as db:db.execute("DELETE FROM observations WHERE id='alternate'")
    elif condition=='stale':
        with c._db() as db:db.execute("UPDATE observations SET at=at-301 WHERE id='alternate'")
    else:c.observe('connection','alternate','unknown','Synthetic unknown')
    seen=[];run=request(r,requested_connection_id='alternate',dispatch=lambda *_:seen.append(True))
    assert run['state']=='denied' and not seen
    assert c.observations()[('connection','primary')]['state']=='reachable'


def test_selected_connection_requires_fresh_health_for_legacy_agent(env):
    c,r=env;body=c.latest('agent','agent')['body'];body.pop('require_fresh_connection_observation');c.save('agent','agent',body)
    with c._db() as db:db.execute('DELETE FROM observations')
    assert request(r,'legacy-primary',dispatch=response)['state']=='completed'
    assert request(r,'legacy-alternate',requested_connection_id='alternate',dispatch=response)['state']=='denied'


def test_revocation_and_compare_save_preserve_history_and_shared_caps(env):
    c,r=env;old=c.latest('agent','agent');c.save('agent','agent',dict(old['body'],daily_request_limit=1))
    assert request(r,'one',requested_connection_id='alternate',dispatch=response)['state']=='completed'
    assert request(r,'two',dispatch=response)['state']=='denied'
    with pytest.raises(ValueError):c.set_permitted_connections('agent',[],old['digest'])
    current=c.latest('agent','agent');c.set_permitted_connections('agent',[],current['digest'])
    assert request(r,'three',requested_connection_id='alternate',dispatch=response)['state']=='denied'
    historical=r.recent()[-1]
    assert historical['configuration']['connection_id']=='alternate' and historical['configuration']['agent_version']==2


def test_selected_change_at_final_guard_never_dispatches(env,monkeypatch):
    c,r=env;guard=c.dispatch_guard;seen=[]
    @contextmanager
    def race(agent,resource,expected):
        old=c.latest('connection','alternate')['body'];c.save('connection','alternate',dict(old,endpoint='http://127.0.0.1:8099'))
        c.observe('connection','alternate','reachable','Fresh replacement health')
        with guard(agent,resource,expected):yield
    monkeypatch.setattr(c,'dispatch_guard',race)
    run=request(r,requested_connection_id='alternate',dispatch=lambda *_:seen.append(True))
    assert run['state']=='failed' and not seen


def test_alternate_wrong_runtime_model_and_crash_cannot_fallback_or_replay(env):
    c,r=env;seen=[]
    def wrong(cfg,*_):seen.append(cfg['connection_id']);return dict(response(cfg),model='wrong-model')
    assert request(r,'mismatch',requested_connection_id='alternate',dispatch=wrong)['state']=='failed'
    assert seen==['alternate']
    def crash(*_):raise SystemExit('Synthetic interruption')
    with pytest.raises(SystemExit):request(r,'crash',requested_connection_id='alternate',dispatch=crash)
    with pytest.raises(sqlite3.IntegrityError):request(LocalInference(r.path,c),'crash',requested_connection_id='alternate',dispatch=response)
    assert r.recent()[0]['state']=='reserved'


def test_permission_writer_waits_for_already_dispatched_alternate(env):
    c,r=env;entered=threading.Event();release=threading.Event();changed=threading.Event();errors=[]
    prior=c.latest('agent','agent')
    def dispatch(cfg,*_):entered.set();assert release.wait(5);return response(cfg)
    def run():
        try:request(r,requested_connection_id='alternate',dispatch=dispatch)
        except BaseException as exc:errors.append(exc)
    def revoke():
        try:CustomerConfig(c.path).set_permitted_connections('agent',[],prior['digest']);changed.set()
        except BaseException as exc:errors.append(exc)
    t=threading.Thread(target=run);t.start();assert entered.wait(5)
    writer=threading.Thread(target=revoke);writer.start();assert not changed.wait(.1)
    release.set();t.join(5);writer.join(5)
    assert not errors and changed.is_set()
    assert request(r,'after',requested_connection_id='alternate',dispatch=response)['state']=='denied'


def test_workbench_selected_hash_and_permission_route_are_bound(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui,store,customer_routes,decision_routes,local_inference
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    c=customer_routes.database();c.save('resource','report',dict(description='Synthetic report',provenance='operator-entered',status='unknown'))
    for name in ['primary','alternate']:
        c.save('connection',name,dict(provider='openclaw',endpoint='http://127.0.0.1:8090',model=name,revision='fixture',scope='local',credential_ref='none'));c.observe('connection',name,'reachable','Fixture')
    c.save('agent','agent',dict(connection_id='primary',instructions='Summarize',allowed_tools=['generate_text'],resources=['report'],external_sharing='none',fallback_id=''))
    client=TestClient(ui.app,base_url='http://127.0.0.1');calls=[]
    monkeypatch.setattr(local_inference.LocalInference,'_dispatch',staticmethod(lambda cfg,*args:(calls.append(cfg['connection_id']) or response(cfg))))
    grant=dict(operator_token=decision_routes.TOKEN,permitted_connection_ids='alternate',expected_agent_digest=c.latest('agent','agent')['digest'])
    assert client.post('/configuration/agents/agent/models',data=grant,headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.post('/configuration/agents/agent/models',data=grant,follow_redirects=False).status_code==303
    assert client.post('/configuration/agents/agent/models',data=grant,follow_redirects=False).status_code==422
    html=client.get('/workbench').text;assert 'Additional model alternate' in html
    primary=c.effective('agent','report');alternate=c.effective('agent','report','alternate')
    form=dict(operator_token=decision_routes.TOKEN,agent_id='agent',resource_id='report',task_id='forged-hash',session_id='',purpose='generate_text',prompt='Synthetic',requested_connection_id='alternate',expected_configuration_sha256=hashlib.sha256(canonical(primary).encode()).hexdigest())
    denied=client.post('/workbench/requests',data=form,follow_redirects=False)
    assert denied.status_code==303 and not calls
    form.update(task_id='selected-request',expected_configuration_sha256=hashlib.sha256(canonical(alternate).encode()).hexdigest())
    success=client.post('/workbench/requests',data=form,follow_redirects=False)
    assert success.status_code==303 and calls==['alternate']
    bundle=client.get(success.headers['location']+'/export').json();assert inference_receipt.verify(bundle,bundle['checkpoint'])
