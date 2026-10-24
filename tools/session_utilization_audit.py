"""Offline request-window accounting from an anchored session snapshot.

Does not infer GPU busy time, result acceptance, idle causes or provider charges.
Does not extract/execute archive contents or contact endpoints.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile


def merge(spans):
    result=[]
    for a,b in sorted(spans):
        if result and a<=result[-1][1]:result[-1][1]=max(b,result[-1][1])
        else:result.append([a,b])
    return result


def family(name):
    for prefix,label in [('HONORABLE','research'),('PILOT','research pilot'),('RECOVERY','recovery'),('B091','ADK'),('ADK','ADK'),('CAI','CAI assistance')]:
        if name.startswith(prefix):return label
    if any(s in name for s in ('PROFILE','B18','REDUCED')):return 'evidence profiles'
    if any(s in name for s in ('SSH','TRANSPORT','HTTP','FALLBACK','B07')):return 'transport/timeouts/fallback'
    return 'Overview/models/shared readiness'


def analyze(archive,anchor,start,end,rate):
    if not 0<rate<100000 or not start<end:raise ValueError('Finite positive accounting window required')
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=anchor:raise ValueError('Archive hash mismatch')
    with tarfile.open(archive)as tar:
        def read(name,limit=5000000):
            member=tar.getmember(name)
            if not member.isfile() or member.size>limit:raise ValueError('Bounded regular member required')
            return tar.extractfile(member).read()
        manifest=json.loads(read('snapshot-manifest.json'))
        def checked(name):
            data=read(name)
            if hashlib.sha256(data).hexdigest()!=manifest['files'][name]:raise ValueError('Member hash mismatch')
            return data
        queue=json.loads(checked('runtime-closeout/final-queue.json'));rows=[]
        for task in queue['tasks']:
            r=json.loads(checked(task['id']+'/result.json'));a=r['started'];b=r['ended']
            if not start<=a<=b<=end:raise ValueError('Request outside accounting window')
            rows.append(dict(id=task['id'],replica_port=task['replica_port'],request_id=task['request_id'],source=task['source'],state=r['state'],finish_reason=r.get('finish_reason'),started=a,first_token=r.get('first_token'),last_progress=task['progress'],ended=b,seconds=b-a,rate_allocated_usd=(b-a)*rate/7200,family=family(task['id']),acceptance='Not inferred; see coordinator adjudication/source acceptance',usage=r.get('usage',{})))
        lanes={};gaps=[]
        for port in (8000,8001):
            selected=sorted([r for r in rows if r['replica_port']==port],key=lambda r:r['started']);spans=merge([(r['started'],r['ended'])for r in selected]);seconds=sum(b-a for a,b in spans)
            cursor=start
            for a,b in spans:
                if a>cursor:gaps.append(dict(replica_port=port,start=cursor,end=a,seconds=a-cursor,cause='Unknown until correlated with independent activity/authority evidence'))
                cursor=b
            if cursor<end:gaps.append(dict(replica_port=port,start=cursor,end=end,seconds=end-cursor,cause='Unknown until correlated'))
            lanes[str(port)]=dict(requests=len(selected),request_window_seconds=seconds,request_window_fraction=seconds/(end-start),incomplete_seconds=sum(r['seconds']for r in selected if r['state']!='completed'))
        any_spans=merge([(r['started'],r['ended'])for r in rows]);groups={}
        for r in rows:
            g=groups.setdefault(r['family'],dict(requests=0,completed=0,incomplete=0,request_window_seconds=0,rate_allocated_usd=0));g['requests']+=1;g['completed'if r['state']=='completed'else'incomplete']+=1;g['request_window_seconds']+=r['seconds'];g['rate_allocated_usd']+=r['rate_allocated_usd']
        logs={}
        for name in ['implementation','review']:
            text=checked('runtime-closeout/'+name+'.log').decode(errors='replace');logs[name]=dict(http_completions=text.count('POST /v1/chat/completions'),basis='HTTP receipt does not establish completion or useful output')
    return dict(schema='session_request_window_audit/v1',archive_sha256=anchor,window=dict(start=start,end=end,rate=rate),requests=sorted(rows,key=lambda x:x['started']),replicas=lanes,gaps=sorted(gaps,key=lambda x:-x['seconds']),families=groups,server_logs=logs,any_request_seconds=sum(b-a for a,b in any_spans),rate_allocated_request_usd=sum(r['rate_allocated_usd']for r in rows),limitations='Request wall windows are not GPU busy seconds. Completed is not accepted. Equal half-rate lane allocation is an analytical allocation, not a provider line item. Gap causes and opportunity costs cannot be inferred from absence alone.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--sha256',required=True);p.add_argument('--start',type=float,required=True);p.add_argument('--end',type=float,required=True);p.add_argument('--rate',type=float,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();result=analyze(a.archive,a.sha256,a.start,a.end,a.rate);a.output.mkdir(parents=True,exist_ok=True);(a.output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    keys=['id','replica_port','request_id','source','state','finish_reason','started','first_token','last_progress','ended','seconds','rate_allocated_usd','family','acceptance']
    with(a.output/'request-timeline.csv').open('w')as f:
        writer=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');writer.writeheader();writer.writerows(result['requests'])
    print(json.dumps({k:result[k]for k in ['replicas','families','any_request_seconds','rate_allocated_request_usd']}))

if __name__=='__main__':main()
