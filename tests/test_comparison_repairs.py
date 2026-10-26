"""Frozen operator acceptance cases; no network or inference."""
import copy,json,time
from verity_core.trust.inference_receipt import configuration_snapshot
from swarmtracker_node_ui.session_coordinator import Coordinator,MODEL


def test_requested_runtime_options_survive_receipt_snapshot():
    original={'model':'fixture','runtime_options':{'keep_alive_seconds':90},'instructions':'PRIVATE','credential':'PRIVATE'}
    snapshot=configuration_snapshot(original)
    assert snapshot['runtime_options']=={'keep_alive_seconds':90}
    assert 'instructions' not in snapshot and 'credential' not in snapshot
    original['runtime_options']['keep_alive_seconds']=0
    assert snapshot['runtime_options']['keep_alive_seconds']==90
    assert 'runtime_options' not in configuration_snapshot({'model':'legacy'})


def test_reviewed_rejection_is_not_pending_review(tmp_path):
    c=Coordinator(tmp_path/'queue.db');c.configure(rate=0,start=time.time()-1,ceiling=1,margin=0,deadline=time.time()+120)
    for name in ['Pending','Rejected','Accepted']:
        spec=dict(id=name,purpose='Review supplied fixture',dependencies=[],authorization='explicit-read-only-glm-review',acceptance_criteria='Separate source review',expected_outputs='Bounded answer',validation_gates='Independent review',model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=2048))
        c.create_operator_task(spec)
        with c.db() as db:
            db.execute("UPDATE tasks SET state='completed',finish_reason='stop',response='{}' WHERE id=?",(name,))
            a=dict(runtime=dict(model='fixture',endpoint='http://127.0.0.1:12345'),source_commit='0'*40,session_id='fixture')
            db.execute('INSERT INTO execution_assignments(task_id,batch_id,agent_id,hash,spec) VALUES (?,?,?,?,?)',(name,'fixture','fixture','fixture',json.dumps(a)))
    c.reject_review('Rejected','separate verifier','Wrong literal source answer')
    c.verify('Accepted','separate verifier','Literal source answer checked')
    v=c.execution_status();rows={x['task_id']:x for x in v['assignments']}
    assert rows['Pending']['awaiting_review'] is True
    assert rows['Rejected']['awaiting_review'] is False
    assert rows['Accepted']['awaiting_review'] is False
    assert v['awaiting_review']==1
    with c.db() as db:
        row=db.execute("SELECT state,verifier,evidence FROM tasks WHERE id='Rejected'").fetchone()
        assert tuple(row)==('completed','separate verifier','Rejected: Wrong literal source answer')
