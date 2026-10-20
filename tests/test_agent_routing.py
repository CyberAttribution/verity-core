from contextlib import contextmanager
import hashlib
import threading
import pytest
from swarmtracker_node_ui.customer_config import CustomerConfig,canonical
from swarmtracker_node_ui.local_inference import LocalInference
from verity_core.trust import inference_receipt


@pytest.fixture
def env(tmp_path):
    c=CustomerConfig(tmp_path/'customer-config.db')
    c.save('resource','summary.md',dict(description='Synthetic output',provenance='operator-entered',status='unknown'))
    c.save('connection','local',dict(provider='openclaw',endpoint='http://127.0.0.1:8090',model='synthetic-model',revision='synthetic',scope='local',credential_ref='none'))
    agent=c.save('agent','agent',dict(connection_id='local',instructions='Synthetic instruction',allowed_tools=['generate_text'],resources=['summary.md'],external_sharing='none',fallback_id=''))
    return c,agent,LocalInference(tmp_path/'runs.db',c)


def request(runs,task,**kwargs):
    return runs.run(agent_id='agent',task_id=task,resource_id='summary.md',purpose='generate_text',prompt='Synthetic specimen',**kwargs)


def completed(cfg,*args):
    return dict(model=cfg['model'],response='Synthetic response',event_id='synthetic-event',decision='allowed',done=True,done_reason='stop')


def test_pause_resume_is_versioned_and_old_review_and_receipt_remain_distinct(env):
    c,agent,runs=env;cfg=c.effective('agent','summary.md');old_hash=hashlib.sha256(canonical(cfg).encode()).hexdigest()
    first=request(runs,'before',dispatch=completed);receipt=inference_receipt.export(first)
    paused=c.set_routing('agent',False,agent['digest']);assert paused['version']==2
    seen=[];denied=request(runs,'paused',dispatch=lambda *args:seen.append(1))
    assert denied['state']=='denied' and denied['configuration']['routing_enabled'] is False and not seen
    assert 'paused' in denied['reason'] and runs.recent()[0]['state']=='denied'
    assert inference_receipt.verify(receipt,receipt['checkpoint'])
    resumed=c.set_routing('agent',True,paused['digest']);assert resumed['version']==3
    stale=request(runs,'old-review',dispatch=lambda *args:seen.append(1),expected_configuration_sha256=old_hash)
    assert stale['state']=='denied' and not seen
    current=request(runs,'resumed',dispatch=completed);assert current['state']=='completed'
    assert current['configuration']['agent_version']==3
    assert inference_receipt.export(denied)['record']['entries'][0]['payload']['configuration']['routing_enabled'] is False


def test_routing_change_between_reservation_and_final_guard_blocks_dispatch(env,monkeypatch):
    c,agent,runs=env;original=c.dispatch_guard
    @contextmanager
    def paused_before_guard(*args):
        c.set_routing('agent',False,agent['digest'])
        with original(*args):yield
    monkeypatch.setattr(c,'dispatch_guard',paused_before_guard)
    seen=[];result=request(runs,'guard-race',dispatch=lambda *args:seen.append(1))
    assert result['state']=='failed' and not seen


def test_inflight_dispatch_finishes_before_pause_can_commit(env):
    c,agent,runs=env;started=threading.Event();release=threading.Event();pause_entered=threading.Event();paused=threading.Event();out=[]
    def dispatch(*args):
        started.set();assert release.wait(3);return completed(*args)
    def run():out.append(request(runs,'inflight',dispatch=dispatch))
    def pause():
        pause_entered.set();CustomerConfig(c.path).set_routing('agent',False,agent['digest']);paused.set()
    first=threading.Thread(target=run);first.start();assert started.wait(3)
    second=threading.Thread(target=pause);second.start();assert pause_entered.wait(3)
    assert not paused.wait(.1)
    release.set();first.join(5);second.join(5)
    assert paused.is_set() and out[0]['state']=='completed'
    assert request(runs,'after-pause',dispatch=completed)['state']=='denied'


def test_routing_setting_cannot_be_silently_omitted_or_written_from_stale_form(env):
    c,agent,runs=env;paused=c.set_routing('agent',False,agent['digest'])
    with pytest.raises(ValueError):c.set_routing('agent',True,agent['digest'])
    body=dict(paused['body']);body.pop('routing_enabled')
    with pytest.raises(ValueError):c.save('agent','agent',body)
    with pytest.raises(ValueError):c.set_routing('agent',1,paused['digest'])
    assert c.latest('agent','agent')==paused


