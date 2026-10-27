"""Real loopback streaming fixtures; synthetic inference, no paid providers."""
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from swarmtracker_node_ui.session_coordinator import Coordinator, MODEL
from tools.prefect_session import _execute, run_loop
from swarmtracker_node_ui.session_batch import digest


def task(name, deps=()):
    return dict(id=name,purpose='Read supplied fixture '+name,dependencies=list(deps),
        authorization='explicit-read-only-glm-review',acceptance_criteria='Return fixture result; no acceptance authority',
        model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=2048),
        expected_outputs='Bounded analysis',validation_gates='Independent fixture evaluator')


@pytest.fixture
def endpoints():
    calls=[];servers=[];threads=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            name=data['messages'][1]['content'].splitlines()[0].rsplit(' ',1)[-1]
            calls.append(dict(name=name,request_id=self.headers['X-Request-ID'],payload=data,at=time.monotonic(),port=self.server.server_port))
            if name=='Refusal':
                self.send_response(400);self.end_headers();self.wfile.write(b'{"error":"unsupported request profile"}');return
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
            try:
                time.sleep(.1)
                if name.startswith('Lost'):return
                if name.startswith('Trickle'):
                    for _ in range(100):self.wfile.write(b': alive\n');self.wfile.flush();time.sleep(.05)
                    return
                def send(x):self.wfile.write(('data: '+json.dumps(x)+'\n\n').encode());self.wfile.flush()
                if name=='NativeToolStream':
                    send(dict(id='server-native',model='fixture-model',choices=[dict(index=0,delta={'tool_calls':[dict(index=0,id='call_native',type='function',function={'name':'read_fixture','arguments':'{"fixture_'})]})]))
                    send(dict(model='fixture-model',choices=[dict(index=0,delta={'tool_calls':[dict(index=0,function={'arguments':'id":"manual"}'})]},finish_reason='tool_calls')],usage={'completion_tokens':9}))
                    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush();return
                model={'QwenProfile':'qwen2.5:7b-instruct','Qwen35Profile':'Qwen/Qwen3.5-4B','GLMFlash':'GLM-4.7-Flash-Q4_K_M','DevstralProfile':'mistralai/Devstral-Small-2-24B-Instruct-2512','QwenCoderProfile':'Qwen3-Coder-Next-Q5_K_M','KolibriProfile':'Kolibri-1-Q5_K_M','KolibriGeneralProfile':'Kolibri-1-Q5_K_M','Qwen38General':'Qwen3.8-27B','GPTOSSProfile':'openai/gpt-oss-120b'}.get(name,'fixture-model')
                send(dict(id='server-'+name,model=model,choices=[dict(delta={'reasoning_content':'fixture reasoning'})]))
                send(dict(model=model,choices=[dict(delta={'content':'Result '+name},finish_reason='stop')],usage={'completion_tokens':3}))
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
    for _ in range(2):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);servers.append(server)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start();threads.append(t)
    yield [f'http://127.0.0.1:{s.server_port}' for s in servers],calls
    for s in servers:s.shutdown();s.server_close()


def prepared(tmp_path, endpoints, names, *, max_seconds=3, runtime_profile=None):
    repo=tmp_path/'source';repo.mkdir();(repo/'fixture.txt').write_text('Immutable source fixture')
    for argv in [['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture']]:
        subprocess.run(['git','-C',str(repo),*argv],check=True)
    rev=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    p=tmp_path/'input.txt';p.write_text('Source fact: this is a synthetic fixture, not research.')
    c=Coordinator(tmp_path/'queue.db');c.configure(rate=0,start=time.time()-1,ceiling=1,margin=0,deadline=time.time()+30)
    assignments=[]
    for name,lane,deps in names:
        spec=task(name,deps)
        if (runtime_profile or {}).get('provider') in {'llamacpp-kolibri1-q5km-general','vllm-qwen38-27b-bf16-general','vllm-gptoss120b-mxfp4-low'}:spec['model_settings']['max_tokens']=4096
        c.create_operator_task(spec)
        assignments.append(dict(task_id=name,agent_id=f'lane{lane}',session_id='synthetic-session',task_hash=c._spec_hash(spec),
            runtime=dict(endpoint=endpoints[lane],model=(runtime_profile or {}).get('model','fixture-model'),provider=(runtime_profile or {}).get('provider','synthetic-acceptance'),configuration_version=('glm47-q4km-reasoning512-output1536-v1' if (runtime_profile or {}).get('provider')=='llamacpp-glm47-flash-q4km' else {'vllm-devstral-small2-fp8':'devstral-fp8-16k-output2048-v1','llamacpp-qwen3-coder-next-q5km':'qwen-coder-next-q5km-16k-output2048-v1','llamacpp-kolibri1-q5km':'kolibri1-q5km-low-16k-output2048-v1','llamacpp-kolibri1-q5km-general':'kolibri1-q5km-low-16k-output4096-general-v1','vllm-qwen38-27b-bf16-general':'qwen38-27b-bf16-low-16k-output4096-general-v1','vllm-gptoss120b-mxfp4-low':'gptoss120b-mxfp4-low-16k-output4096-v1'}.get((runtime_profile or {}).get('provider'),'fixture-v1')),provenance='two synthetic loopback acceptance endpoints'),
            source_repo=str(repo),source_commit=rev,input_path=str(p),input_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            context_version='fixture-context-v1',configuration_version='fixture-configuration-v1',purpose=spec['purpose'],expires_at=time.time()+25,
            max_seconds=max_seconds,acceptance_responsibility='Separate fixture evaluator; no product or research acceptance'))
    batch=dict(id='fixture',authorization='Operator authorizes bounded local synthetic acceptance only',assignments=assignments)
    c.load_prepared_batch(batch);c.control('running');return c,batch


