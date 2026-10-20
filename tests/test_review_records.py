import json
import hashlib
import pytest
from swarmtracker_node_ui.review_records import load,ingest,adjudicate
from swarmtracker_node_ui.session_coordinator import Coordinator


COMMIT='a'*40


def fixture(tmp_path,finish='stop'):
    p=tmp_path/'ReviewTask';p.mkdir();source='Synthetic private source';hash=hashlib.sha256(source.encode()).hexdigest()
    payload=dict(model='glm-5.3-flash',reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},messages=[dict(role='system',content='Read only'),dict(role='user',content='Question\nSOURCE PACKET:\n'+source)])
    response=dict(model='glm-5.3-flash',choices=[dict(finish_reason=finish,message=dict(content='Synthetic private answer' if finish=='stop' else None))],usage=dict(completion_tokens=4))
    raw=json.dumps(response).encode();(p/'response.raw.json').write_bytes(raw)
    result=dict(task_id='ReviewTask',input_commit=COMMIT,source_sha256=hash,response_sha256=hashlib.sha256(raw).hexdigest(),finish_reason=finish,state='awaiting_independent_review' if finish=='stop' else 'incomplete')
    (p/'request.json').write_text(json.dumps(dict(input_commit=COMMIT,source_sha256=hash,payload=payload)));(p/'result.json').write_text(json.dumps(result))
    return p,Coordinator(tmp_path/'work.db')


def test_import_is_idempotent_and_not_automatic_acceptance(tmp_path):
    p,c=fixture(tmp_path);first=ingest(p,c,session_id='session');assert first['imported'] and first['automatic_acceptance'] is False
    assert not ingest(p,c,session_id='session')['imported']
    rows=c.snapshot()['work_items'];assert len(rows)==1 and rows[0]['state']=='awaiting_review'
    assert 'Synthetic private source' not in json.dumps(rows) and 'Synthetic private answer' not in json.dumps(rows)
    assert adjudicate(c,'ReviewTask',disposition='supported_guard',actor='Independent test reviewer',checkpoint=COMMIT,evidence='Focused race tests passed')
    assert not adjudicate(c,'ReviewTask',disposition='supported_guard',actor='Independent test reviewer',checkpoint=COMMIT,evidence='Focused race tests passed')
    ingest(p,c,session_id='session');assert c.snapshot()['work_items'][0]['state']=='finished'


@pytest.mark.parametrize('corruption',['low','source','response','commit'])
def test_bad_bindings_fail_before_work_is_inserted(tmp_path,corruption):
    p,c=fixture(tmp_path)
    if corruption=='response':(p/'response.raw.json').write_bytes(b'{}')
    else:
        request=json.loads((p/'request.json').read_text())
        if corruption=='low':request['payload']['reasoning_effort']='medium'
        if corruption=='source':request['payload']['messages'][1]['content']+=' altered'
        if corruption=='commit':request['input_commit']='b'*40
        (p/'request.json').write_text(json.dumps(request))
    with pytest.raises(ValueError):ingest(p,c,session_id='session')
    assert not c.snapshot()['work_items']


def test_same_task_id_different_response_or_session_cannot_replace_history(tmp_path):
    p,c=fixture(tmp_path);ingest(p,c,session_id='session')
    with pytest.raises(ValueError):ingest(p,c,session_id='other')
    response=json.loads((p/'response.raw.json').read_bytes());response['choices'][0]['message']['content']='Different answer';raw=json.dumps(response).encode();(p/'response.raw.json').write_bytes(raw)
    result=json.loads((p/'result.json').read_text());result['response_sha256']=hashlib.sha256(raw).hexdigest();(p/'result.json').write_text(json.dumps(result))
    with pytest.raises(ValueError):ingest(p,c,session_id='session')
    assert len(c.snapshot()['work_items'])==1


def test_token_limited_response_cannot_be_adjudicated_as_accepted(tmp_path):
    p,c=fixture(tmp_path,'length');ingest(p,c,session_id='session');assert c.snapshot()['work_items'][0]['state']=='failed'
    with pytest.raises(ValueError):adjudicate(c,'ReviewTask',disposition='accepted_fix',actor='Reviewer',checkpoint=COMMIT,evidence='Partial text is not final')
    assert adjudicate(c,'ReviewTask',disposition='incomplete',actor='Reviewer',checkpoint=COMMIT,evidence='Raw finish reason length; no final answer')


def test_fix_checkpoint_is_separate_from_immutable_review_input(tmp_path):
    p,c=fixture(tmp_path);ingest(p,c,session_id='session')
    fix='b'*40
    adjudicate(c,'ReviewTask',disposition='accepted_fix',actor='Independent test reviewer',checkpoint=fix,evidence='Later fix tested against retained reviewed source')
    with c.db() as db:
        observation=json.loads(db.execute('SELECT observation FROM review_imports WHERE id=?',('ReviewTask',)).fetchone()[0])
        assessment=json.loads(db.execute('SELECT assessment FROM review_assessments WHERE id=?',('ReviewTask',)).fetchone()[0])
    assert observation['input_commit']==COMMIT
    assert assessment['checkpoint']==fix
    ingest(p,c,session_id='session')
    assert c.snapshot()['work_items'][0]['checkpoint']==fix
