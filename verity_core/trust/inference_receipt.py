"""Portable local routed-request observation, verified against an external anchor.

Not authenticated identity, answer correctness, OS containment or action authority.
Prompt text and operator instruction text are intentionally excluded.
"""
import copy
import re
from . import record as trust, evidence_check as evidence

SCHEMA = 'inference_receipt/v0.1'
BUNDLE_SCHEMA = 'inference_receipt_bundle/v0.1'
LIMITATIONS = 'Local unauthenticated observations. Completion does not establish answer correctness, source truth, action authority or OS containment. Prompt and instruction fields omitted; returned output may quote inputs. Token usage and request cost unavailable.'


def validate(payload):
    evidence.fields(payload, 'schema run_id task_id agent_id resource_id purpose session_id state decision reason model model_revision prompt_sha256 requested_configuration_sha256 configuration result limitations')
    if payload['schema'] != SCHEMA or payload['limitations'] != LIMITATIONS:
        raise ValueError('Unknown inference receipt')
    for name in ('run_id','task_id','agent_id','resource_id','model','model_revision'):
        evidence.text(payload[name],160)
    if payload['session_id'] is not None:
        evidence.text(payload['session_id'],160)
    if payload['purpose'] not in ('generate_text','draft_text') or not re.fullmatch('[0-9a-f]{64}',payload['prompt_sha256']):
        raise ValueError('Invalid request binding')
    if payload['requested_configuration_sha256'] is not None and (not isinstance(payload['requested_configuration_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',payload['requested_configuration_sha256'])):
        raise ValueError('Invalid reviewed configuration binding')
    cfg=payload['configuration']
    if not isinstance(cfg,dict) or 'instructions' in cfg:
        raise ValueError('Receipt must omit instruction text')
    for name in ('agent_id','resource_id','model','model_revision'):
        if cfg[name] != payload[name]:raise ValueError('Configuration identity mismatch')
    for name in ('agent_version','connection_version','resource_version'):
        if type(cfg[name]) is not int or cfg[name]<1:raise ValueError('Invalid configuration version')
    for name in ('agent_hash','connection_hash','resource_hash','instructions_sha256'):
        if not isinstance(cfg[name],str) or not re.fullmatch('[0-9a-f]{64}',cfg[name]):raise ValueError('Invalid configuration hash')
    if payload['state'] not in ('completed','failed','denied'):
        raise ValueError('Unfinished request has no receipt')
    if payload['decision'] != ('denied' if payload['state']=='denied' else 'allowed'):
        raise ValueError('Request decision/state mismatch')
    if payload['reason'] is not None:evidence.text(payload['reason'],1000)
    result=payload['result']
    if payload['state']=='completed':
        evidence.fields(result,'response response_sha256 runtime_event_id runtime_decision finish_reason provider')
        evidence.text(result['response'],20000);evidence.text(result['runtime_event_id'],160)
        if result['response_sha256']!=evidence.digest(result['response']) or result['runtime_decision'] not in ('allowed','allow') or result['finish_reason'] not in (None,'stop'):
            raise ValueError('Unverifiable runtime result')
        if result['provider'] is not None:evidence.text(result['provider'],160)
    elif result is not None:
        raise ValueError('Incomplete request cannot carry a completed result')
    if len(trust.canonical_json(payload).encode())>131072:raise ValueError('Receipt exceeds bounded size')
    return payload


def export(run):
    metadata={'agent_id','agent_version','agent_hash','connection_id','connection_version','connection_hash',
              'model','model_revision','provider','instructions_sha256','allowed_tools','external_sharing',
              'resource_id','resource_version','resource_hash','executor_id','machine_id','machine_version','machine_hash',
              'adapter_kind','runtime_version','service_identity','identity_status','daily_request_limit',
              'session_request_limit','require_fresh_connection_observation','fallback_id','fallback_version',
              'fallback_model','fallback_revision','provenance'}
    cfg={k:copy.deepcopy(v) for k,v in run['configuration'].items() if k in metadata}
    payload={k:copy.deepcopy(run[k]) for k in ('task_id','agent_id','resource_id','purpose','session_id','state','decision','reason','model','model_revision','prompt_sha256','result')}
    payload.update(schema=SCHEMA,run_id=run['id'],configuration=cfg,requested_configuration_sha256=run.get('requested_configuration_sha256'),limitations=LIMITATIONS)
    validate(payload)
    record=trust.new_record(dict(kind='inference_receipt',identifier=run['id']))
    trust.append_entry(record,'note',payload=payload)
    checkpoint=dict(record_id=record['record_id'],entry_count=1,head_hash=record['entries'][0]['entry_hash'])
    return dict(schema=BUNDLE_SCHEMA,record=record,checkpoint=checkpoint)


def verify(bundle,retained_checkpoint):
    try:
        evidence.fields(bundle,'schema record checkpoint');evidence.fields(retained_checkpoint,'record_id entry_count head_hash')
        record=bundle['record']
        if bundle['schema']!=BUNDLE_SCHEMA or not isinstance(record,dict) or not isinstance(record.get('entries'),list) or len(record['entries'])!=1:return False
        if len(trust.canonical_json(bundle).encode())>140000:return False
        payload=validate(record['entries'][0]['payload'])
        if not trust.chain_valid(record) or record['refs']!={} or record['entries'][0]['entry_type']!='note':return False
        if record['subject']!=dict(kind='inference_receipt',identifier=payload['run_id']):return False
        actual=dict(record_id=record['record_id'],entry_count=1,head_hash=record['entries'][0]['entry_hash'])
        return type(retained_checkpoint['entry_count']) is int and actual==bundle['checkpoint']==retained_checkpoint
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,UnicodeError,RecursionError):
        return False


def main():
    import argparse
    from pathlib import Path
    from ..json_safety import bounded_json_loads
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('receipt');parser.add_argument('--checkpoint',required=True);args=parser.parse_args()
    try:
        raw=Path(args.receipt).read_bytes();anchor=Path(args.checkpoint).read_bytes()
        if len(raw)>140000 or len(anchor)>4096:raise ValueError('Input too large')
        ok=verify(bounded_json_loads(raw,max_depth=32),bounded_json_loads(anchor,max_depth=8))
    except (OSError,ValueError,TypeError,RecursionError):ok=False
    print('Receipt matches retained checkpoint; interpretation remains unverified.' if ok else 'Receipt verification failed.')
    return 0 if ok else 1

if __name__=='__main__':
    raise SystemExit(main())