def wait(predicate,seconds=8):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Acceptance condition timed out')


def states(c):return {x['id']:x['state'] for x in c.snapshot()['tasks']}


def test_checkpoint_delay_does_not_submit_with_expired_worker_heartbeat(tmp_path,endpoints,monkeypatch):
    """Model elapsed checkpoint work by aging its lease, without sleeping31s."""
    urls,calls=endpoints
    c,_=prepared(tmp_path,urls,[('LeaseFirst',0,()),('LeaseNext',0,())])
    original=Coordinator.execution_checkpoint
    aged=[]
    def delayed(self,directory):
        receipt=original(self,directory)
        if not aged:
            with self.db() as db:
                db.execute('UPDATE worker_process SET heartbeat=? WHERE id=1',(self.clock()-31,))
            aged.append(True)
        return receipt
    monkeypatch.setattr(Coordinator,'execution_checkpoint',delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=run_loop(str(c.path),lambda *args:pool.submit(_execute,*args),poll_seconds=.01,max_active=1)
    assert result['reason'].startswith('queue exhausted')
    assert [x['name'] for x in calls]==['LeaseFirst','LeaseNext']
    assert states(c)=={'LeaseFirst':'completed','LeaseNext':'completed'}
    assert all(x['attempts']==1 for x in c.snapshot()['tasks'])
    assert c.worker_status()['state']=='absent'


def test_checkpoint_boundary_does_not_steal_changed_worker_ownership(tmp_path,endpoints,monkeypatch):
    urls,calls=endpoints
    c,_=prepared(tmp_path,urls,[('LeaseFirst',0,()),('LeaseNext',0,())])
    original=Coordinator.execution_checkpoint
    changed=[]
    def replaced(self,directory):
        receipt=original(self,directory)
        if not changed:
            with self.db() as db:
                db.execute("UPDATE worker_process SET token='different-owner' WHERE id=1")
            changed.append(True)
        return receipt
    monkeypatch.setattr(Coordinator,'execution_checkpoint',replaced)
    with ThreadPoolExecutor(max_workers=2) as pool:
        with pytest.raises(RuntimeError,match='Worker ownership lost'):
            run_loop(str(c.path),lambda *args:pool.submit(_execute,*args),poll_seconds=.01,max_active=1)
    assert [x['name'] for x in calls]==['LeaseFirst']
    assert c.worker_status()['token']=='different-owner'
    assert next(x for x in c.snapshot()['tasks'] if x['id']=='LeaseNext')['attempts']==0


def test_two_lanes_automatic_successors_and_independent_dependency(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('A1',0,()),('B1',1,()),('A2',0,()),('B2',1,()),('Dependent',0,('A1',))])
    with ThreadPoolExecutor(max_workers=2) as pool,ThreadPoolExecutor(max_workers=1) as supervisor:
        future=supervisor.submit(run_loop,str(c.path),lambda *x:pool.submit(_execute,*x),poll_seconds=.02)
        wait(lambda:len(calls)==4 and all(states(c)[x]=='completed' for x in ('A1','A2','B1','B2')))
        assert states(c)['Dependent']=='queued'
        assert abs(calls[0]['at']-calls[1]['at'])<.2
        assert c.execution_status()['awaiting_review']==4
        c.verify('A1','separate fixture evaluator','Checked literal fixture result and request identity, not real research')
        assert future.result(8)['reason'].startswith('queue exhausted')
    assert len(calls)==5 and states(c)['Dependent']=='completed'
    assert all(x['attempts']==1 for x in c.snapshot()['tasks'])
    for x in calls:
        assert x['payload']['reasoning_effort']=='low'
        assert x['payload']['chat_template_kwargs']=={'reasoning_effort':'low'}
    response=json.loads(c.snapshot()['tasks'][1]['response'])
    assert response['model']=='fixture-model' and response['reasoning_content']=='fixture reasoning'
    assert c.worker_status()['state']=='absent'


