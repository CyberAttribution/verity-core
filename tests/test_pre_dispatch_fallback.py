import hashlib,time
import pytest
from test_research_journey import setup
from test_framework_report import report
from swarmtracker_node_ui.customer_config import canonical


def enable(cfg):
    cfg.save('connection','fallback',dict(cfg.latest('connection','local')['body'],revision='fallback-revision'))
    cfg.observe('connection','local','unavailable','Synthetic primary unavailable')
    cfg.observe('connection','fallback','reachable','Synthetic fallback reachable')
    agent=cfg.latest('agent','researcher');body=dict(agent['body'],fallback_id='fallback',pre_dispatch_fallback_enabled=True)
    cfg.save('agent','researcher',body,expected_digest=agent['digest'])


def test_reviewed_policy_selects_before_reservation_and_preserves_receipt(setup):
    cfg,j,calls,c,form=setup;enable(cfg)
    selected=cfg.resolve_pre_dispatch('researcher','research','generate_text')
    assert selected['connection_id']=='fallback' and selected['selection_reason']=='pre_dispatch_policy'
    expected=hashlib.sha256(canonical(selected).encode()).hexdigest()
    no_review=j.runs.run(agent_id='researcher',task_id='no-review',resource_id='research',purpose='generate_text',prompt='No authority')
    assert no_review['state']=='denied' and calls==[]
    result=j.runs.run(agent_id='researcher',task_id='reviewed',resource_id='research',purpose='generate_text',prompt='Reviewed',expected_configuration_sha256=expected)
    assert result['state']=='completed' and len(calls)==1
    assert result['configuration']==selected and j.runs.request_export(result['id'])
    assert 'Reviewed pre-dispatch local fallback' in c.get('/workbench/requests/'+result['id']).text


def test_changed_selection_pause_and_uncertain_never_redispatch(setup):
    cfg,j,calls,c,form=setup;enable(cfg)
    selected=cfg.resolve_pre_dispatch('researcher','research','generate_text');expected=hashlib.sha256(canonical(selected).encode()).hexdigest()
    cfg.observe('connection','local','reachable','Primary recovered')
    result=j.runs.run(agent_id='researcher',task_id='changed',resource_id='research',purpose='generate_text',prompt='Refuse reselection',expected_configuration_sha256=expected)
    assert result['state']=='denied' and calls==[]
    cfg.observe('connection','local','unavailable','Unavailable again')
    with pytest.raises(KeyboardInterrupt):
        j.runs.run(agent_id='researcher',task_id='lost-ack',resource_id='research',purpose='generate_text',prompt='Uncertain',expected_configuration_sha256=expected,dispatch=lambda *args:(_ for _ in ()).throw(KeyboardInterrupt()))
    assert j.runs.recent()[0]['state']=='reserved' and calls==[]


def test_report_policy_recovery_and_action_remain_bound(report):
    cfg,runs,calls,actions,journey,call=report;enable(cfg)
    call['configuration']=cfg.resolve_pre_dispatch('researcher','summary.md','draft_text')
    proposal=journey.propose(**call);assert len(calls)==1
    assert journey.propose(**call,recover=True)==proposal and len(calls)==1
    view=actions.get(proposal['proposal_id']);approved=actions.decide(view['id'],'approved','Disposable text only','operator',view['head_hash'])
    assert actions.execute(view['id'],'broker',approved['head_hash'])['state']=='written'


def test_operator_policy_review_and_workbench_selection_are_explicit(setup):
    cfg,j,calls,c,form=setup
    cfg.save('connection','fallback',dict(cfg.latest('connection','local')['body']))
    cfg.observe('connection','local','unavailable','Unavailable fixture');cfg.observe('connection','fallback','reachable','Reachable fixture')
    agent=cfg.latest('agent','researcher');before=agent['body']
    data=dict(operator_token=form['operator_token'],fallback_id='fallback',expected_digest=agent['digest'],enabled='on')
    assert c.post('/configuration/agents/researcher/fallback-policy',data=dict(data,expected_digest='0'*64)).status_code==409
    assert c.post('/configuration/agents/researcher/fallback-policy',data=data,follow_redirects=False).status_code==303
    updated=cfg.latest('agent','researcher')['body'];assert updated['allowed_tools']==before['allowed_tools'] and updated['resources']==before['resources']
    assert 'reviewed policy fallback' in c.get('/workbench').text
    from swarmtracker_node_ui.overview import workflow
    assert workflow(cfg,j.runs,'researcher','research')['connection']=='fallback'


@pytest.mark.parametrize('blocked',['stale','disabled','purpose','paused','flag-absent'])
def test_unapproved_or_unavailable_fallback_never_dispatches(setup,blocked):
    cfg,j,calls,c,form=setup;enable(cfg)
    if blocked=='stale':
        with cfg._db() as db:db.execute("UPDATE observations SET at=? WHERE id='fallback'",(time.time()-600,))
    elif blocked=='disabled':cfg.disable_connection('fallback')
    elif blocked=='paused':cfg.set_routing('researcher',False,cfg.latest('agent','researcher')['digest'])
    elif blocked=='flag-absent':
        body=cfg.latest('agent','researcher')['body'];body.pop('pre_dispatch_fallback_enabled');cfg.save('agent','researcher',body)
    purpose='draft_text' if blocked=='purpose' else 'generate_text'
    selected=cfg.resolve_pre_dispatch('researcher','research',purpose)
    assert selected['connection_id']=='local'
    run=j.runs.run(agent_id='researcher',task_id='refused-'+blocked,resource_id='research',purpose=purpose,prompt='No dispatch',expected_configuration_sha256=hashlib.sha256(canonical(selected).encode()).hexdigest())
    assert run['state']=='denied' and calls==[]


def test_runtime_timeout_never_triggers_fallback_or_retry(setup):
    cfg,j,calls,c,form=setup;enable(cfg)
    selected=cfg.resolve_pre_dispatch('researcher','research','generate_text');sent=[]
    def failed(cfg,prompt,purpose):sent.append(cfg['connection_id']);raise TimeoutError('client deadline; server cancellation unknown')
    run=j.runs.run(agent_id='researcher',task_id='timeout-once',resource_id='research',purpose='generate_text',prompt='One attempt',expected_configuration_sha256=hashlib.sha256(canonical(selected).encode()).hexdigest(),dispatch=failed)
    assert run['state']=='failed' and sent==['fallback'] and calls==[]
