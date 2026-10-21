import json
import pytest
from swarmtracker_node_ui.agent_observations import configured_agents
from swarmtracker_node_ui.customer_config import CustomerConfig
from swarmtracker_node_ui.local_inference import LocalInference


@pytest.fixture
def env(tmp_path):
    cfg=CustomerConfig(tmp_path/'customer-config.db')
    cfg.save('resource','summary.md',dict(description='Synthetic output',provenance='operator-entered',status='unknown'))
    cfg.save('connection','local',dict(provider='openclaw',endpoint='http://127.0.0.1:8090',model='synthetic-model',revision='synthetic',scope='local',credential_ref='none'))
    for name in ['a','b']:
        cfg.save('agent',name,dict(connection_id='local',instructions='PRIVATE_INSTRUCTION',allowed_tools=['generate_text'],resources=['summary.md'],external_sharing='none',fallback_id='',daily_request_limit=0))
    return cfg,LocalInference(tmp_path/'local-inference.db',cfg)


def submit(runs,name,task):
    return runs.run(agent_id=name,task_id=task,resource_id='summary.md',purpose='generate_text',prompt='PRIVATE_SPECIMEN',
                    dispatch=lambda cfg,*args:dict(model=cfg['model'],response='PRIVATE_RESPONSE',event_id='synthetic-event',decision='allowed',done=True,done_reason='stop'))


def setup(env,name='a'):
    return next(v['setup_observation'] for v in configured_agents(*env) if v['id']==name)


def test_per_agent_evidence_is_not_hidden_by_global_recent_limit(env):
    cfg,runs=env;first=submit(runs,'a','a-first')
    for i in range(31):submit(runs,'b','b-'+str(i))
    assert all(r['agent_id']=='b' for r in runs.recent())
    view=setup(env)
    assert view['last_completed']['id']==first['id']
    assert view['last_completed']['configuration_matches']
    assert view['last_completed']['runtime_event_id']=='synthetic-event'
    assert 'PRIVATE_RESPONSE' not in json.dumps(view)
    assert 'PRIVATE_INSTRUCTION' not in json.dumps(view)
    assert 'PRIVATE_SPECIMEN' not in json.dumps(view)


def test_contact_does_not_claim_completion_and_connection_edit_invalidates_history(env):
    cfg,runs=env
    assert setup(env)['connection_state']=='unknown'
    assert setup(env)['last_completed'] is None
    cfg.observe('connection','local','reachable','Synthetic contact')
    assert setup(env)['connection_state']=='reachable' and setup(env)['last_completed'] is None
    submit(runs,'a','old')
    previous=cfg.latest('connection','local')
    cfg.save('connection','local',dict(previous['body'],model='new-model'))
    view=setup(env)
    assert view['connection_state']=='unknown'
    assert not view['last_completed']['configuration_matches']
    assert view['last_completed']['model']=='synthetic-model'


def test_paused_denied_attempt_retains_separate_historical_completion(env):
    cfg,runs=env;first=submit(runs,'a','before')
    agent=cfg.latest('agent','a');cfg.set_routing('a',False,agent['digest'])
    denied=submit(runs,'a','after')
    view=setup(env)
    assert denied['state']=='denied' and view['latest_attempt']['id']==denied['id']
    assert view['last_completed']['id']==first['id']
    assert not view['last_completed']['configuration_matches']
    cfg.disable_connection('local')
    assert setup(env)['connection_state']=='disabled'


def test_custom_registration_and_stale_contact_are_not_supported_readiness(env,monkeypatch):
    cfg,runs=env;agent=cfg.latest('agent','a')
    cfg.save('agent','a',dict(agent['body'],adapter_kind='custom_unverified'))
    cfg.observe('connection','local','reachable','Synthetic contact')
    assert not setup(env)['adapter_supported']
    from swarmtracker_node_ui import customer_config
    original=customer_config.time.time
    current=original()
    monkeypatch.setattr(customer_config.time,'time',lambda:current+301)
    assert setup(env)['connection_state']=='stale'


def test_agents_page_renders_metadata_without_private_request_contents(env,monkeypatch):
    cfg,runs=env;submit(runs,'a','page-request')
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app,store
    monkeypatch.setattr(store,'DB_PATH',cfg.path.with_name('profile.db'))
    page=TestClient(app.app,base_url='http://127.0.0.1').get('/agents')
    assert page.status_code==200
    assert 'Last completed routed request: page-request' in page.text
    assert 'matches the currently recorded configuration' in page.text
    assert 'No completed routed request is recorded for this agent' in page.text
    # Existing configuration instructions may be present in other views; the new
    # observation section itself exposes metadata only, never response/specimen.
    assert 'PRIVATE_RESPONSE' not in page.text and 'PRIVATE_SPECIMEN' not in page.text
