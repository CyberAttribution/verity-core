"""Internal provisional reduced copy, not a cryptographic selective proof.

Only child integrity is checked without a supplied full parent. Exact parent
checkpoints are references, not proof of withheld contents or authenticated
origin. Qualifications and corrections can themselves quote private inputs;
review every disclosed value before export. No anonymity, completeness, source
truth, operator identity, execution authority or public compatibility claim.
"""
import copy,json,re
from . import record as trust,evidence_profile as profile

SCHEMA='verity_internal_reduced_profile/v0.1'
LIMITS=__doc__.strip()
WITHHELD=['raw article/source text','model response','request instructions and configuration details','denial detail','context contents and reference']
FIELDS=('task_id','request_id','agent_id','resource_id','session_id','model','model_revision','operator_decision','denial_task_id','denial_request_id','relation','source_dependence','disagreement','known_corrections','corrections_recorded_through')


def projection(parent):
    if not profile.verify(parent,parent['checkpoint']):raise ValueError('Intact full parent profile required')
    p=parent['record']['entries'][0]['payload'];mapping=p['mapping'];decision=p['decision']['record']['entries'][1]['payload']
    reduced={k:copy.deepcopy(mapping[k]) for k in FIELDS}
    reduced['context_status']='none recorded' if mapping['context'] is None else 'reference withheld; context exists'
    corrections=[]
    for correction in p['corrections']:
        e=correction['record']['entries'][0];c=e['payload']
        corrections.append(dict(kind=c['kind'],text=c['text'],at=e['at_utc'],checkpoint=copy.deepcopy(correction['checkpoint'])))
    return dict(mapping=reduced,decision_qualification=decision['reason'],corrections=corrections,
        parent_references=dict(profile=copy.deepcopy(parent['checkpoint']),decision=copy.deepcopy(p['decision']['checkpoint']),denial=copy.deepcopy(p['denial']['checkpoint'])),
        withheld=list(WITHHELD),limitations=LIMITS)


def create(parent):
    p=projection(parent);record=trust.new_record(dict(kind='internal_reduced_profile',identifier=p['mapping']['task_id']))
    trust.append_entry(record,'note',payload=dict(schema=SCHEMA,**p))
    return dict(schema=SCHEMA,record=record,checkpoint=profile._checkpoint(record))


def _reference(value):
    if not isinstance(value,dict) or set(value)!={'record_id','entry_count','head_hash'}:raise ValueError('Exact checkpoint reference required')
    if type(value['entry_count']) is not int or not 0<value['entry_count']<1000 or not isinstance(value['record_id'],str) or not 0<len(value['record_id'])<160 or not re.fullmatch('[0-9a-f]{64}',value['head_hash']):raise ValueError('Invalid checkpoint reference')


def verify(bundle,anchor,*,parent=None):
    result=dict(child_integrity=False,parent_check='not supplied',correspondence=None,limitations=LIMITS)
    try:
        if len(trust.canonical_json(bundle).encode())>100000 or not profile._intact(bundle,SCHEMA,anchor):return result
        record=bundle['record'];entries=record['entries']
        if len(entries)!=1 or entries[0]['entry_type']!='note':return result
        p=entries[0]['payload']
        if set(p)!={'schema','mapping','decision_qualification','corrections','parent_references','withheld','limitations'} or p['schema']!=SCHEMA or p['withheld']!=WITHHELD or p['limitations']!=LIMITS:return result
        mapping=p['mapping']
        if not isinstance(mapping,dict) or set(mapping)!=set(FIELDS)|{'context_status'}:return result
        if mapping['context_status'] not in ('none recorded','reference withheld; context exists'):return result
        if type(mapping['known_corrections']) is not int or not 1<=mapping['known_corrections']<=20:return result
        for k,v in mapping.items():
            if k=='known_corrections':continue
            if v is None and k=='session_id':continue
            if not isinstance(v,str) or not 0<len(v)<=500:return result
        if mapping['operator_decision'] not in ('accepted','rejected'):return result
        if record['subject']!=dict(kind='internal_reduced_profile',identifier=mapping['task_id']):return result
        if not isinstance(p['decision_qualification'],str) or not 0<len(p['decision_qualification'])<=2000:return result
        if set(p['parent_references'])!={'profile','decision','denial'}:return result
        for ref in p['parent_references'].values():_reference(ref)
        if not isinstance(p['corrections'],list) or len(p['corrections'])!=mapping['known_corrections']:return result
        for c in p['corrections']:
            if set(c)!={'kind','text','at','checkpoint'} or c['kind'] not in ('qualification','correction','withdrawal') or not isinstance(c['text'],str) or not 0<len(c['text'])<=2000 or not isinstance(c['at'],str) or not 0<len(c['at'])<100:return result
            _reference(c['checkpoint'])
        result['child_integrity']=True
        if parent is not None:
            result.update(parent_check='failed',correspondence=False)
            if profile.verify(parent,p['parent_references']['profile']):
                result.update(parent_check='matched retained parent reference',correspondence={k:v for k,v in p.items() if k!='schema'}==projection(parent))
        return result
    except (ValueError,KeyError,TypeError,AttributeError,IndexError,RecursionError,UnicodeError):return result


def main():
    import argparse
    from pathlib import Path
    from ..json_safety import bounded_json_loads
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('bundle');parser.add_argument('--checkpoint',required=True);parser.add_argument('--parent');args=parser.parse_args()
    try:
        def read(path,limit):
            raw=Path(path).read_bytes()
            if len(raw)>limit:raise ValueError('Bounded input required')
            return bounded_json_loads(raw,max_depth=48)
        result=verify(read(args.bundle,100000),read(args.checkpoint,4096),parent=read(args.parent,800000) if args.parent else None)
    except (OSError,ValueError,TypeError,RecursionError):result=dict(child_integrity=False,parent_check='unavailable',correspondence=None)
    print(json.dumps(result,indent=2))
    return 0 if result['child_integrity'] and (not args.parent or result['correspondence'] is True) else 1


if __name__=='__main__':raise SystemExit(main())
