"""Operator controls must work for every already-supported bounded task profile."""
import copy
import json
import time
import pytest
from apps.node.swarmtracker_node_ui.session_coordinator import Coordinator, MODEL


def task(limit):
    return dict(id='Editable',purpose='Inspect a fixed harmless source packet',dependencies=[],
                authorization='explicit-read-only-glm-review',acceptance_criteria='Separate source and behavioral review',
                expected_outputs='A bounded proposal',validation_gates='Operator approval and source/deadline checks',
                model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=limit))


def row(c,table):
    with c.db() as db:return dict(db.execute('select * from '+table+' where '+('task_id' if table=='task_controls' else 'id')+'=?',('Editable',)).fetchone())


@pytest.mark.parametrize('limit',[2048,4096])
def test_supported_profile_pause_resume_survives_restart_without_redispatch(tmp_path,limit):
    c=Coordinator(tmp_path/'queue.db');spec=task(limit);c.create_operator_task(spec)
    c.authorize_operator_task('Editable','Operator inspected this exact bounded source contract')
    original=row(c,'task_controls')['authorization_hash']
    c.edit_operator_task('Editable',spec,pause=True,priority=17)
    reopened=Coordinator(c.path)
    assert row(reopened,'task_controls')['paused']==1
    assert row(reopened,'task_controls')['authorization_hash']==original
    reopened.edit_operator_task('Editable',spec,pause=False)
    assert row(reopened,'task_controls')['paused']==0
    assert row(reopened,'task_controls')['authorization_hash']==original
    saved=row(reopened,'tasks')
    assert saved['state']=='queued' and saved['attempts']==0 and saved['request_id'] is None


def test_changed_4096_contract_revokes_old_approval(tmp_path):
    c=Coordinator(tmp_path/'queue.db');spec=task(4096);c.create_operator_task(spec)
    c.authorize_operator_task('Editable','Operator inspected the original bounded source contract')
    changed={**spec,'purpose':'A separately revised source review assignment'}
    c.edit_operator_task('Editable',changed,pause=True)
    assert row(c,'task_controls')['authorization_hash'] is None
    assert row(c,'task_controls')['paused']==1
    assert json.loads(row(c,'tasks')['spec'])==changed
    assert row(c,'tasks')['request_id'] is None and row(c,'tasks')['attempts']==0


@pytest.mark.parametrize('change',['larger_output','different_reasoning'])
def test_unsupported_edit_refused_without_mutation(tmp_path,change):
    c=Coordinator(tmp_path/'queue.db');spec=task(4096);c.create_operator_task(spec)
    c.authorize_operator_task('Editable','Operator inspected this exact bounded source contract')
    before=(row(c,'tasks'),row(c,'task_controls'));changed=copy.deepcopy(spec)
    if change=='larger_output':changed['model_settings']['max_tokens']=8192
    else:changed['model_settings']['reasoning_effort']='medium'
    with pytest.raises(ValueError):c.edit_operator_task('Editable',changed,pause=True)
    assert (row(c,'tasks'),row(c,'task_controls'))==before


def test_edit_preserves_uncertain_attempt_history_receipt_and_dependency_hold(tmp_path):
    calls=[]
    def local_fixture(spec,request_id):
        calls.append(request_id)
        return dict(request_id=request_id,content='Retained synthetic history response',finish_reason='stop',usage={'completion_tokens':1})
    c=Coordinator(tmp_path/'queue.db',adapter=local_fixture)
    c.configure(rate=0,start=time.time()-1,ceiling=25,margin=5);c.control('running')
    historical={**task(2048),'id':'History'};c.enqueue(historical)
    assert c.run_one('fixture')=='History'
    uncertain={**task(2048),'id':'Uncertain'};c.enqueue(uncertain)
    claimed=c.claim('fixture');assert claimed['spec']['id']=='Uncertain'
    with c.db() as db:db.execute('update control set heartbeat=0')
    assert c.recover_stale()
    dependent={**task(2048),'id':'Dependent','dependencies':['History']};c.enqueue(dependent)
    editable=task(4096);c.create_operator_task(editable)
    c.authorize_operator_task('Editable','Operator inspected the supported queued source contract')
    def retained():
        with c.db() as db:
            return {name:dict(db.execute('select * from tasks where id=?',(name,)).fetchone()) for name in ['History','Uncertain','Dependent']}
    before=retained()
    assert before['History']['state']=='completed' and before['History']['response']
    assert before['Uncertain']['state']=='unresolved' and before['Uncertain']['attempts']==1
    assert before['Uncertain']['request_id']==claimed['request_id']
    c.edit_operator_task('Editable',editable,pause=True)
    with pytest.raises(ValueError):c.edit_operator_task('Uncertain',uncertain,pause=False)
    assert c.run_one('observer') is None  # History is completed, not independently accepted; Editable is paused.
    assert retained()==before
    assert len(calls)==1  # No uncertain replay, dependent dispatch or automatic acceptance.
    assert row(c,'tasks')['attempts']==0 and row(c,'tasks')['request_id'] is None