def test_lost_ack_blocks_lane_without_replay_other_lane_progresses(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('Lost1',0,()),('A2',0,()),('B1',1,()),('B2',1,())])
    with ThreadPoolExecutor(max_workers=2) as pool,ThreadPoolExecutor(max_workers=1) as supervisor:
        future=supervisor.submit(run_loop,str(c.path),lambda *x:pool.submit(_execute,*x),poll_seconds=.02)
        wait(lambda:states(c)['B2']=='completed')
        assert states(c)['Lost1']=='unresolved' and states(c)['A2']=='queued'
        c.control('stopped');future.result(5)
    assert len(calls)==3 and c.snapshot()['tasks'][0]['attempts']==1
    fresh=Coordinator(c.path);assert fresh.execution_status()['queue_depth']==1
    with fresh.db() as db:
        row=db.execute("SELECT * FROM execution_assignments WHERE task_id='A2'").fetchone()
        assert 'uncertain' in fresh.assignment_gate(db,row,task('A2'))


def test_clean_exit_releases_lease_for_immediate_successor_flow(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('A1',0,()),('B1',1,())])
    submissions=[]
    def run():
        with ThreadPoolExecutor(max_workers=2) as pool:
            def submit(*args):
                submissions.append(args[3]);return pool.submit(_execute,*args)
            return run_loop(str(c.path),submit,poll_seconds=.02)
    run()
    assert c.snapshot()['control']['owner'] is None
    # Reuse the authorized fixture's identities/configuration for a fresh task.
    spec=task('A2');c.create_operator_task(spec)
    assignment=dict(b['assignments'][0],task_id='A2',task_hash=c._spec_hash(spec),purpose=spec['purpose'])
    c.load_prepared_batch(dict(id='successor',authorization=b['authorization'],assignments=[assignment]))
    started=time.monotonic();run()
    assert time.monotonic()-started<3
    assert sorted(submissions)==['A1','A2','B1']
    assert len(calls)==3 and all(t['attempts']==1 for t in c.snapshot()['tasks'])


def test_quiesce_pauses_without_discarding_queue_or_ownership(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('A1',0,()),('Dependent',0,('A1',))])
    with ThreadPoolExecutor(max_workers=2) as pool,ThreadPoolExecutor(max_workers=1) as supervisor:
        future=supervisor.submit(run_loop,str(c.path),lambda *x:pool.submit(_execute,*x),poll_seconds=.02,quiesce_on_pause=True)
        wait(lambda:states(c)['A1']=='completed')
        c.control('paused')
        assert future.result(5)['reason'].startswith('paused and quiescent')
    assert states(c)=={'A1':'completed','Dependent':'queued'}
    assert c.execution_status()['host']['host']==socket.gethostname()
    assert c.worker_status()['state']=='absent' and c.snapshot()['control']['owner'] is None
    assert len(calls)==1
    c.verify('A1','separate fixture evaluator','Literal fixture response and exact identity checked independently')
    c.control('running')
    with ThreadPoolExecutor(max_workers=2) as pool:
        run_loop(str(c.path),lambda *x:pool.submit(_execute,*x),poll_seconds=.02)
    assert states(c)['Dependent']=='completed' and len(calls)==2


