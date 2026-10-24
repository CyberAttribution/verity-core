"""Real disposable ADK Runner -> existing Verity request/report contracts.

Uses only an existing loopback Ollama model. No Gemini/LiteLLM/cloud calls,
framework tools, new credentials, or OS containment claim. Never run against a
customer profile: the required new output directory must not already exist.
"""
import argparse,asyncio,hashlib,json,os,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'apps/node')]
from swarmtracker_node_ui.customer_config import CustomerConfig
from swarmtracker_node_ui.local_inference import LocalInference
from swarmtracker_node_ui.framework_report import ReportJourney,adk_agent
from swarmtracker_node_ui import report_actions as ra


async def main(root,exercise_fallback=False,dispatch=None):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    import importlib.metadata
    root=Path(root).resolve();root.mkdir(exist_ok=False)
    cfg=CustomerConfig(root/'customer-config.db')
    cfg.save('resource',ra.TARGET,dict(description='Harmless disposable report output',provenance='operator-entered',status='unknown'))
    cfg.save('connection','macbook-ollama',dict(provider='ollama_local',endpoint='http://127.0.0.1:11434',model='qwen2.5:7b-instruct',revision='845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e',scope='local',credential_ref='none'))
    cfg.save('agent','adk-report',dict(connection_id='macbook-ollama',instructions='Return a concise plain-text report using only the supplied specimen. No tools or actions.',allowed_tools=['draft_text'],resources=[ra.TARGET],external_sharing='none',fallback_id='',adapter_kind='ollama_local_v1',runtime_version='Ollama 0.40.10 / ADK 1.5.0 wrapper',service_identity='disposable same-user process'))
    if exercise_fallback:
        import socket
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        endpoint='http://127.0.0.1:'+str(port)
        cfg.save('connection','unavailable-primary',dict(cfg.latest('connection','macbook-ollama')['body'],endpoint=endpoint))
        if dispatch is None:
            from swarmtracker_node_ui.bounded_loopback import request_json
            try:request_json(endpoint,'GET','/api/version',timeout=2)
            except (OSError,ValueError):pass
            else:raise ValueError('Disposable primary unexpectedly answered; refusing fixture substitution')
            request_json('http://127.0.0.1:11434','GET','/api/version',timeout=5)
        cfg.observe('connection','unavailable-primary','unavailable','Disposable primary connection failed before dispatch' if dispatch is None else 'Synthetic connection state for stub-only ADK test')
        cfg.observe('connection','macbook-ollama','reachable','Endpoint health only; model digest checked during governed request' if dispatch is None else 'Synthetic connection state for stub-only ADK test')
        previous=cfg.latest('agent','adk-report')
        cfg.save('agent','adk-report',dict(previous['body'],connection_id='unavailable-primary',fallback_id='macbook-ollama',pre_dispatch_fallback_enabled=True),expected_digest=previous['digest'])
    runs=LocalInference(root/'local-inference.db',cfg);actions=ra.Store(root/'report-actions.db',root/'output',cfg);journey=ReportJourney(runs,actions)
    dispatch_calls=[]
    original_dispatch=dispatch or runs._dispatch
    def observed_dispatch(*args):
        dispatch_calls.append(args[0]['connection_id']);return original_dispatch(*args)
    runs._dispatch=observed_dispatch
    call=dict(agent_id='adk-report',task_id='adk-report-1',request_id=str(uuid.uuid4()),session_id='adk-conformance',prompt='Specimen: a primary memo says badge RED; two reports copy it. Summarize the one original source and the unverified exposure in at most two sentences.',configuration=cfg.resolve_pre_dispatch('adk-report',ra.TARGET,'draft_text'),source_name='harmless copied-source specimen')
    (root/'intended-request.json').write_text(json.dumps(call,indent=2))
    async def invoke(parameters,session):
        service=InMemorySessionService();app='verity_conformance'
        await service.create_session(app_name=app,user_id='disposable_operator',session_id=session)
        runner=Runner(agent=adk_agent(journey,parameters),app_name=app,session_service=service)
        captured=[]
        async for event in runner.run_async(user_id='disposable_operator',session_id=session,new_message=types.Content(role='user',parts=[types.Part(text='Run the already-reviewed disposable Verity report request')])):
            captured.append(event.model_dump(mode='json'))
        with (root/'adk-events.jsonl').open('a') as f:
            for event in captured:f.write(json.dumps(dict(session=session,event=event))+'\n')
        parts=[p['text'] for e in captured for p in (e.get('content') or {}).get('parts',[]) if p.get('text')]
        return json.loads(parts[-1])
    proposed=await invoke(call,'first');assert proposed['state']=='proposed'
    run=runs.get(call['request_id']);assert run['state']=='completed'
    assert len(dispatch_calls)==1
    before=actions.get(proposed['proposal_id']);recovered=await invoke(dict(call,recover=True),'restart')
    assert recovered==proposed and len(runs.recent())==1 and actions.get(before['id'])==before
    approved=actions.decide(before['id'],'approved','Authorized harmless disposable conformance output only','local operator',before['head_hash'])
    written=actions.execute(before['id'],'Verity broker',approved['head_hash']);assert written['state']=='written'
    data=(root/'output'/ra.TARGET).read_bytes();assert hashlib.sha256(data).hexdigest()==before['manifest']['action']['sha256']
    try:actions.execute(written['id'],'Verity broker',written['head_hash']);raise AssertionError('Redispatch accepted')
    except ra.Conflict:pass
    paused=cfg.latest('agent','adk-report');cfg.set_routing('adk-report',False,paused['digest'])
    denied_call=dict(call,task_id='adk-paused-1',request_id=str(uuid.uuid4()),configuration=cfg.effective('adk-report',ra.TARGET))
    denied=await invoke(denied_call,'denied');assert denied['state']=='denied'
    assert len(actions.recent())==1 and len(runs.recent())==2 and len(dispatch_calls)==1
    # Read-only restart independently reopens both durable stores.
    restored=ra.Store(root/'report-actions.db',root/'output',cfg);assert restored.get(written['id'])==written
    bundle=ra.export(written['record']);anchor=ra.checkpoint(written['record']);assert ra.verify(bundle,anchor)
    (root/'report.bundle.json').write_text(json.dumps(bundle,indent=2));(root/'report.checkpoint.json').write_text(json.dumps(anchor,indent=2))
    reopened_runs=LocalInference(root/'local-inference.db',cfg)
    assert reopened_runs.get(run['id'])==run
    request_bundle=reopened_runs.request_export(run['id'])
    (root/'request.bundle.json').write_text(json.dumps(request_bundle,indent=2))
    (root/'request.checkpoint.json').write_text(json.dumps(request_bundle['checkpoint'],indent=2))
    summary=dict(state='real ADK disposable workflow passed' if dispatch is None else 'real ADK framework with stub model dispatch passed',adk_version=importlib.metadata.version('google-adk'),request_id=run['id'],proposal_id=written['id'],model=run['model'],configuration=run['configuration'],response=run['result'],output_sha256=hashlib.sha256(data).hexdigest(),durable_requests=2,model_requests=1 if dispatch is None else 0,dispatch_calls=dispatch_calls,exercise_fallback=exercise_fallback,real_runtime_exercised=dispatch is None,broker_attempts=written['attempts'],exact_recovery=True,denied_request=denied['request_id'],restart=True,observed='Real ADK Runner/BaseAgent events and '+('local Ollama response' if dispatch is None else 'deterministic stub response; no model runtime exercised'),mediated='Existing Verity LocalInference and fixed-target approval/attempt broker',containment_tested=False,limits='Same-user disposable process; direct DB/runtime/filesystem bypass remains possible. No protected OS boundary or general external exactly-once claim.',native='pending closeout',independent='pending another Mac')
    (root/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--exercise-fallback',action='store_true');args=p.parse_args()
    asyncio.run(asyncio.wait_for(main(args.output,args.exercise_fallback),timeout=180))
