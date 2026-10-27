"""Optional self-hosted Prefect adapter for Verity's existing durable queue.

Prefect receives IDs, hashes and optional local operator-plan path only. Private
packets/results remain in Verity; Prefect has no acceptance or retry authority.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Optional

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'apps/node')]
from swarmtracker_node_ui.session_coordinator import Coordinator


def _execute(db_path, owner, instance_id, task_id, assignment_hash, tool_plan=None):
    c=Coordinator(db_path);c._instance_id=instance_id
    c.run_prepared(owner,task_id,assignment_hash)
    if tool_plan:
        from swarmtracker_node_ui.structured_tools import dispatch_retained
        try:dispatch_retained(c,task_id,tool_plan)
        except Exception as exc:
            with c.db() as db:
                db.execute("UPDATE tasks SET error=? WHERE id=?",("Native tool bridge refused or uncertain: "+type(exc).__name__,task_id))
                db.execute("INSERT INTO task_audit(task_id,at,action,detail) VALUES (?,?,?,?)",(task_id,c.clock(),"native_bridge_attention",type(exc).__name__+"; no automatic replay or acceptance"))
            raise
    with c.db() as db:
        r=db.execute('SELECT id,state,request_id FROM tasks WHERE id=?',(task_id,)).fetchone()
    return dict(r)  # No private source, response or reasoning in Prefect results.


def run_loop(db_path, submit, *, poll_seconds=1, stop_when_exhausted=True, quiesce_on_pause=False, tool_plan=None, max_active=2):
    """Prefect is the concurrency executor; Verity selects eligible assignments."""
    if type(max_active) is not int or max_active not in {1,2}:raise ValueError("One or two explicit lanes required")
    c=Coordinator(db_path);owner=uuid.uuid4().hex;c.claim_execution_host()
    c.register_worker(owner,os.getpid());active={}
    try:
        while True:
            c.worker_heartbeat(owner);c.recover_stale()
            for lane,(future,task_id) in list(active.items()):
                # PrefectConcurrentFuture exposes its wrapped executor future;
                # unlike concurrent.futures.Future it has no done() method.
                if getattr(future,'wrapped_future',future).done():
                    # Ledger is authoritative even if Prefect result retrieval failed.
                    try:future.result()
                    except Exception as exc:
                        with c.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            row=db.execute('SELECT state FROM tasks WHERE id=?',(task_id,)).fetchone()
                            if row and row['state'] in {'queued','submitted','acknowledged'}:
                                state='failed' if row['state']=='queued' else 'unresolved'
                                db.execute('UPDATE tasks SET error=? WHERE id=?',(type(exc).__name__,task_id))
                                c._transition(db,task_id,state,'Executor failure; no automatic retry; inspect persisted attempt and reconcile')
                    c.execution_checkpoint(Path(db_path).parent/'execution-checkpoints'/uuid.uuid4().hex)
                    del active[lane]
            status=c.execution_status();gate=c.dispatch_gate(status['control'])
            if gate=='paused' and not active and quiesce_on_pause:
                return {'reason':'paused and quiescent; ownership retained for explicit handover','status':status}
            if gate and gate!='paused' and not active:return {'reason':gate,'status':status}
            if not gate:
                for a in status['assignments']:
                    if len(active)<max_active and a['ready'] and a['agent_id'] not in active:
                        active[a['agent_id']]=(submit(db_path,owner,c._instance_id,a['task_id'],a['hash'],**({'tool_plan':tool_plan} if tool_plan else {})),a['task_id'])
            if not active and status['queue_depth']==0 and stop_when_exhausted:
                return {'reason':'queue exhausted; acceptance remains separate','status':status}
            time.sleep(poll_seconds)
    finally:
        # On a supervisor crash, its children may have submitted remotely. The
        # persisted attempts stay uncertain; nothing is requeued here.
        c.unregister_worker(owner)


def make_flow():
    # Never silently start an ephemeral server or fall back to Prefect Cloud.
    if os.environ.get('PREFECT_API_URL')!='http://127.0.0.1:4200/api':
        raise ValueError('Explicit self-hosted loopback Prefect API required')
    from prefect import flow,task
    from prefect.cache_policies import NO_CACHE
    from prefect.task_runners import ThreadPoolTaskRunner
    dispatch=task(name='verity-governed-attempt',retries=0,cache_policy=NO_CACHE,persist_result=False,log_prints=False)(_execute)
    @flow(name='verity-prepared-batch',retries=0,task_runner=ThreadPoolTaskRunner(max_workers=2),persist_result=False,log_prints=False)
    def prepared(db_path:str,poll_seconds:float=1,stop_when_exhausted:bool=True,quiesce_on_pause:bool=False,tool_plan:Optional[str]=None,max_active:int=1):
        return run_loop(db_path,dispatch.submit,poll_seconds=poll_seconds,stop_when_exhausted=stop_when_exhausted,quiesce_on_pause=quiesce_on_pause,tool_plan=tool_plan,max_active=max_active)
    return prepared


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['run','status','load','pause','resume','stop','checkpoint','deploy','handover']);p.add_argument('--db',required=True);p.add_argument('--file');p.add_argument('--target');p.add_argument('--tool-plan');p.add_argument('--max-active',type=int,default=1);p.add_argument('--quiesce-on-pause',action='store_true');a=p.parse_args();c=Coordinator(a.db)
    if a.action=='run':print(json.dumps(make_flow()(a.db,quiesce_on_pause=a.quiesce_on_pause,tool_plan=a.tool_plan,max_active=a.max_active),indent=2))
    elif a.action=='load':c.load_prepared_batch(json.loads(Path(a.file).read_text()))
    elif a.action in {'pause','resume','stop'}:c.control({'pause':'paused','resume':'running','stop':'stopped'}[a.action])
    elif a.action=='checkpoint':print(json.dumps(c.execution_checkpoint(a.file),indent=2))
    elif a.action=='handover':c.release_execution_host(a.target,Path(a.file).read_text())
    elif a.action=='deploy':
        from prefect import flow
        f=flow.from_source(source=str(Path(__file__).resolve().parents[1]),entrypoint='tools/prefect_session.py:prefect_entrypoint')
        print(f.deploy(name='verity-local',work_pool_name='verity-local-process',build=False,push=False,parameters={'db_path':str(Path(a.db).resolve()),'quiesce_on_pause':a.quiesce_on_pause}))
    else:print(json.dumps(c.execution_status(),indent=2))


# Only loaded by the optional execution environment; ordinary Verity does not
# import Prefect or silently start its temporary server.
if os.environ.get('VERITY_PREFECT_DEPLOYMENT')=='1':
    prefect_entrypoint=make_flow()

if __name__=='__main__':main()
