import hashlib,json,subprocess,sys,time
from pathlib import Path
import pytest
from swarmtracker_node_ui.session_coordinator import Coordinator,MODEL
from tools.apply_prepared_patch import execute_patch,sha


def fixture(tmp_path):
    root=tmp_path/'source';(root/'tools').mkdir(parents=True);(root/'tests').mkdir()
    f=root/'tools/fixture.py';f.write_text('def allowed(state):\n    return state in ("completed", "independently_verified")\n')
    (root/'tests/test_behavior.py').write_text('import runpy\ndef test_acceptance_gate():\n    allowed=runpy.run_path("tools/fixture.py")["allowed"]\n    assert allowed("independently_verified") is True\n    assert allowed("completed") is False\n    assert allowed("unresolved") is False\n')
    for args in [('init','-q'),('add','.'),('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','base')]:subprocess.run(['git','-C',str(root),*args],check=True)
    commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    c=Coordinator(tmp_path/'queue.db');c.configure(rate=0,start=time.time()-1,ceiling=1,margin=0,deadline=time.time()+120)
    spec=dict(id='Patch',purpose='Fix acceptance dependency gate',dependencies=[],authorization='explicit-read-only-glm-review',acceptance_criteria='Operator separately reviews literal edit',expected_outputs='JSON proposal',validation_gates='Separate tests and acceptance',model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=2048))
    c.create_operator_task(spec)
    contract=dict(task_id='Patch',worktree=str(root),starting_commit=commit,permitted_files={'tools/fixture.py':sha(f.read_bytes())},tests=['tests/test_behavior.py'],python=sys.executable,max_seconds=30,artifact_directory=str(tmp_path/'artifacts'),acceptor='Independent operator')
    cp=tmp_path/'contract.json';cp.write_text(json.dumps(contract));h=sha(cp.read_bytes());source=tmp_path/'packet.txt';source.write_text('Execution contract SHA256: '+h)
    a=dict(input_path=str(source),input_sha256=sha(source.read_bytes()))
    proposal=json.dumps({'changes':[{'file':'tools/fixture.py','old':'return state in ("completed", "independently_verified")','new':'return state == "independently_verified"'}]})
    with c.db() as db:
        db.execute("UPDATE tasks SET state='completed',request_id='request1',finish_reason='stop',response=? WHERE id='Patch'",(json.dumps(dict(content=proposal)),))
        db.execute("UPDATE task_controls SET authorization_hash=? WHERE task_id='Patch'",(c._spec_hash(spec),))
        db.execute('INSERT INTO execution_assignments(task_id,batch_id,agent_id,hash,spec) VALUES (?,?,?,?,?)',('Patch','batch','lane','x',json.dumps(a)))
    c.control('running')
    return c,cp,h,proposal,root


def test_actual_patch_and_tests_retain_receipt_without_self_acceptance(tmp_path):
    c,cp,h,proposal,root=fixture(tmp_path)
    receipt=execute_patch(c.path,'Patch',cp,h,sha(proposal.encode()))
    assert receipt['state']=='tested' and receipt['test_returncode']==0
    assert (tmp_path/'artifacts/patch.diff').is_file()
    with c.db() as db:
        assert db.execute("SELECT state FROM tasks WHERE id='Patch'").fetchone()[0]=='completed'
        assert db.execute('SELECT state FROM coding_executions').fetchone()[0]=='tested'
    with pytest.raises(ValueError):execute_patch(c.path,'Patch',cp,h,sha(proposal.encode()))


@pytest.mark.parametrize('change',['review','contract','source','pause','outside','traversal'])
def test_contract_review_and_path_gates_prevent_mutation(tmp_path,change):
    c,cp,h,proposal,root=fixture(tmp_path);original=(root/'tools/fixture.py').read_bytes();ph=sha(proposal.encode())
    if change=='review':ph='0'*64
    if change=='contract':h='0'*64
    if change=='source':(root/'tools/fixture.py').write_text('changed')
    if change=='pause':c.control('paused')
    if change in {'outside','traversal'}:
        p=json.loads(proposal);p['changes'][0]['file']='tools/other.py' if change=='outside' else 'tools/../../secret.py';proposal=json.dumps(p);ph=sha(proposal.encode())
        with c.db() as db:db.execute("UPDATE tasks SET response=? WHERE id='Patch'",(json.dumps(dict(content=proposal)),))
    with pytest.raises(ValueError):execute_patch(c.path,'Patch',cp,h,ph)
    assert (root/'tools/fixture.py').read_bytes()==(b'changed' if change=='source' else original)
    assert not (tmp_path/'artifacts').exists()