def test_explicit_qwen_profile_and_retained_refusal(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('QwenProfile',0,()),('Refusal',1,())],runtime_profile=dict(provider='ollama-qwen2.5-7b',model='qwen2.5:7b-instruct'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        run_loop(str(c.path),lambda *x:pool.submit(_execute,*x),poll_seconds=.02)
    assert states(c)=={'QwenProfile':'completed','Refusal':'unresolved'}
    assert len(calls)==2
    assert all('reasoning_effort' not in x['payload'] and 'chat_template_kwargs' not in x['payload'] for x in calls)
    with c.db() as db:
        assert db.execute("SELECT COUNT(*) FROM execution_chunks WHERE kind='request_payload'").fetchone()[0]==2
        refusal=json.loads(db.execute("SELECT content FROM execution_chunks WHERE kind='runtime_refusal'").fetchone()[0])
        assert refusal['status']==400 and 'unsupported request profile' in refusal['body']
        assert '400' in db.execute("SELECT content FROM execution_chunks WHERE kind='diagnostic'").fetchone()[0]
    assert all(t['attempts']==1 for t in c.snapshot()['tasks'])


def test_qwen_profile_cannot_strip_glm_reasoning_settings(tmp_path,endpoints):
    with pytest.raises(ValueError,match='cannot substitute'):
        prepared(tmp_path,endpoints[0],[('A1',0,())],runtime_profile=dict(provider='ollama-qwen2.5-7b',model=MODEL))


def test_qwen35_profile_uses_supported_non_thinking_template(tmp_path,endpoints):
    urls,calls=endpoints
    c,_=prepared(tmp_path,urls,[('Qwen35Profile',0,())],runtime_profile=dict(provider='transformers-qwen3.5-4b',model='Qwen/Qwen3.5-4B'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        run_loop(str(c.path),lambda *x:pool.submit(_execute,*x),poll_seconds=.02)
    assert states(c)=={'Qwen35Profile':'completed'}
    assert len(calls)==1 and 'reasoning_effort' not in calls[0]['payload']
    assert calls[0]['payload']['chat_template_kwargs']=={'enable_thinking':False}
    assert calls[0]['payload']['max_tokens']==384


def test_qwen35_profile_refuses_model_substitution(tmp_path,endpoints):
    with pytest.raises(ValueError,match='cannot substitute'):
        prepared(tmp_path,endpoints[0],[('A1',0,())],runtime_profile=dict(provider='transformers-qwen3.5-4b',model=MODEL))


def test_explicit_new_allocation_keeps_ledger_and_prior_limits(tmp_path):
    c=Coordinator(tmp_path/'queue.db');now=time.time()
    c.configure(rate=0,start=now-100,ceiling=1,margin=0,deadline=now+10)
    args=dict(session_id='newallocation',authorization='Operator explicitly permits a distinct allocation, bounded to five dollars',rate=1.99,start=now-10,ceiling=5,margin=1,deadline=now+300)
    with pytest.raises(RuntimeError):c.authorize_execution_session(**args)
    with c.db() as db:db.execute('UPDATE control SET deadline=?',(now-1,))
    c.register_worker('test',os.getpid())
    with pytest.raises(RuntimeError):c.authorize_execution_session(**args)
    c.unregister_worker('test');c.authorize_execution_session(**args)
    with c.db() as db:
        row=db.execute('SELECT * FROM execution_session_authorizations').fetchone()
        assert json.loads(row['previous_control'])['ceiling']==1
        assert json.loads(row['new_control'])['ceiling']==5
    assert c.snapshot()['control']['mode']=='paused'
    with pytest.raises(RuntimeError):c.authorize_execution_session(**args)


@pytest.mark.parametrize('change,reason',[
    ('input','Source/input'),('source','Source revision'),('connection','Connection routing'),
    ('pause','Task paused'),('policy','Active review policy'),('expiry','expired'),('deadline','deadline reached'),('budget','budget reserve'),
])
def test_gates_prevent_network(tmp_path,endpoints,change,reason):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('A1',0,())]);a=b['assignments'][0]
    if change=='input':open(a['input_path'],'w').write('changed')
    if change=='source':open(a['source_repo']+'/fixture.txt','w').write('changed')
    if change=='connection':
        with c.db() as db:db.execute("UPDATE agent_registrations SET model='other'")
    if change=='pause':
        with c.db() as db:db.execute('UPDATE task_controls SET paused=1')
    if change=='policy':c.activate_review_policy(dict(permitted_actions=[],protected_targets=['filesystem','shell'],require_approval=True,spending_limit=1))
    if change=='expiry':c.clock=lambda:a['expires_at']+1
    if change=='deadline':
        with c.db() as db:db.execute('UPDATE control SET deadline=1')
    if change=='budget':
        with c.db() as db:db.execute('UPDATE control SET rate=3600,start=?',(time.time()-5,))
    status=c.execution_status();assert reason in status['assignments'][0]['blocker']
    c.register_worker('test',os.getpid());assert c.run_prepared('test','A1',status['assignments'][0]['hash']) is None
    assert not calls and c.snapshot()['tasks'][0]['attempts']==0


def test_restart_exclusive_owner_immutable_batch_and_restore(tmp_path,endpoints):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('A1',0,()),('B1',1,())])
    c.claim_execution_host();c.load_prepared_batch(b)
    changed=json.loads(json.dumps(b));changed['assignments'][0]['purpose']='substituted'
    with pytest.raises(ValueError):c.load_prepared_batch(changed)
    c.register_worker('first',os.getpid())
    with pytest.raises(RuntimeError):Coordinator(c.path).register_worker('second',os.getpid())
    a=c.execution_status()['assignments'][0];c.claim('first',task_id='A1',assignment_hash=a['hash'])
    with c.db() as db:db.execute('UPDATE control SET heartbeat=0');db.execute('DELETE FROM worker_process')
    fresh=Coordinator(c.path);fresh.recover_stale();fresh.register_worker('new',os.getpid())
    b1=fresh.execution_status()['assignments'][1];fresh.run_prepared('new','B1',b1['hash'])
    fresh.unregister_worker('new')
    assert states(c)=={'A1':'unresolved','B1':'completed'} and len(calls)==1
    receipt=fresh.execution_checkpoint(tmp_path/'checkpoint');dbfile=tmp_path/'checkpoint/coordinator.db'
    assert hashlib.sha256(dbfile.read_bytes()).hexdigest()==receipt['sha256']
    restored=Coordinator(dbfile);assert states(restored)==states(c)
    with restored.db() as db:assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    with c.db() as db:db.execute("UPDATE execution_host SET host='another-host'")
    with pytest.raises(RuntimeError):c.claim_execution_host()


