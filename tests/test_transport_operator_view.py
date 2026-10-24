import hashlib
from test_research_journey import setup


def test_readable_unknown_transport_and_recorded_requirement(setup,tmp_path):
    cfg,j,calls,c,form=setup
    trust=tmp_path/'known_hosts';trust.write_text('Synthetic pinned public trust fixture\n')
    old=cfg.latest('connection','local')
    t=dict(kind='ssh_local_forward',host='192.168.1.203',login='swarmtracker',ssh_port=22,remote_port=11434,local_port=19434,known_hosts_path=str(trust),known_hosts_sha256=hashlib.sha256(trust.read_bytes()).hexdigest(),identity_file='')
    cfg.save('connection','local',dict(old['body'],endpoint='http://127.0.0.1:19434',transport=t))
    page=c.get('/configuration/connections/local/ssh/status',headers={'Accept':'text/html'})
    assert page.status_code==200 and page.headers['Cache-Control']=='no-store'
    assert 'unknown' in page.text and 'Configured model' in page.text
    assert 'No owned tunnel' in page.text and 'not verified availability' in page.text
    api=c.get('/configuration/connections/local/ssh/status').json()
    assert api['state']=='unknown' and 'separate' in api['model_readiness']
    # Fixture dispatch is synthetic: this verifies rendering, not live SSH acceptance.
    result=j.runs.run(agent_id='researcher',task_id='transport-view-fixture',resource_id='research',purpose='generate_text',prompt='Synthetic rendering fixture')
    view=c.get('/workbench/requests/'+result['id'])
    assert view.status_code==200 and 'Recorded transport requirement' in view.text
    assert 'remote loopback runtime port 11434' in view.text
    assert 'does not establish current connectivity or OS containment' in view.text


def test_agents_and_overview_refuse_saved_health_without_owned_transport(setup,tmp_path):
    from swarmtracker_node_ui.agent_observations import configured_agents
    from swarmtracker_node_ui.overview import connection_rows
    cfg,j,calls,c,form=setup
    known=tmp_path/'known_hosts';known.write_text('Synthetic trust fixture\n')
    body=cfg.latest('connection','local')['body']
    t=dict(kind='ssh_local_forward',host='192.168.1.203',login='swarmtracker',ssh_port=22,remote_port=11434,local_port=19434,known_hosts_path=str(known),known_hosts_sha256=hashlib.sha256(known.read_bytes()).hexdigest(),identity_file='')
    cfg.save('connection','local',dict(body,endpoint='http://127.0.0.1:19434',transport=t))
    cfg.observe('connection','local','reachable','Synthetic earlier health')
    view=configured_agents(cfg,j.runs)[0]['setup_observation'];row=connection_rows(cfg)[0]
    assert view['connection_state']==row['state']=='unknown'
    assert view['connection_detail']==row['detail']
    assert view['connection_observed_at']==row['at']
    assert view['last_completed'] is None
