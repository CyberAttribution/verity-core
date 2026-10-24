"""Disposable offline lifecycle acceptance of the existing session worker.

Synthetic loopback protocol fixture, NOT GLM inference or literal Codex-network
outage acceptance. A launcher exits before prepared work progresses; an owned
worker is killed/restarted, and an uncertain request is never redispatched.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/node'))
from swarmtracker_node_ui.session_coordinator import Coordinator, MODEL


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    calls=[];release=threading.Event();workers=[];events=[]
    def event(kind,**kw):
        events.append(dict(at=time.time(),event=kind,**kw))
        (out/'events.json').write_text(json.dumps(events,indent=2))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            rid=self.headers['X-Request-ID'];text=data['messages'][1]['content'];task=text.splitlines()[0]
            assert data['reasoning_effort']=='low' and data['chat_template_kwargs']=={'reasoning_effort':'low'}
            calls.append(dict(task=task,request_id=rid,at=time.time()))
            (out/'endpoint-calls.json').write_text(json.dumps(calls,indent=2))
            if task=='Uncertain':release.wait(90)
            response=json.dumps(dict(id='synthetic-'+rid,model=MODEL,choices=[dict(finish_reason='stop',message={'content':'Synthetic fixture result for '+task})],usage={'completion_tokens':5})).encode()
            try:
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
            except (BrokenPipeError,ConnectionResetError):pass
    # Occupied port refuses naturally; no adoption or unrelated process kill.
    server=ThreadingHTTPServer(('127.0.0.1',18001),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    db=Coordinator(out/'coordinator.db');db.configure(rate=1,start=time.time()-1,ceiling=100,deadline=time.time()+120)
    def spec(name,deps=()):
        return dict(id=name,purpose=name,dependencies=list(deps),authorization='explicit-read-only-glm-review',acceptance_criteria='Synthetic fixture only',model_settings=dict(model=MODEL,reasoning_effort='low',chat_template_kwargs={'reasoning_effort':'low'},max_tokens=2048),expected_outputs='Bounded fixture result',validation_gates='Separate evaluator; no code execution')
    for name,deps in [('First',()),('Uncertain',()),('Independent',()),('Dependent',('First',))]:db.enqueue(spec(name,deps))
    db.control('running');env=dict(os.environ,NODE_UI_COORDINATOR_DB=str(db.path))
    def launch():
        command=[sys.executable,str(ROOT/'tools/session_worker.py'),'serve','--poll-seconds','0.1']
        # This creator process exits; its child owns no terminal/chat descriptors.
        code='import subprocess,sys; f=open(sys.argv[1],"ab"); p=subprocess.Popen(sys.argv[2:],stdin=subprocess.DEVNULL,stdout=f,stderr=f,start_new_session=True); print(p.pid)'
        pid=int(subprocess.check_output([sys.executable,'-c',code,str(out/'worker.log'),*command],env=env,text=True));workers.append(pid);event('launcher_exited',worker_pid=pid);return pid
    def until(predicate,seconds=12):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.1)
        raise AssertionError('Bounded acceptance wait expired')
    def states():return {x['id']:x['state']for x in db.snapshot()['tasks']}
    try:
        first=launch();until(lambda:any(x['task']=='Uncertain'for x in calls));assert states()['First']=='completed'
        event('prepared_work_progressed_without_launcher',states=states());os.kill(first,signal.SIGKILL);event('owned_worker_killed',pid=first);release.set()
        # Real lease aging, not a manually rewritten ledger timestamp.
        until(lambda:db.worker_status()['state']=='stale' and
              time.time()-(db.snapshot()['control']['heartbeat'] or 0)>=30,seconds=35)
        second=launch();until(lambda:states()['Independent']=='completed');assert states()['Uncertain']=='unresolved' and states()['Dependent']=='queued'
        assert [x['task']for x in calls]==['First','Uncertain','Independent']
        event('restart_and_dependency_hold',states=states())
        db.verify('First','separate-synthetic-evaluator','Checked fixture literal result and endpoint request identity; not source-code or research acceptance')
        until(lambda:states()['Dependent']=='completed');db.control('stopped');until(lambda:db.worker_status()['state']=='absent')
        assert [x['task']for x in calls]==['First','Uncertain','Independent','Dependent']
        snap=db.snapshot();assert [x['attempts']for x in snap['tasks']]==[1,1,1,1]
        (out/'final-snapshot.json').write_text(json.dumps(snap,indent=2))
        receipt=dict(passed=True,scope=__doc__.strip(),requests=len(calls),attempts=[x['attempts']for x in snap['tasks']],states=states(),literal_chat_network_disconnect='not induced; launcher exit and detached child proven',real_GLM_inference=False,automatic_code_changes=False)
        (out/'receipt.json').write_text(json.dumps(receipt,indent=2));event('accepted',**receipt);print(json.dumps(receipt))
    finally:
        release.set();server.shutdown();server.server_close()
        for pid in workers:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
    return 0

if __name__=='__main__':raise SystemExit(main())
