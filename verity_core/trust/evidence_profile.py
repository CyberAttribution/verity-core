"""Internal provisional evidence mapping; no public compatibility commitment.

Integrity requires a separately retained export checkpoint. Local operator and
runtime identities are unauthenticated; source truth, completeness, containment,
and execution authority are not established. Full artifacts may contain private
source and instruction text. Export only to an authorized recipient.
"""
import copy
import json
from . import record as trust, research_record, inference_receipt

SCHEMA = 'verity_internal_evidence_profile/v0.1'
CORRECTION = 'verity_research_correction/v0.1'
LIMITATIONS = __doc__.strip()


def _checkpoint(record):
    return research_record.checkpoint(record)


def _intact(bundle, schema, anchor):
    return (isinstance(bundle,dict) and set(bundle)=={'schema','record','checkpoint'}
            and bundle['schema']==schema and trust.chain_valid(bundle['record'])
            and bundle['record']['refs']=={} and _checkpoint(bundle['record'])==bundle['checkpoint']==anchor
            and type(anchor['entry_count']) is int)


def correction(decision, previous, text, *, kind='qualification'):
    if kind not in ('qualification','correction','withdrawal') or not isinstance(text,str) or not 0<len(text.strip())<=2000:
        raise ValueError('Bounded correction and supported kind required')
    if not research_record.verify(decision,decision['checkpoint']) or len(decision['record']['entries'])!=2:
        raise ValueError('Intact decided research required')
    _corrections(decision,previous)
    subject=decision['record']['subject']['identifier']
    record=trust.new_record(dict(kind='research_correction',identifier=subject))
    trust.append_entry(record,'note',actor='local operator',payload=dict(
        schema=CORRECTION,work_id=subject,decision_checkpoint=copy.deepcopy(decision['checkpoint']),
        proposal_binding=decision['record']['entries'][0]['payload']['binding'],
        previous_correction_head=previous[-1]['checkpoint']['head_hash'] if previous else None,
        kind=kind,text=text.strip(),identity_provenance='local operator intent; unauthenticated',
        authority='qualification only; no execution or new acceptance'))
    return dict(schema=CORRECTION,record=record,checkpoint=_checkpoint(record))


def _corrections(decision, corrections):
    if not isinstance(corrections,list) or len(corrections)>20:
        raise ValueError('Bounded correction history required')
    previous=None;seen=set()
    for bundle in corrections:
        if not _intact(bundle,CORRECTION,bundle['checkpoint']):raise ValueError('Correction integrity differs')
        record=bundle['record'];entries=record['entries']
        if len(entries)!=1 or entries[0]['entry_type']!='note' or entries[0]['actor']!='local operator':
            raise ValueError('Correction event required')
        p=entries[0]['payload']
        if set(p)!={'schema','work_id','decision_checkpoint','proposal_binding','previous_correction_head','kind','text','identity_provenance','authority'}:
            raise ValueError('Unsupported correction semantics')
        if (p['schema']!=CORRECTION or p['work_id']!=decision['record']['subject']['identifier']
            or record['subject']!=dict(kind='research_correction',identifier=p['work_id'])
            or p['decision_checkpoint']!=decision['checkpoint']
            or p['proposal_binding']!=decision['record']['entries'][0]['payload']['binding']
            or p['previous_correction_head']!=previous
            or p['identity_provenance']!='local operator intent; unauthenticated'
            or p['authority']!='qualification only; no execution or new acceptance'
            or p['kind'] not in ('qualification','correction','withdrawal')
            or not isinstance(p['text'],str) or not 0<len(p['text'])<=2000):
            raise ValueError('Correction binding or meaning differs')
        head=bundle['checkpoint']['head_hash']
        if head in seen:raise ValueError('Duplicate correction')
        seen.add(head);previous=head
    return corrections


def validate_corrections(decision, corrections):
    if not research_record.verify(decision,decision['checkpoint']):raise ValueError('Research integrity differs')
    return _corrections(decision,corrections)


