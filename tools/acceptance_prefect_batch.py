"""Disposable self-hosted Prefect/process-worker acceptance, synthetic endpoints.

Requires the pinned optional environment and the owned loopback server. No paid
inference, code application, remote service, or infrastructure provisioning.
The fixture evaluator accepts literal fixture strings only, never model findings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'apps/node')]
from swarmtracker_node_ui.session_coordinator import Coordinator,MODEL


def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(path)


def fixture(out):
    lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            name=body['messages'][1]['content'].splitlines()[0]
            rid=self.headers['X-Request-ID']
            assert body['reasoning_effort']=='low' and body['chat_template_kwargs']=={'reasoning_effort':'low'}
            with lock,(out/'endpoint-calls.jsonl').open('a') as f:
                f.write(json.dumps(dict(at=time.time(),name=name,request_id=rid,port=self.server.server_port,payload=body))+'\n')
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
            try:
                if name=='LostAcknowledgement':time.sleep(2);return
                if name=='CrashReservation':time.sleep(45)
                else:time.sleep(3)
                for delta,finish in [({'reasoning_content':'synthetic fixture reasoning'},None),({'content':'Fixture result '+name},'stop')]:
                    self.wfile.write(('data: '+json.dumps(dict(id='fixture-'+rid,model='synthetic-model',choices=[dict(delta=delta,finish_reason=finish)],usage={'completion_tokens':4}))+'\n\n').encode());self.wfile.flush()
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
    servers=[ThreadingHTTPServer(('127.0.0.1',0),Handler) for _ in range(2)]
    save(out/'endpoints.json',[f'http://127.0.0.1:{s.server_port}' for s in servers])
    for s in servers:threading.Thread(target=s.serve_forever,daemon=True).start()
    while True:time.sleep(1)


def contract(name,deps=()):
    return dict(id=name,purpose=name,dependencies=list(deps),authorization='explicit-read-only-glm-review',
        acceptance_criteria='Return the literal synthetic fixture result; independent adjudication remains required',
        model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=2048),
        expected_outputs='Fixture result '+name,validation_gates='Separate deterministic synthetic evaluator; no source/research acceptance')


def prepare(c,out,urls,names,batch_id):
    packet=out/'fixture-input.txt'
    if not packet.exists():packet.write_text('Synthetic scheduling fixture. No customer data, code executor or acceptance authority.')
    rev=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    assignments=[]
    for name,lane,deps in names:
        spec=contract(name,deps);c.create_operator_task(spec)
        assignments.append(dict(task_id=name,agent_id='fixture-lane'+str(lane),session_id='prefect-acceptance',task_hash=c._spec_hash(spec),
            runtime=dict(endpoint=urls[lane],model='synthetic-model',provider='synthetic-acceptance',configuration_version='fixture-v1',provenance='Disposable loopback acceptance fixture; no real inference'),
            source_repo=str(ROOT),source_commit=rev,input_path=str(packet),input_sha256=hashlib.sha256(packet.read_bytes()).hexdigest(),
            context_version='fixture-v1',configuration_version='fixture-v1',purpose=name,expires_at=time.time()+540,max_seconds=60,
            acceptance_responsibility='Separate deterministic synthetic evaluator; no model self-acceptance'))
    batch=dict(id=batch_id,authorization='Operator authorizes this bounded synthetic local integration acceptance',assignments=assignments)
    save(out/(batch_id+'.json'),batch);c.load_prepared_batch(batch)


def wait(predicate,seconds=90):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.2)
    raise TimeoutError('Bounded Prefect acceptance wait expired')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['fixture','accept']);p.add_argument('--output',required=True)
    args=p.parse_args();out=Path(args.output).resolve()
    if args.action=='fixture':fixture(out);return
    out.mkdir(parents=True,exist_ok=False)
    if os.environ.get('PREFECT_API_URL')!='http://127.0.0.1:4200/api':raise ValueError('Owned self-hosted API required')
    env=dict(os.environ,VERITY_PREFECT_DEPLOYMENT='1',DO_NOT_TRACK='1',PREFECT_SERVER_ANALYTICS_ENABLED='false')
    log=(out/'fixture.log').open('ab')
    fixture_process=subprocess.Popen([sys.executable,__file__,'fixture','--output',str(out)],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    worker=None;events=[]
    def event(name,**data):
        events.append(dict(at=time.time(),event=name,**data));save(out/'events.json',events)
    def calls():return [json.loads(x) for x in (out/'endpoint-calls.jsonl').read_text().splitlines()] if (out/'endpoint-calls.jsonl').exists() else []
    try:
        wait(lambda:(out/'endpoints.json').exists(),10);urls=json.loads((out/'endpoints.json').read_text())
        c=Coordinator(out/'coordinator.db');c.configure(rate=0,start=time.time()-1,ceiling=1,margin=0,deadline=time.time()+600)
        prepare(c,out,urls,[(x+str(i),lane,()) for i in range(1,4) for lane,x in enumerate(('A','B'))]+[('Dependent',0,('A1',))],'prepared')
        c.control('running')
        from prefect.client.orchestration import get_client
        from prefect.client.schemas.actions import WorkPoolCreate
        from prefect.exceptions import ObjectNotFound
        from prefect import flow
        from prefect.deployments import run_deployment
        pool='verity-local-process'
        with get_client(sync_client=True) as client:
            try:work_pool=client.read_work_pool(pool)
            except ObjectNotFound:work_pool=client.create_work_pool(WorkPoolCreate(name=pool,type='process'))
            if work_pool.type!='process':raise RuntimeError('Existing pool is not the authorized local process pool')
        f=flow.from_source(source=str(ROOT),entrypoint='tools/prefect_session.py:prefect_entrypoint')
        deployment=f.deploy(name='local-acceptance-'+out.name,work_pool_name=pool,build=False,push=False,concurrency_limit=1,
            job_variables={'env':{'VERITY_PREFECT_DEPLOYMENT':'1','DO_NOT_TRACK':'1','PREFECT_SERVER_ANALYTICS_ENABLED':'false'}},parameters={'db_path':str(c.path),'poll_seconds':.2},print_next_steps=False)
        def launch_worker():
            wl=(out/'process-worker.log').open('ab')
            child=subprocess.Popen([str(Path(sys.executable).with_name('prefect')),'worker','start','--pool',pool,'--type','process','--limit','1'],env=env,stdin=subprocess.DEVNULL,stdout=wl,stderr=wl,start_new_session=True)
            event('process_worker_started',pid=child.pid);return child
        worker=launch_worker()
        run=run_deployment(deployment,timeout=0,idempotency_key=uuid.uuid4().hex,as_subflow=False)
        event('deployment_submitted',deployment_id=str(deployment),flow_run_id=str(run.id),fixture_pid=fixture_process.pid,source_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip())
        def states():return {x['id']:x['state'] for x in c.snapshot()['tasks']}
        wait(lambda:all(states().get(x+str(i))=='completed' for i in range(1,4) for x in ('A','B')))
        assert states()['Dependent']=='queued' and len(calls())==6
        event('independent_successors_and_dependency_hold',status=c.execution_status())
        for row in c.snapshot()['tasks']:
            if row['id']!='Dependent':assert json.loads(row['response'])['content']=='Fixture result '+row['id']
        c.verify('A1','separate synthetic evaluator','Literal fixture result, request identity and endpoint log checked; not real model/research acceptance')
        wait(lambda:states()['Dependent']=='completed' and c.worker_status()['state']=='absent')
        event('dependency_released_only_after_adjudication',status=c.execution_status())
        worker.terminate();worker.wait(15);event('owned_process_worker_stopped',pid=worker.pid)
        prepare(c,out,urls,[('LostAcknowledgement',0,()),('IndependentAfterRestart',1,()),('HeldAfterUncertain',0,())],'restart')
        worker=launch_worker();run2=run_deployment(deployment,timeout=0,idempotency_key=uuid.uuid4().hex,as_subflow=False)
        wait(lambda:states().get('LostAcknowledgement')=='unresolved' and states().get('IndependentAfterRestart')=='completed')
        assert states()['HeldAfterUncertain']=='queued'
        c.control('paused');event('lost_ack_and_worker_restart',flow_run_id=str(run2.id),status=c.execution_status())
        owned=c.worker_status();assert owned['state']=='active'
        os.kill(owned['pid'],signal.SIGKILL);event('owned_coordinator_process_killed',pid=owned['pid'])
        wait(lambda:c.worker_status()['state']=='stale')
        # No active HTTP request at this kill. Earlier in-flight crash acceptance
        # remains a separate core test; do not inflate this process proof.
        c.control('running');run3=run_deployment(deployment,timeout=0,idempotency_key=uuid.uuid4().hex,as_subflow=False)
        wait(lambda:c.worker_status()['state']=='active' and c.worker_status()['pid']!=owned['pid'])
        time.sleep(2)
        assert sum(x['name']=='LostAcknowledgement' for x in calls())==1
        c.control('stopped');wait(lambda:c.worker_status()['state']=='absent')
        assert all(x['attempts']<=1 for x in c.snapshot()['tasks'])
        receipt=c.execution_checkpoint(out/'final-checkpoint')
        save(out/'final-snapshot.json',c.snapshot());save(out/'receipt.json',dict(passed=True,scope='Actual self-hosted Prefect process worker; two synthetic endpoints; no real inference',requests=len(calls()),coordinator_checkpoint=receipt,screen_lock='pending separate actual test',chat_disconnect='launcher independence only; literal chat network loss not induced',inflight_process_kill='core fixture test only; this Prefect kill occurred while paused',automatic_code_changes=False))
        event('accepted_synthetic_process_path',requests=len(calls()));print((out/'receipt.json').read_text())
    finally:
        if worker and worker.poll() is None:worker.terminate()
        if fixture_process.poll() is None:fixture_process.terminate()


if __name__=='__main__':main()