def test_agent_page_routing_form_checks_csrf_digest_and_generic_edit_preserves_pause(env,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app,store,decision_routes
    c,agent,runs=env;monkeypatch.setattr(store,'DB_PATH',c.path.with_name('profile.db'))
    client=TestClient(app.app,base_url='http://127.0.0.1')
    form=dict(operator_token=decision_routes.TOKEN,expected_agent_digest=agent['digest'],enabled='false')
    assert client.post('/agents/agent/routing',data=dict(form,operator_token='bad')).status_code==403
    assert client.post('/agents/agent/routing',data=form,follow_redirects=False).status_code==303
    page=client.get('/agents');assert 'Re-enable routed requests' in page.text
    assert client.post('/agents/agent/routing',data=dict(form,enabled='true')).status_code==409
    assert client.post('/agents/agent/routing',data=dict(form,enabled='1')).status_code==422
    edit=dict(operator_token=decision_routes.TOKEN,kind='agent',id='agent',connection_id='local',instructions='Updated instruction',allowed_tools='generate_text',resources='summary.md',external_sharing='none',fallback_id='',daily_request_limit='20',session_request_limit='0')
    assert client.post('/configuration',data=edit,follow_redirects=False).status_code==303
    assert c.latest('agent','agent')['body']['routing_enabled'] is False


def test_pause_blocks_report_proposals_and_invalidates_old_approval(env,tmp_path):
    from swarmtracker_node_ui import report_actions
    c,agent,runs=env;cfg=c.effective('agent','summary.md');proposal='Synthetic summary';sha=hashlib.sha256(proposal.encode()).hexdigest()
    def manifest(configuration):
        return dict(schema=report_actions.SCHEMA,agent_id='agent',task_id='report-routing',model=configuration['model'],provider=configuration['provider'],source_name='synthetic.txt',source_sha256=sha,runtime_event_id='synthetic-event',proposal=proposal,action=dict(kind='write_sanitized_summary',target='summary.md',sha256=sha),policy=report_actions.POLICY,effective_configuration=configuration)
    broker=report_actions.Store(tmp_path/'actions.db',tmp_path/'output',c)
    original=broker.create(manifest(cfg));approved=broker.decide(original['id'],'approved','Synthetic operator review','operator',original['head_hash'])
    paused=c.set_routing('agent',False,agent['digest'])
    with pytest.raises(report_actions.Conflict):broker.execute(original['id'],'operator',approved['head_hash'])
    assert not (tmp_path/'output/summary.md').exists()
    with pytest.raises(ValueError,match='paused'):broker.create(manifest(c.effective('agent','summary.md')))
    resumed=c.set_routing('agent',True,paused['digest'])
    with pytest.raises(report_actions.Conflict):broker.execute(original['id'],'operator',approved['head_hash'])
    fresh=broker.create(manifest(c.effective('agent','summary.md')))
    accepted=broker.decide(fresh['id'],'approved','Fresh review after resume','operator',fresh['head_hash'])
    result=broker.execute(fresh['id'],'operator',accepted['head_hash'])
    assert result['state']=='written' and (tmp_path/'output/summary.md').read_text()==proposal


def test_registered_agent_health_requirement_survives_action_snapshot(env,tmp_path):
    from swarmtracker_node_ui import report_actions
    c,agent,runs=env
    c.save('agent','agent',dict(agent['body'],adapter_kind='openclaw_v0',runtime_version='synthetic',service_identity='synthetic',require_fresh_connection_observation=True))
    cfg=c.effective('agent','summary.md');proposal='Synthetic summary';sha=hashlib.sha256(proposal.encode()).hexdigest()
    manifest=dict(schema=report_actions.SCHEMA,agent_id='agent',task_id='registered-report',model=cfg['model'],provider=cfg['provider'],source_name='synthetic.txt',source_sha256=sha,runtime_event_id='synthetic-event',proposal=proposal,action=dict(kind='write_sanitized_summary',target='summary.md',sha256=sha),policy=report_actions.POLICY,effective_configuration=cfg)
    broker=report_actions.Store(tmp_path/'registered-actions.db',tmp_path/'registered-output',c)
    view=broker.create(manifest)
    assert view['manifest']['effective_configuration']['require_fresh_connection_observation'] is True
    bad=dict(manifest,effective_configuration=dict(cfg,require_fresh_connection_observation='verified'))
    with pytest.raises(ValueError):report_actions.validate(bad)