def test_overall_deadline_includes_slow_trickle(tmp_path,endpoints):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('Trickle1',0,())],max_seconds=1)
    c.register_worker('test',os.getpid());a=c.execution_status()['assignments'][0];start=time.monotonic()
    c.run_prepared('test','Trickle1',a['hash'])
    assert time.monotonic()-start<2.5 and states(c)['Trickle1']=='unresolved' and len(calls)==1


def test_handover_requires_quiescence_and_unknown_stale_ownership(tmp_path,endpoints):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('A1',0,())]);c.claim_execution_host()
    with pytest.raises(RuntimeError):c.release_execution_host('destination.local','Approved synthetic handover with an immutable checkpoint')
    c.register_worker('test',os.getpid());a=c.execution_status()['assignments'][0]
    c.claim('test',task_id='A1',assignment_hash=a['hash'])
    with c.db() as db:db.execute('UPDATE worker_process SET heartbeat=0')
    assert c.execution_status()['idle_seconds'] is None
    c.control('paused');c.unregister_worker('test')
    with pytest.raises(RuntimeError):c.release_execution_host('destination.local','Approved synthetic handover with an immutable checkpoint')
    c.reconcile('A1','failed','Synthetic proof: reservation never reached network; no request was sent')
    c.release_execution_host('destination.local','Approved synthetic handover with an immutable checkpoint')
    assert c.execution_status()['host']['host']=='destination.local'
    with pytest.raises(RuntimeError):c.claim_execution_host()


def test_ui_observes_configured_queue_without_dispatch(tmp_path,endpoints,monkeypatch):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('A1',0,())])
    monkeypatch.setenv('NODE_UI_COORDINATOR_DB',str(c.path))
    from swarmtracker_node_ui.sessions import configured_execution_view
    from jinja2 import Environment,FileSystemLoader,select_autoescape
    from pathlib import Path
    view=configured_execution_view();assert view['queue_depth']==1 and view['ready']==1
    template=Path(__file__).resolve().parents[1]/'apps/node/swarmtracker_node_ui/templates'
    text=Environment(loader=FileSystemLoader(template),autoescape=select_autoescape()).get_template('_prepared_execution.html').render(prepared_execution=view)
    assert 'GPU activity unknown' in text and 'Independent acceptance' not in text and 'fixture-model' in text
    assert not calls and c.snapshot()['tasks'][0]['attempts']==0


