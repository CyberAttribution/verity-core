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
    a=dict(input_path=str(source),input_sha256=sha(source.read_bytes()),expires_at=time.time()+90)
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


@pytest.mark.parametrize('change',['review','contract','source','pause','outside','traversal','expiry'])
def test_contract_review_and_path_gates_prevent_mutation(tmp_path,change):
    c,cp,h,proposal,root=fixture(tmp_path);original=(root/'tools/fixture.py').read_bytes();ph=sha(proposal.encode())
    if change=='review':ph='0'*64
    if change=='contract':h='0'*64
    if change=='source':(root/'tools/fixture.py').write_text('changed')
    if change=='pause':c.control('paused')
    if change=='expiry':
        with c.db() as db:
            a=json.loads(db.execute("SELECT spec FROM execution_assignments WHERE task_id='Patch'").fetchone()[0]);a['expires_at']=time.time()-1
            db.execute("UPDATE execution_assignments SET spec=? WHERE task_id='Patch'",(json.dumps(a),))
    if change in {'outside','traversal','expiry'}:
        p=json.loads(proposal);p['changes'][0]['file']='tools/other.py' if change=='outside' else 'tools/../../secret.py';proposal=json.dumps(p);ph=sha(proposal.encode())
        with c.db() as db:db.execute("UPDATE tasks SET response=? WHERE id='Patch'",(json.dumps(dict(content=proposal)),))
    with pytest.raises(ValueError):execute_patch(c.path,'Patch',cp,h,ph)
    assert (root/'tools/fixture.py').read_bytes()==(b'changed' if change=='source' else original)
    assert not (tmp_path/'artifacts').exists()


def test_single_json_fence_preserves_exact_reviewed_response_hash(tmp_path):
    c,cp,h,proposal,root=fixture(tmp_path)
    raw='```json\n'+proposal+'\n```'
    with c.db() as db:db.execute("UPDATE tasks SET response=? WHERE id='Patch'",(json.dumps(dict(content=raw)),))
    receipt=execute_patch(c.path,'Patch',cp,h,sha(raw.encode()))
    assert receipt['state']=='tested' and receipt['format_normalization']=='single JSON fence removed'
    assert (tmp_path/'artifacts/proposal.json').read_text()==raw


def test_operator_correction_keeps_rejected_model_proposal_separate(tmp_path):
    c,cp,h,proposal,root=fixture(tmp_path)
    with c.db() as db:db.execute("UPDATE tasks SET response=?,evidence='Rejected: wrong model patch' WHERE id='Patch'",(json.dumps(dict(content='invalid model suggestion')),))
    op=tmp_path/'operator-proposal.json';op.write_text(proposal)
    with pytest.raises(ValueError):execute_patch(c.path,'Patch',cp,h,sha(proposal.encode()),operator_proposal_path=str(op))
    receipt=execute_patch(c.path,'Patch',cp,h,sha(proposal.encode()),operator_proposal_path=str(op),correction_authorization='Execution coordinator corrects patch within the original prebound behavior and file contract')
    assert receipt['state']=='tested' and receipt['proposal_origin']=='operator correction'
    assert (tmp_path/'artifacts/original-model-proposal.txt').read_text()=='invalid model suggestion'
    with c.db() as db:assert db.execute("SELECT evidence FROM tasks WHERE id='Patch'").fetchone()[0].startswith('Rejected:')


@pytest.mark.parametrize('template,allowed',[('_prepared_execution.html',True),('other.html',False)])
def test_only_named_prebound_review_template_is_supported(tmp_path,template,allowed):
    c,cp,h,proposal,root=fixture(tmp_path)
    name='apps/node/swarmtracker_node_ui/templates/'+template
    page=root/name;page.parent.mkdir(parents=True);page.write_text('Pending review')
    (root/'tests/test_behavior.py').write_text('from pathlib import Path\ndef test_visible_rejection():\n    assert Path('+repr(name)+').read_text() == "Review rejected"\n')
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','prepared template fixture'],check=True)
    contract=json.loads(cp.read_text());contract.update(starting_commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),permitted_files={name:sha(page.read_bytes())});cp.write_text(json.dumps(contract));h=sha(cp.read_bytes())
    packet=tmp_path/'packet.txt';packet.write_text('Execution contract SHA256: '+h)
    proposal=json.dumps({'changes':[{'file':name,'old':'Pending review','new':'Review rejected'}]})
    with c.db() as db:
        a=json.loads(db.execute("SELECT spec FROM execution_assignments WHERE task_id='Patch'").fetchone()[0]);a.update(input_path=str(packet),input_sha256=sha(packet.read_bytes()));db.execute("UPDATE execution_assignments SET spec=? WHERE task_id='Patch'",(json.dumps(a),));db.execute("UPDATE tasks SET response=? WHERE id='Patch'",(json.dumps(dict(content=proposal)),))
    if allowed:
        receipt=execute_patch(c.path,'Patch',cp,h,sha(proposal.encode()));assert receipt['state']=='tested' and page.read_text()=='Review rejected'
    else:
        with pytest.raises(ValueError):execute_patch(c.path,'Patch',cp,h,sha(proposal.encode()))
        assert page.read_text()=='Pending review' and not (tmp_path/'artifacts').exists()