def _mapping(decision,denial,corrections):
    if not research_record.verify(decision,decision['checkpoint']) or len(decision['record']['entries'])!=2:
        raise ValueError('Decided research required')
    p=decision['record']['entries'][0]['payload']['proposal'];identity=p.get('intake_identity')
    if not isinstance(identity,dict):raise ValueError('Bound intake identity required; historical record remains readable')
    if not inference_receipt.verify(denial,denial['checkpoint']):raise ValueError('Denial integrity differs')
    denied=denial['record']['entries'][0]['payload'];run=p['request_bundle']['record']['entries'][0]['payload']
    if denied['state']!='denied' or denied['decision']!='denied' or denied['result'] is not None:
        raise ValueError('Denied action required')
    if any(denied[k]!=run[k] for k in ('agent_id','resource_id','session_id')) or denied['run_id']==run['run_id'] or denied['task_id']==run['task_id']:
        raise ValueError('Denial must identify a separate task in the same agent/resource/session scope')
    _corrections(decision,corrections)
    if not corrections:raise ValueError('At least one correction required')
    return dict(task_id=run['task_id'],request_id=run['run_id'],agent_id=run['agent_id'],resource_id=run['resource_id'],
        session_id=run['session_id'],context=identity['context_ref'],model=run['model'],model_revision=run['model_revision'],
        requested_configuration_sha256=run['requested_configuration_sha256'],
        operator_decision=decision['record']['entries'][1]['payload']['state'],
        denial_task_id=denied['task_id'],denial_request_id=denied['run_id'],
        relation='Separate denied task in same agent/resource/session; no inferred causal or execution authority',
        source_dependence='unknown; operator-supplied source; copies do not establish independence',
        disagreement='unassessed except retained source, output, decision qualifications and corrections',
        known_corrections=len(corrections),corrections_recorded_through=corrections[-1]['record']['entries'][0]['at_utc'])


def assemble(decision,denial,corrections):
    mapping=_mapping(decision,denial,corrections)
    record=trust.new_record(dict(kind='internal_evidence_profile',identifier=mapping['task_id']))
    trust.append_entry(record,'note',payload=dict(schema=SCHEMA,internal=True,provisional=True,
        critical=['decision','denial','corrections','disclosure'],mapping=mapping,
        decision=copy.deepcopy(decision),denial=copy.deepcopy(denial),corrections=copy.deepcopy(corrections),
        disclosure=LIMITATIONS))
    return dict(schema=SCHEMA,record=record,checkpoint=_checkpoint(record))


def verify(bundle,anchor):
    try:
        if len(trust.canonical_json(bundle).encode())>800000 or not _intact(bundle,SCHEMA,anchor):return False
        record=bundle['record'];entries=record['entries']
        if len(entries)!=1 or entries[0]['entry_type']!='note':return False
        p=entries[0]['payload']
        if set(p)!={'schema','internal','provisional','critical','mapping','decision','denial','corrections','disclosure'}:return False
        if p['schema']!=SCHEMA or p['internal'] is not True or p['provisional'] is not True or p['critical']!=['decision','denial','corrections','disclosure'] or p['disclosure']!=LIMITATIONS:return False
        mapping=_mapping(p['decision'],p['denial'],p['corrections'])
        return p['mapping']==mapping and record['subject']==dict(kind='internal_evidence_profile',identifier=mapping['task_id'])
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,RecursionError,UnicodeError):return False


def main():
    import argparse
    from pathlib import Path
    from ..json_safety import bounded_json_loads
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('profile');parser.add_argument('--checkpoint',required=True);args=parser.parse_args()
    try:
        raw=Path(args.profile).read_bytes();anchor=Path(args.checkpoint).read_bytes()
        if len(raw)>800000 or len(anchor)>4096:raise ValueError('Bounded export required')
        ok=verify(bounded_json_loads(raw,max_depth=48),bounded_json_loads(anchor,max_depth=8))
    except (OSError,ValueError,TypeError,RecursionError):ok=False
    print('Internal provisional profile integrity verified. Read all qualifications, corrections and disclosure limits; identity, source truth, completeness and authority remain unverified.' if ok else 'Evidence profile verification failed.')
    return 0 if ok else 1

if __name__=='__main__':raise SystemExit(main())