def test_new_session_preserves_exact_uncertain_set_without_replay(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('LostAck',0,()),('Held',0,())])
    owner='preserve-test';c.register_worker(owner,os.getpid())
    c.control('running');c.run_prepared(owner,'LostAck',b['assignments'][0]['hash'] if 'hash' in b['assignments'][0] else digest(b['assignments'][0]))
    c.unregister_worker(owner);c.control('stopped')
    with c.db() as db:
        db.execute('UPDATE control SET deadline=?',(time.time()-1,))
        before=dict(db.execute("SELECT * FROM tasks WHERE id='LostAck'").fetchone())
    args=dict(session_id='renewal',authorization='Operator permits new allocation preserving lost acknowledgement without any replay',rate=1,start=time.time()-1,ceiling=15,margin=2,deadline=time.time()+300)
    with pytest.raises(RuntimeError):c.authorize_execution_session(**args)
    with pytest.raises(RuntimeError):c.authorize_execution_session(**args,preserve_prior_tasks=['Held'])
    c.authorize_execution_session(**args,preserve_prior_tasks=['LostAck','Held'])
    with c.db() as db:
        assert dict(db.execute("SELECT * FROM tasks WHERE id='LostAck'").fetchone())==before
        assert db.execute('SELECT count(*) FROM execution_preserved_tasks').fetchone()[0]==2
        assert db.execute("SELECT paused,authorization_hash FROM task_controls WHERE task_id='Held'").fetchone()[:]==(1,None)
    c.control('running')
    assert not any(a['ready'] for a in c.execution_status()['assignments'])
    assert len(calls)==1


@pytest.mark.parametrize('profile,thinking', [('qwen35-bf16-thinking-1536-v1',True),('qwen35-bf16-no-thinking-1536-v1',False)])
def test_qwen_matched_profiles_have_explicit_template_and_equal_cap(tmp_path,endpoints,profile,thinking):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('Qwen35Profile',0,())],runtime_profile=dict(provider='transformers-qwen3.5-4b',model='Qwen/Qwen3.5-4B'))
    # Bind a fresh immutable configuration before loading, using a separate fixture.
    with c.db() as db:
        a=b['assignments'][0];a['runtime']['configuration_version']=profile
        db.execute('UPDATE execution_assignments SET hash=?,spec=? WHERE task_id=?',(digest(a),json.dumps(a,sort_keys=True),a['task_id']))
        db.execute('UPDATE agent_registrations SET provenance=? WHERE id=?',(json.dumps(a['runtime'],sort_keys=True),a['agent_id']))
    owner='profile-test';c.register_worker(owner,os.getpid());c.control('running')
    c.run_prepared(owner,'Qwen35Profile',digest(a));c.unregister_worker(owner)
    assert calls[0]['payload']['chat_template_kwargs']=={'enable_thinking':thinking}
    assert calls[0]['payload']['max_tokens']==1536
    assert 'reasoning_effort' not in calls[0]['payload']


def test_glm47_llamacpp_has_model_specific_wire_contract(tmp_path,endpoints):
    urls,calls=endpoints
    c,b=prepared(tmp_path,urls,[('GLMFlash',0,())],runtime_profile=dict(provider='llamacpp-glm47-flash-q4km',model='GLM-4.7-Flash-Q4_K_M'))
    owner='glm-test';c.register_worker(owner,os.getpid());c.control('running')
    c.run_prepared(owner,'GLMFlash',digest(b['assignments'][0]));c.unregister_worker(owner)
    payload=calls[0]['payload']
    assert 'reasoning_effort' not in payload and 'chat_template_kwargs' not in payload
    assert payload['max_tokens']==1536 and payload['min_p']==0.01 and payload['repeat_penalty']==1.0
    assert c.snapshot()['tasks'][0]['state']=='completed'


@pytest.mark.parametrize('mode,expected',[('stopped',None),('paused',10),('running',10)])
def test_stopped_session_does_not_report_allocation_idle_seconds(tmp_path,endpoints,mode,expected):
    c,b=prepared(tmp_path,endpoints[0],[('Finished',0,())])
    now=time.time()
    c.clock=lambda:now
    with c.db() as db:
        db.execute("UPDATE tasks SET state='completed' WHERE id='Finished'")
        db.execute("UPDATE execution_assignments SET last_progress=?,ended=? WHERE task_id='Finished'",(now-10,now-10))
        db.execute('UPDATE control SET mode=?',(mode,))
    assert c.execution_status()['worker']['state']=='absent'
    assert c.execution_status()['idle_seconds']==expected


