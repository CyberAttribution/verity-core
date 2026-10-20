"""Read-only bounded evidence view for prepared replica workers.

A task marker is not model-server proof of inference, and a completed response
is not acceptance. This module never dispatches, retries, or provisions.
"""
import json
import time
from pathlib import Path

MAX_STATUS_BYTES=131072


def _read(path):
    with Path(path).open('rb') as f:raw=f.read(MAX_STATUS_BYTES+1)
    if len(raw)>MAX_STATUS_BYTES:raise ValueError('Status exceeds bound')
    result=json.loads(raw)
    if not isinstance(result,(dict,list)):raise ValueError('Invalid status')
    return result


def summarize(root,replica,*,now=None,stale_after=120):
    if type(replica) is not int or replica not in (0,1):raise ValueError('Unknown replica')
    root=Path(root);now=time.time() if now is None else now
    result=dict(replica=replica,phase='unknown',actual_model=None,current_task=None,last_progress=None,
                last_result=None,queue_depth=None,telemetry='unknown',automatic_acceptance=False)
    try:
        state=_read(root/('replica'+str(replica)+'-status.json'))
        if not isinstance(state,dict) or type(state.get('replica')) is not int or state.get('replica')!=replica:raise ValueError('Mismatched replica')
        for key,limit,optional in [('phase',240,False),('current_task',80,True),('actual_model',160,True)]:
            value=state.get(key)
            if value is None and optional:continue
            if not isinstance(value,str) or not 0<len(value)<=limit or any(ord(c)<32 for c in value):
                raise ValueError('Invalid worker identity or phase')
        for key in ('low_fields_audited','template_low_verified'):
            if state.get(key) is not None and type(state[key]) is not bool:raise ValueError('Invalid audit marker')
        at=state.get('last_progress')
        if type(at) not in (int,float) or not 0<at<=now+5:raise ValueError('Invalid progress time')
        result.update({k:state.get(k) for k in ('actual_model','current_task','last_progress','queue_depth','phase','low_fields_audited','template_low_verified')})
        depth=result['queue_depth']
        if depth is not None and (type(depth) is not int or not 0<=depth<=100):raise ValueError('Invalid queue depth')
        results_path=root/('reviews-replica'+str(replica))/'results.json'
        results=_read(results_path) if results_path.exists() else []
        if not isinstance(results,list) or len(results)>100:raise ValueError('Invalid result list')
        if results:
            last=results[-1]
            if not isinstance(last,dict) or last.get('state') not in ('awaiting_independent_review','incomplete','unknown'):raise ValueError('Invalid result state')
            result['last_result']={k:last.get(k) for k in ('task_id','state','input_commit','source_sha256','response_sha256','finish_reason','usage','seconds')}
        # Current task and exhausted queue are current-state claims. Preserve their
        # last reported values separately when the observer is stale or inconsistent.
        if now-at>stale_after:
            result.update(telemetry='stale',reported_phase=result['phase'],reported_current_task=result['current_task'],reported_queue_depth=depth,
                          phase='unknown',current_task=None,queue_depth=None)
        elif result['phase']=='substantive inference' and not result['current_task']:
            raise ValueError('Inference marker lacks task identity')
        else:result['telemetry']='observed worker report'
    except (OSError,ValueError,TypeError,KeyError):
        result.update(phase='unknown',actual_model=None,current_task=None,last_progress=None,last_result=None,queue_depth=None,telemetry='unknown',low_fields_audited=None,template_low_verified=None)
    result['service']=service_view(root,replica,now=now,stale_after=stale_after)
    return result


def collect_service(replica,*,opener=None,clock=time.time):
    """Probe fixed loopback health, identity and metrics; never execute inference."""
    import urllib.request
    if type(replica) is not int or replica not in (0,1):raise ValueError('Unknown replica')
    if opener is None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    base='http://127.0.0.1:'+str(8000+replica)
    result=dict(replica=replica,at=clock(),state='unknown',models=[],running_requests=None,waiting_requests=None,activity='unknown')
    def read(path,limit):
        with opener.open(urllib.request.Request(base+path),timeout=2) as response:
            if response.status!=200:raise ValueError('Unhealthy service response')
            raw=response.read(limit+1)
            if len(raw)>limit:raise ValueError('Service observation exceeds bound')
            return raw
    try:
        read('/health',4096)
        models=json.loads(read('/v1/models',65536))
        entries=models.get('data') if isinstance(models,dict) else None
        if (not isinstance(entries,list) or not entries or len(entries)>20
                or any(not isinstance(x,dict) or not isinstance(x.get('id'),str) or not 0<len(x['id'])<=160 for x in entries)):
            raise ValueError('Model identity not observed')
        result.update(state='ready',models=[x['id'] for x in entries])
        try:
            metrics=read('/metrics',262144).decode('utf-8')
            for key in ('running','waiting'):
                samples=[]
                for line in metrics.splitlines():
                    name=line.split(' ',1)[0]
                    if name.split('{',1)[0]=='vllm:num_requests_'+key:
                        value=float(line.split()[-1])
                        if not value.is_integer() or not 0<=value<=10000:raise ValueError('Invalid request count')
                        samples.append(int(value))
                if samples:result[key+'_requests']=sum(samples)
            if result['running_requests'] is not None:
                result['activity']='inference reported by server' if result['running_requests'] else 'no inference reported by server'
        except (OSError,ValueError,UnicodeError):pass  # Missing metrics never implies zero requests.
    except (OSError,ValueError,TypeError,KeyError):pass
    return result


def service_view(root,replica,*,now=None,stale_after=120):
    now=time.time() if now is None else now
    result=dict(state='unknown',activity='unknown',models=[],running_requests=None,waiting_requests=None)
    try:
        sample=_read(Path(root)/('replica'+str(replica)+'-service.json'))
        if (not isinstance(sample,dict) or type(sample.get('replica')) is not int or sample.get('replica')!=replica or type(sample.get('at')) not in (int,float)
                or not 0<sample['at']<=now+5 or now-sample['at']>stale_after):return result
        models=sample.get('models')
        if (sample.get('state')!='ready' or not isinstance(models,list) or not 1<=len(models)<=20
                or any(not isinstance(x,str) or not 0<len(x)<=160 for x in models)):return result
        for key in ('running_requests','waiting_requests'):
            value=sample.get(key)
            if value is not None and (type(value) is not int or not 0<=value<=10000):return result
        result.update(state='ready',models=models,running_requests=sample.get('running_requests'),waiting_requests=sample.get('waiting_requests'))
        running=result['running_requests']
        result['activity']=('unknown' if running is None else 'inference reported by server' if running else 'no inference reported by server')
    except (OSError,ValueError,TypeError,KeyError):pass
    return result
