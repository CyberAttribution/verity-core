import hashlib
import io
import json
import tarfile
from pathlib import Path
import pytest
from tools.session_utilization_audit import analyze,merge


def test_overlap_incomplete_and_manifest_refusal(tmp_path):
    archive=tmp_path/'capture.tar.gz';files={};tasks=[]
    for name,port,a,b,state in [('A',8000,10,30,'completed'),('B',8001,20,50,'incomplete')]:
        tasks.append(dict(id=name,replica_port=port,request_id=name,source='exact-source',progress=b))
        files[name+'/result.json']=json.dumps(dict(started=a,ended=b,first_token=a+1,state=state,finish_reason='stop'if state=='completed'else'length')).encode()
    files['runtime-closeout/final-queue.json']=json.dumps({'tasks':tasks}).encode()
    for n in ['implementation','review']:files['runtime-closeout/'+n+'.log']=b'POST /v1/chat/completions'
    files['snapshot-manifest.json']=json.dumps({'files':{n:hashlib.sha256(v).hexdigest()for n,v in files.items()}}).encode()
    with tarfile.open(archive,'w:gz')as t:
        for n,v in files.items():
            info=tarfile.TarInfo(n);info.size=len(v);t.addfile(info,io.BytesIO(v))
    sha=hashlib.sha256(archive.read_bytes()).hexdigest();r=analyze(archive,sha,0,60,20)
    assert r['any_request_seconds']==40  # Overlap counted once, not 50 seconds.
    assert r['replicas']['8001']['incomplete_seconds']==30
    assert r['rate_allocated_request_usd']==pytest.approx(50*10/3600)
    assert len(r['gaps'])==4 and all('Unknown'in g['cause']for g in r['gaps'])
    assert all('Not inferred'in x['acceptance']for x in r['requests'])
    with pytest.raises(ValueError,match='hash mismatch'):analyze(archive,'0'*64,0,60,20)
    with pytest.raises(ValueError,match='outside accounting'):analyze(archive,sha,0,40,20)