@pytest.mark.parametrize('name,provider,model', [('DevstralProfile','vllm-devstral-small2-fp8','mistralai/Devstral-Small-2-24B-Instruct-2512'),('QwenCoderProfile','llamacpp-qwen3-coder-next-q5km','Qwen3-Coder-Next-Q5_K_M'),('KolibriProfile','llamacpp-kolibri1-q5km','Kolibri-1-Q5_K_M'),('KolibriGeneralProfile','llamacpp-kolibri1-q5km-general','Kolibri-1-Q5_K_M')])
def test_coding_profiles_do_not_send_foreign_reasoning_controls(tmp_path,endpoints,name,provider,model):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[(name,0,())],runtime_profile=dict(provider=provider,model=model))
    owner='coding-test';c.register_worker(owner,os.getpid());c.run_prepared(owner,name,digest(b['assignments'][0]));c.unregister_worker(owner)
    payload=calls[0]['payload']
    assert payload['model']==model and payload['max_tokens']==(4096 if provider.endswith('-general') else 2048)
    assert 'reasoning_effort' not in payload and 'chat_template_kwargs' not in payload
    assert c.snapshot()['tasks'][0]['state']=='completed'
    if provider.startswith('vllm'):assert payload['temperature']==0.15 and 'top_k' not in payload
    else:assert payload['top_k']==40 and payload['min_p']==0


def test_explicit_terminated_allocation_closeout_retains_old_budget_and_lost_attempt(tmp_path):
    c=Coordinator(tmp_path/'queue.db',clock=lambda:1000);c.configure(rate=3.29,start=900,ceiling=15,margin=2,deadline=2000)
    c.create_operator_task(task('Lost'));c.create_operator_task(task('Held'))
    with c.db() as db:db.execute("UPDATE tasks SET state='unresolved',request_id='lost-id',attempts=1 WHERE id='Lost'")
    c.close_execution_allocation('Operator reports previous provider allocation manually terminated')
    with c.db() as db:
        old=json.loads(db.execute('SELECT previous_control FROM execution_allocation_closures').fetchone()[0]);assert old['deadline']==2000 and old['start']==900 and old['ceiling']==15
    c.authorize_execution_session(session_id='new',authorization='Distinct new allocation authorized by operator with cumulativeUSD15',rate=3.29,start=999,ceiling=15,margin=2,deadline=2000,preserve_prior_tasks=['Lost','Held'])
    with c.db() as db:assert tuple(db.execute("SELECT state,request_id,attempts FROM tasks WHERE id='Lost'").fetchone())==('unresolved','lost-id',1)


@pytest.mark.parametrize('mutation',[None,'start','rate','ceiling','deadline','expired','inflight'])
def test_same_allocation_explicit_resume_preserves_budget_and_uncertainty(tmp_path,mutation):
    c=Coordinator(tmp_path/'queue.db',clock=lambda:1000)
    c.configure(rate=3.29,start=900,ceiling=15,margin=2,deadline=1001)
    c.clock=lambda:1002
    c.authorize_execution_session(session_id='existing',authorization='Operator authorized this allocation including its complete prior spending',rate=3.29,start=1000,ceiling=15,margin=2,deadline=2000)
    c.create_operator_task(task('Lost'));c.create_operator_task(task('Held',('Lost',)))
    with c.db() as db:
        db.execute("UPDATE tasks SET state='unresolved',request_id='lost-id',attempts=1 WHERE id='Lost'")
    c.control('stopped')
    if mutation in {'start','rate','ceiling','deadline'}:
        with c.db() as db:db.execute('UPDATE control SET '+mutation+'='+mutation+'+1')
    if mutation=='expired':c.clock=lambda:2001
    if mutation=='inflight':
        with c.db() as db:db.execute("UPDATE tasks SET state='submitted' WHERE id='Held'")
    if mutation:
        with pytest.raises(RuntimeError):c.resume_execution_allocation('existing','Operator explicitly continues this same allocation without new budget')
        assert c.snapshot()['control']['mode']=='stopped'
    else:
        before=c.snapshot()['control'];c.resume_execution_allocation('existing','Operator explicitly continues this same allocation without new budget')
        after=c.snapshot()['control'];assert after==dict(before,mode='paused')
        with c.db() as db:
            assert tuple(db.execute("SELECT state,request_id,attempts FROM tasks WHERE id='Lost'").fetchone())==('unresolved','lost-id',1)
            assert db.execute('SELECT COUNT(*) FROM execution_allocation_continuations').fetchone()[0]==1


