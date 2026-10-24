"""Offline reconstruction of a captured experiment; does not rerun inference.

Checks retained authorization/application linkage, controls and scoring. Hashes
and self-reported instrumentation do not authenticate the experiment operator.
Use an independently retained archive manifest to anchor these files.
"""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from cai_cpu_probe import digest, packets, score


def verify(root):
    root=Path(root)
    read=lambda name:json.loads((root/name).read_text())
    lines=lambda name:[json.loads(x) for x in (root/name).read_text().splitlines()]
    frozen=read('frozen-before-heldout.json');summary=read('summary.json')
    requests=lines('requests.jsonl');intents=lines('authorization-intents.jsonl')
    events=lines('application-events.jsonl');by_id=defaultdict(list)
    for e in events:by_id[e['request_id']].append(e)
    intent_ids={x['profile']['request_id']:x for x in intents}
    assert len(intent_ids)==len(intents)
    assert digest(packets('heldout'))==frozen['heldout_sha256']
    assert digest(packets('tuning'))==frozen['tuning_sha256']
    heldout=[r for r in requests if r['phase']=='heldout']
    assert len(heldout)==50
    expected={p['id']:p for p in packets('heldout')}
    counts=defaultdict(int);outputs=defaultdict(dict);totals={}
    for r in requests:
        p=r['profile'];identifier=r['request_id'];assert identifier==p['request_id']
        assert intent_ids[identifier]['profile']==p
        assert digest(p)==r['authorization_digest']
        assert p['model_revision']==frozen['model_pin']['revision']
        assert p['template_sha256']==frozen['template_sha256']
        assert p['runtime']==frozen['runtime']
        assert p['site']==frozen['site'] and p['token_scope']==frozen['token_scope']
        assert r['hooks_removed']
        logged=by_id[identifier]
        assert len(logged)==len(r['instrumentation'])
        for a,b in zip(logged,r['instrumentation']):
            assert {k:v for k,v in a.items() if k not in ('request_id','at')}==b
            assert a['at']<p['expires_at']
            if r['condition']=='zero':
                assert a['delta_norm']==0 and a['before_sha256']==a['after_sha256']
            else:
                assert a['delta_norm']>0 and a['before_sha256']!=a['after_sha256']
                assert math.isclose(a['delta_norm'],p['coefficient'],abs_tol=1e-4)
        if r['condition'] in ('ordinary','improved'):assert not logged
        else:assert logged
        if r['phase']!='heldout':continue
        assert intent_ids[identifier]['at']>=frozen['frozen_at']
        key=(r['task_id'],r['condition']);counts[key]+=1;assert counts[key]==1
        assert score(r['output'],expected[r['task_id']])==r['score']
        outputs[r['task_id']][r['condition']]=r['output']
        t=totals.setdefault(r['condition'],dict(n=0,correct=0,malformed=0,unsupported=0,appropriate_abstention=0,unnecessary_abstention=0,seconds=0.,tokens=0))
        t['n']+=1;t['seconds']+=r['seconds'];t['tokens']+=r['output_tokens']
        for k,v in r['score'].items():t[k]+=v
    for t in outputs.values():assert t['improved']==t['zero']
    assert totals==summary['effect']
    assert summary['execution']['request_isolation_replay']
    return dict(state='verified captured evidence',requests=len(requests),application_events=len(events),heldout=len(heldout),effect=totals,interpretation='No measured candidate benefit over improved/zero/random controls in this small synthetic set.',limitations=summary['limitations'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('capture');args=p.parse_args()
    print(json.dumps(verify(args.capture),indent=2))
