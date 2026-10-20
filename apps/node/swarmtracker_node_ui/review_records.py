"""Import retained GLM observations into the durable work ledger, never accept them.

Hashes check retained byte/source bindings, not authenticated model origin or
claim truth. Private source packets and response bodies stay outside Git/UI.
"""
from pathlib import Path
import hashlib
import json
import re
import time
from verity_core.json_safety import bounded_json_loads


LIMIT=1048576


def read(path):
    with Path(path).open('rb') as f:raw=f.read(LIMIT+1)
    if len(raw)>LIMIT:raise ValueError('Review artifact exceeds bound')
    value=bounded_json_loads(raw,max_depth=32)
    if not isinstance(value,dict):raise ValueError('Review artifact must be an object')
    return raw,value


def load(directory):
    directory=Path(directory)
    _,request=read(directory/'request.json');_,result=read(directory/'result.json');raw,response=read(directory/'response.raw.json')
    task=result.get('task_id')
    if not isinstance(task,str) or not task.isalnum() or not 1<=len(task)<=80 or directory.name!=task:
        raise ValueError('Review identity mismatch')
    commit=result.get('input_commit');source_hash=result.get('source_sha256')
    if not isinstance(commit,str) or not re.fullmatch('[a-f0-9]{40}',commit):raise ValueError('Pinned review commit required')
    if not isinstance(source_hash,str) or not re.fullmatch('[a-f0-9]{64}',source_hash):raise ValueError('Pinned source hash required')
    if request.get('input_commit')!=commit or request.get('source_sha256')!=source_hash:
        raise ValueError('Request/result source mismatch')
    payload=request.get('payload')
    if not isinstance(payload,dict):raise ValueError('Review request payload required')
    messages=payload.get('messages')
    if payload.get('model')!='glm-5.3-flash' or payload.get('reasoning_effort')!='low' or payload.get('chat_template_kwargs')!={'reasoning_effort':'low'}:
        raise ValueError('Both GLM low fields required')
    if not isinstance(messages,list) or len(messages)!=2 or not isinstance(messages[1],dict) or messages[1].get('role')!='user':raise ValueError('Invalid review source packet')
    text=messages[1].get('content')
    if not isinstance(text,str) or '\nSOURCE PACKET:\n' not in text:raise ValueError('Missing review source')
    source=text.split('\nSOURCE PACKET:\n',1)[1]
    if hashlib.sha256(source.encode()).hexdigest()!=source_hash:raise ValueError('Source bytes differ')
    digest=hashlib.sha256(raw).hexdigest()
    if result.get('response_sha256')!=digest or response.get('model')!='glm-5.3-flash':raise ValueError('Response bytes or model differ')
    choices=response.get('choices')
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict) or not isinstance(choices[0].get('message'),dict):raise ValueError('Single review response choice required')
    choice=choices[0];finish=choice.get('finish_reason');answer=choice['message'].get('content')
    complete=finish=='stop' and isinstance(answer,str) and bool(answer.strip())
    expected='awaiting_independent_review' if complete else 'incomplete'
    if result.get('state')!=expected or result.get('finish_reason')!=finish:raise ValueError('Review completion metadata differs')
    return dict(task_id=task,input_commit=commit,source_sha256=source_hash,response_sha256=digest,finish_reason=finish,
                state='awaiting_review' if complete else 'failed',model=response['model'],usage=response.get('usage'),
                evidence_path=str(directory),automatic_acceptance=False)


def ingest(directory,coordinator,*,session_id):
    """Stable IDs plus exact bindings make replay idempotent and conflicts fail."""
    observation=load(directory)
    if not isinstance(session_id,str) or not re.fullmatch('[A-Za-z0-9._-]{1,80}',session_id):raise ValueError('Session identity required')
    task=observation['task_id'];binding={k:observation[k] for k in ('input_commit','source_sha256','response_sha256')};binding['session_id']=session_id
    raw_binding=json.dumps(binding,sort_keys=True,separators=(',',':'))
    spec=dict(id=task,purpose='Source-pinned GLM review; answer awaits independent adjudication',dependencies=[],authorization='explicit-product-development',
              acceptance_criteria='Independent source reproduction and meaningful tests; model completion is not acceptance',expected_outputs=observation['evidence_path'],
              validation_gates='Pinned source/response/commit and both low fields checked',agent_id='glm-review',session_id=session_id)
    with coordinator.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS review_imports (id TEXT PRIMARY KEY,binding TEXT NOT NULL,observation TEXT NOT NULL,imported_at REAL NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT binding FROM review_imports WHERE id=?',(task,)).fetchone()
        if prior:
            if prior['binding']!=raw_binding:raise ValueError('Conflicting review identity or evidence')
            return dict(observation,imported=False)
        if db.execute('SELECT 1 FROM work_items WHERE id=?',(task,)).fetchone():raise ValueError('Work ID belongs to another contract')
        now=time.time();encoded=json.dumps(observation,sort_keys=True)
        db.execute('INSERT INTO work_items(id,spec,state,evidence,updated) VALUES (?,?,?,?,?)',(task,json.dumps(spec,sort_keys=True),observation['state'],encoded,now))
        db.execute('INSERT INTO review_imports VALUES (?,?,?,?)',(task,raw_binding,encoded,now))
    return dict(observation,imported=True)


def adjudicate(coordinator,task_id,*,disposition,actor,checkpoint,evidence):
    """Record an attributed independent review; never infer it from model prose.

    checkpoint identifies the independently assessed fix/validation source. It may
    follow the model input commit, which remains immutable in review_imports.
    """
    if disposition not in {'accepted_fix','supported_guard','advisory','rejected','incomplete','unresolved'}:
        raise ValueError('Explicit review disposition required')
    for value,limit in ((actor,160),(evidence,2000)):
        if not isinstance(value,str) or not 0<len(value)<=limit or any(ord(c)<32 for c in value):raise ValueError('Reviewer and evidence required')
    if not isinstance(checkpoint,str) or not re.fullmatch('[a-f0-9]{40}',checkpoint):raise ValueError('Reviewed source checkpoint required')
    assessment=dict(disposition=disposition,actor=actor,checkpoint=checkpoint,evidence=evidence,authenticated_identity=False)
    encoded=json.dumps(assessment,sort_keys=True)
    with coordinator.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS review_assessments (id TEXT NOT NULL,version INTEGER NOT NULL,assessment TEXT NOT NULL,at REAL NOT NULL,PRIMARY KEY(id,version))')
        db.execute('BEGIN IMMEDIATE')
        imported=db.execute('SELECT observation FROM review_imports WHERE id=?',(task_id,)).fetchone()
        if not imported:raise ValueError('Verified retained review import required')
        observation=json.loads(imported['observation'])
        if observation['state']=='failed' and disposition in {'accepted_fix','supported_guard','advisory'}:
            raise ValueError('Incomplete model response cannot become accepted review')
        prior=db.execute('SELECT version,assessment FROM review_assessments WHERE id=? ORDER BY version DESC LIMIT 1',(task_id,)).fetchone()
        if prior and prior['assessment']==encoded:return False
        version=prior['version']+1 if prior else 1
        db.execute('INSERT INTO review_assessments VALUES (?,?,?,?)',(task_id,version,encoded,time.time()))
        state='finished' if disposition in {'accepted_fix','supported_guard','advisory'} else 'failed' if disposition in {'rejected','incomplete'} else 'awaiting_review'
        db.execute('UPDATE work_items SET state=?,checkpoint=?,evidence=?,updated=? WHERE id=?',(state,checkpoint,encoded,time.time(),task_id))
    return True
