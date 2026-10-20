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
        if not isinstance(state,dict) or state.get('replica')!=replica:raise ValueError('Mismatched replica')
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
        result.update(phase='unknown',current_task=None,queue_depth=None,telemetry='unknown')
    return result
