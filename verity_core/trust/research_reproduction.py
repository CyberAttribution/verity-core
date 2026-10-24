"""Offline anchored integrity and historical calculation reproduction only.

Never executes code, follows URLs, or calls models from a supplied export.
Source support, source independence, authority authenticity and completeness are
not verified. Retain the checkpoint independently from the package.
"""
import argparse
import json
from pathlib import Path
from . import record as trust,research_record
from .research_finance import effective_observations,comparisons,scenarios


def verify(bundle,anchor):
    try:
        record=bundle['record'];entries=record['entries'];a=entries[0]['payload']['assignment']
        intact=(bundle['schema']=='verity_governed_research/v0.1' and bool(entries) and trust.chain_valid(record)
                and research_record.checkpoint(record)==anchor==bundle['checkpoint']
                and record['subject']==dict(kind='governed_research',identifier=a['task_id'])
                and all(e['payload']['schema']==bundle['schema'] and e['payload']['task_id']==a['task_id'] for e in entries))
        if not intact:return dict(integrity=False,calculations_reproduced=False,reason='Record or retained checkpoint differs')
        subjects=list(a['subjects']);checked=0
        for index,e in enumerate(entries):
            p=e['payload']
            if p['kind']=='subject_added':subjects.append(p['body'])
            elif p['kind']=='calculation':
                body=p['body'];before=entries[:index]
                if not before or body['input_head']!=before[-1]['entry_hash']:raise ValueError('Historical input head differs')
                if body['comparables']!=comparisons(effective_observations(before),subjects):raise ValueError('Historical comparable calculations differ')
                if body['analysis']!=scenarios([r['assumptions'] for r in body['analysis']['scenarios']]):raise ValueError('Scenario calculations differ')
                checked+=1
        return dict(integrity=True,calculations_reproduced=True,calculation_revisions=checked,source_support='not assessed',source_independence='not assessed',authority_authenticity='not assessed',completeness='not assessed',limitations=__doc__.strip())
    except (ValueError,KeyError,TypeError,IndexError,AttributeError):return dict(integrity=False,calculations_reproduced=False,reason='Unsupported, invalid or unreproducible package')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bundle');p.add_argument('--checkpoint',required=True);args=p.parse_args()
    for path in (args.bundle,args.checkpoint):
        if Path(path).stat().st_size>8_000_000:raise SystemExit('Package exceeds supported8MB bound')
    from verity_core.json_safety import bounded_json_loads
    bundle=bounded_json_loads(Path(args.bundle).read_text())
    anchor=bounded_json_loads(Path(args.checkpoint).read_text())
    result=verify(bundle,anchor);print(json.dumps(result,indent=2));raise SystemExit(0 if result['integrity'] and result['calculations_reproduced'] else 1)


if __name__=='__main__':main()
