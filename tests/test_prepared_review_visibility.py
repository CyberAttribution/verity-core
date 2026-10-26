"""Actual ledger-to-existing-template rejection visibility, without dispatch."""
from pathlib import Path
import json,time
from jinja2 import Environment,FileSystemLoader,select_autoescape
from swarmtracker_node_ui.session_coordinator import Coordinator,MODEL


def test_recorded_rejection_visible_without_releasing_any_authority(tmp_path):
    c=Coordinator(tmp_path/'queue.db');c.configure(rate=0,start=time.time()-1,ceiling=1,margin=0,deadline=time.time()+120)
    for name in ['PendingReview','RejectedReview']:
        spec=dict(id=name,purpose='Review fixture',dependencies=[],authorization='explicit-read-only-glm-review',acceptance_criteria='Separate source review',expected_outputs='Bounded answer',validation_gates='Independent review',model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=2048))
        c.create_operator_task(spec)
        with c.db() as db:
            db.execute("UPDATE tasks SET state='completed',finish_reason='stop',response='{}' WHERE id=?",(name,))
            a=dict(runtime=dict(model='fixture',endpoint='http://127.0.0.1:12345'),source_commit='0'*40,session_id='fixture')
            db.execute('INSERT INTO execution_assignments(task_id,batch_id,agent_id,hash,spec) VALUES (?,?,?,?,?)',(name,'fixture','fixture','fixture',json.dumps(a)))
    c.reject_review('RejectedReview','FixtureReviewer','Literal <script>alert(1)</script> claim contradicted')
    before=c.snapshot()['tasks'];view=c.execution_status()
    templates=Path(__file__).resolve().parents[1]/'apps/node/swarmtracker_node_ui/templates'
    text=Environment(loader=FileSystemLoader(templates),autoescape=select_autoescape()).get_template('_prepared_execution.html').render(prepared_execution=view)
    assert 'review rejected' in text.lower()
    assert 'FixtureReviewer' in text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in text and '<script>alert(1)</script>' not in text
    assert 'Independent acceptance pending' in text or 'review pending' in text.lower()
    assert c.snapshot()['tasks']==before
    assert all(x['state']=='completed' and x['attempts']==0 for x in before)
