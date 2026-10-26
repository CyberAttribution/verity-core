"""Apply a separately reviewed model proposal under a prebound local contract.

No generated shell, installation, publication, merge or automatic acceptance.
This is an operator-governed executor, not an OS security boundary.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'apps/node')]
from swarmtracker_node_ui.session_coordinator import Coordinator


def sha(data):
    return hashlib.sha256(data).hexdigest()


def execute_patch(db_path, task_id, contract_path, contract_sha256, reviewed_proposal_sha256, *, operator_proposal_path=None, correction_authorization=None):
    raw=Path(contract_path).read_bytes()
    if len(raw)>65536 or sha(raw)!=contract_sha256:raise ValueError('Contract hash mismatch')
    contract=json.loads(raw)
    keys={'task_id','worktree','starting_commit','permitted_files','tests','python','max_seconds','artifact_directory','acceptor'}
    if set(contract)!=keys or contract['task_id']!=task_id:raise ValueError('Invalid execution contract')
    if not 1<=contract['max_seconds']<=120 or not contract['acceptor']:raise ValueError('Bounded tests and independent acceptor required')
    root=Path(contract['worktree']);artifacts=Path(contract['artifact_directory'])
    if not root.is_absolute() or not artifacts.is_absolute() or root.resolve()==artifacts.resolve() or root.resolve() in artifacts.resolve().parents:raise ValueError('Isolated absolute worktree and artifact directory required')
    if not re.fullmatch('[a-f0-9]{40}',contract['starting_commit']):raise ValueError('Exact starting commit required')
    py=Path(contract['python'])
    if not py.is_absolute() or not py.is_file():raise ValueError('Prepared Python executable required')
    tests=contract['tests']
    if not isinstance(tests,list) or not 1<=len(tests)<=4 or any(not re.fullmatch(r'tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py',x) for x in tests):raise ValueError('Only explicit existing pytest files allowed')
    files=contract['permitted_files']
    if not isinstance(files,dict) or not 1<=len(files)<=3:raise ValueError('Finite file allowlist required')
    for name,h in files.items():
        p=Path(name)
        if not re.fullmatch(r'(?:apps/node/swarmtracker_node_ui/|verity_core/trust/|tools/)[A-Za-z0-9_/]+\.py',name) or '..' in p.parts or not re.fullmatch('[a-f0-9]{64}',h):raise ValueError('Only prehashed source files allowed')
        if (root/p).is_symlink() or root.resolve() not in (root/p).resolve().parents:raise ValueError('Unsafe source path')
    for name in tests:
        if (root/name).is_symlink() or root.resolve() not in (root/name).resolve().parents or not (root/name).is_file():raise ValueError('Unsafe test path')
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True,timeout=5).strip()
    if git('rev-parse','HEAD')!=contract['starting_commit'] or git('status','--porcelain'):raise ValueError('Worktree must be clean at the authorized commit')
    for name,h in files.items():
        if sha((root/name).read_bytes())!=h:raise ValueError('Source hash changed')
    c=Coordinator(db_path);c.claim_execution_host()
    if c.dispatch_gate(c.snapshot()['control']):raise ValueError('Session dispatch gates prevent code execution')
    with c.db() as db:
        row=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        a=db.execute('SELECT spec FROM execution_assignments WHERE task_id=?',(task_id,)).fetchone()
        if not row or row['state']!='completed' or row['finish_reason']!='stop' or not a:raise ValueError('Completed bound proposal required')
        assignment=json.loads(a['spec'])
        if c.clock()>=assignment['expires_at']:raise ValueError('Assignment authorization expired')
        control=db.execute('SELECT * FROM control WHERE id=1').fetchone()
        if c.dispatch_gate(dict(control) if control else None):raise ValueError('Session dispatch gates prevent code execution')
        if contract_sha256 not in c.bound_input(assignment):raise ValueError('Contract was not bound before model dispatch')
        ctl=db.execute('SELECT * FROM task_controls WHERE task_id=?',(task_id,)).fetchone()
        if not ctl or ctl['paused'] or ctl['cancelled'] or ctl['cancellation_requested'] or ctl['authorization_hash']!=c._spec_hash(json.loads(row['spec'])):raise ValueError('Task authority changed')
        response=json.loads(row['response']);model_proposal_text=response['content'];proposal_text=model_proposal_text
        if operator_proposal_path is not None:
            if not isinstance(correction_authorization,str) or not 20<=len(correction_authorization)<=1000:raise ValueError('Explicit operator correction authority required')
            op=Path(operator_proposal_path)
            if not op.is_absolute() or op.is_symlink() or op.stat().st_size>30000:raise ValueError('Bounded local operator proposal required')
            proposal_text=op.read_text()
        elif correction_authorization is not None:raise ValueError('Correction must identify its exact local proposal')
        if len(proposal_text)>30000 or sha(proposal_text.encode())!=reviewed_proposal_sha256:raise ValueError('Exact proposal requires independent operator review')
        normalized=proposal_text.strip()
        fenced=re.fullmatch(r'```(?:json)?[ \t]*\n(.*?)\n```',normalized,re.S)
        if fenced:normalized=fenced.group(1)
        proposal=json.loads(normalized)
        if set(proposal)!={'changes'} or not isinstance(proposal['changes'],list) or not 1<=len(proposal['changes'])<=3:raise ValueError('Only finite literal replacements accepted')
        pending={};changed=set()
        for change in proposal['changes']:
            if set(change)!={'file','old','new'} or change['file'] not in files or change['file'] in changed:raise ValueError('Proposal exceeds contract')
            if not all(isinstance(change[k],str) and 0<len(change[k])<=12000 for k in ('old','new')):raise ValueError('Invalid bounded replacement')
            name=change['file'];text=(root/name).read_text()
            if text.count(change['old'])!=1 or change['old']==change['new']:raise ValueError('Replacement must uniquely change source')
            pending[name]=text.replace(change['old'],change['new']);changed.add(name)
        db.execute('CREATE TABLE IF NOT EXISTS coding_executions(task_id TEXT PRIMARY KEY,execution_id TEXT UNIQUE NOT NULL,contract_sha256 TEXT NOT NULL,proposal_sha256 TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT NOT NULL)')
        execution_id=uuid.uuid4().hex
        receipt=dict(execution_id=execution_id,task_id=task_id,request_id=row['request_id'],contract_sha256=contract_sha256,proposal_sha256=reviewed_proposal_sha256,started=time.time(),proposal_origin='operator correction' if operator_proposal_path else 'model',model_proposal_sha256=sha(model_proposal_text.encode()),correction_authorization=correction_authorization,format_normalization='single JSON fence removed' if fenced else 'none',worktree=str(root),starting_commit=contract['starting_commit'],acceptor=contract['acceptor'])
        # Durable identity before any filesystem mutation. Failed/uncertain runs
        # are retained and cannot be blindly rerun under this task identity.
        db.execute('INSERT INTO coding_executions VALUES (?,?,?,?,?,?)',(task_id,execution_id,contract_sha256,reviewed_proposal_sha256,'started',json.dumps(receipt)))
    state='failed'
    try:
        artifacts.mkdir(parents=True,exist_ok=False)
        (artifacts/'contract.json').write_bytes(raw);(artifacts/'proposal.json').write_text(proposal_text);(artifacts/'original-model-proposal.txt').write_text(model_proposal_text)
        for name,text in pending.items():(root/name).write_text(text)
        (artifacts/'patch.diff').write_text(git('diff','--',*sorted(changed))+'\n')
        # Commands and interpreter come only from the prebound operator contract.
        env={k:v for k,v in os.environ.items() if k in {'PATH','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
        env.update(PYTHONPATH=str(root)+os.pathsep+str(root/'apps/node'),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1')
        with (artifacts/'tests.log').open('wb') as log:
            result=subprocess.run([str(py),'-m','pytest','-q',*tests],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=contract['max_seconds'],check=False)
        receipt.update(test_returncode=result.returncode,changed_files=sorted(changed));state='tested' if result.returncode==0 else 'failed'
    except BaseException as exc:
        receipt['error']=type(exc).__name__+': '+str(exc)[:300]
        raise
    finally:
        receipt.update(ended=time.time(),state=state,acceptance='pending independent review; no automatic commit or merge')
        if artifacts.exists():(artifacts/'receipt.json').write_text(json.dumps(receipt,indent=2))
        with c.db() as db:db.execute('UPDATE coding_executions SET state=?,receipt=? WHERE task_id=?',(state,json.dumps(receipt),task_id))
    return receipt


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['db','task','contract','contract-sha256','reviewed-proposal-sha256']:p.add_argument('--'+key,required=True)
    p.add_argument('--operator-proposal');p.add_argument('--correction-authorization-file');p.add_argument('--prefect',action='store_true')
    a=p.parse_args()
    kwargs=dict(operator_proposal_path=a.operator_proposal,correction_authorization=Path(a.correction_authorization_file).read_text() if a.correction_authorization_file else None)
    def run():
        receipt=execute_patch(a.db,a.task,a.contract,a.contract_sha256,a.reviewed_proposal_sha256,**kwargs)
        if a.prefect:
            from prefect.context import get_run_context
            receipt['prefect_flow_run_id']=str(get_run_context().flow_run.id)
        return receipt
    if a.prefect:
        if os.environ.get('PREFECT_API_URL')!='http://127.0.0.1:4200/api':raise ValueError('Existing self-hosted loopback Prefect API required')
        from prefect import flow
        run=flow(name='verity-reviewed-patch',retries=0,persist_result=False,log_prints=False)(run)
    print(json.dumps(run(),indent=2))