def test_qwen38_uses_its_verified_low_template_contract(tmp_path,endpoints):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('Qwen38General',0,())],runtime_profile=dict(provider='vllm-qwen38-27b-bf16-general',model='Qwen3.8-27B'))
    owner='qwen38-test';c.register_worker(owner,os.getpid());c.run_prepared(owner,'Qwen38General',digest(b['assignments'][0]));c.unregister_worker(owner)
    payload=calls[0]['payload'];assert payload['model']=='Qwen3.8-27B' and payload['max_tokens']==4096
    assert 'reasoning_effort' not in payload and payload['chat_template_kwargs']==dict(reasoning_effort='low',enable_thinking=True,preserve_thinking=False)
    assert payload['temperature']==1.0 and payload['top_k']==20 and payload['repetition_penalty']==1.0
    assert c.snapshot()['tasks'][0]['state']=='completed'


def test_gptoss_native_low_wire_and_binding(tmp_path,endpoints):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('GPTOSSProfile',0,())],runtime_profile=dict(provider='vllm-gptoss120b-mxfp4-low',model='openai/gpt-oss-120b'))
    owner='gptoss-test';c.register_worker(owner,os.getpid());c.run_prepared(owner,'GPTOSSProfile',digest(b['assignments'][0]));c.unregister_worker(owner)
    p=calls[0]['payload'];assert p['model']=='openai/gpt-oss-120b' and p['reasoning_effort']=='low' and p['max_tokens']==4096
    assert 'chat_template_kwargs' not in p and 'top_k' not in p and p['stream_options']['include_usage']
    assert c.snapshot()['tasks'][0]['state']=='completed'
    (tmp_path/'wrong').mkdir();bad=dict(b['assignments'][0]['runtime'],model='other-model')
    with pytest.raises(ValueError,match='cannot substitute'):prepared(tmp_path/'wrong',urls,[('Other',0,())],runtime_profile=bad)


def test_explicit_ceiling_amendment_keeps_same_start_and_uncertainty(tmp_path):
    c=Coordinator(tmp_path/'queue.db',clock=lambda:1000);c.configure(rate=3.29,start=900,ceiling=15,margin=2,deadline=1001);c.clock=lambda:1002
    c.authorize_execution_session(session_id='existing',authorization='Operator authorized this same allocation with cumulative ceiling15',rate=3.29,start=1000,ceiling=15,margin=2,deadline=2000)
    c.create_operator_task(task('Lost'));c.create_operator_task(task('Held',('Lost',)))
    with c.db() as db:db.execute("UPDATE tasks SET state='unresolved',request_id='lost-id',attempts=1 WHERE id='Lost'")
    c.control('stopped');before=c.snapshot()['control']
    c.amend_execution_allocation('existing','Operator explicitly authorizes bounded experiment cumulative ceiling17',expected_ceiling=15,ceiling=17)
    after=c.snapshot()['control'];assert after['start']==before['start'] and after['rate']==before['rate'] and after['mode']=='stopped' and after['margin']==2
    assert after['ceiling']==17 and after['deadline']==pytest.approx(2000+7200/3.29)
    with c.db() as db:assert tuple(db.execute("SELECT state,request_id,attempts FROM tasks WHERE id='Lost'").fetchone())==('unresolved','lost-id',1)
    with pytest.raises(RuntimeError):c.amend_execution_allocation('existing','Repeated old ceiling authority must be rejected',expected_ceiling=15,ceiling=17)
    c.resume_execution_allocation('existing','Operator continues authorized bounded experiment on same allocation');assert c.snapshot()['control']['mode']=='paused'


def test_native_sse_call_retained_without_automatic_acceptance(tmp_path,endpoints):
    urls,calls=endpoints;c,b=prepared(tmp_path,urls,[('NativeToolStream',0,())]);owner='native-test';c.register_worker(owner,os.getpid());c.run_prepared(owner,'NativeToolStream',digest(b['assignments'][0]));c.unregister_worker(owner)
    row=c.snapshot()['tasks'][0];assert row['state']=='completed' and row['finish_reason']=='tool_calls' and not row['verifier']
    response=json.loads(row['response']);assert response['tool_calls'][0]['function']['arguments']=='{"fixture_id":"manual"}' and response['response_kind']=='native_tool_call'
    with c.db() as db:assert db.execute("SELECT COUNT(*) FROM execution_chunks WHERE kind='tool_call_delta'").fetchone()[0]==2
