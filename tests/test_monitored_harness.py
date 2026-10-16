import importlib.util
import json
from pathlib import Path
import pytest

SPEC=importlib.util.spec_from_file_location('monitored_harness',Path(__file__).resolve().parents[1]/'tools/monitored_development.py')
h=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(h)


@pytest.mark.parametrize('url',['https://127.0.0.1:8000','http://evil.invalid:8000','http://user@127.0.0.1:8000','http://127.0.0.1:8000/path','http://127.0.0.1:8000?token=x'])
def test_destination_is_exact_loopback_origin(url):
    with pytest.raises(ValueError):h.loopback(url)


def test_stream_measures_reasoning_token_before_visible_content(monkeypatch):
    chunks=[dict(choices=[dict(delta={'reasoning':'thinking'},finish_reason=None)]),dict(choices=[dict(delta={'content':'answer'},finish_reason=None)]),dict(choices=[dict(delta={},finish_reason='stop')]),dict(choices=[],usage={'completion_tokens':40})]
    lines=[b'data: '+json.dumps(c).encode()+b'\n' for c in chunks]+[b'data: [DONE]\n']
    class Response:
        def __enter__(self):return iter(lines)
        def __exit__(self,*args):pass
    class Opener:
        def open(self,*args,**kwargs):return Response()
    monkeypatch.setattr(h,'OPENER',Opener())
    ticks=iter([0,1,3,4]);monkeypatch.setattr(h.time,'monotonic',lambda:next(ticks))
    r=h.completion('http://127.0.0.1:8000',{'messages':[]})
    assert r['ttft_seconds']==1
    assert r['first_content_seconds']==3
    assert r['reasoning']=='thinking'
    assert r['completion_tokens_per_total_second']==10


def test_invalid_collector_configuration_does_not_leave_output(monkeypatch,tmp_path):
    inp=tmp_path/'input.json';inp.write_text('{}');out=tmp_path/'output.json'
    import sys
    monkeypatch.setattr(sys,'argv',['harness','--monitor-url','http://127.0.0.1:9000','--token-file',str(tmp_path/'missing-token'),'--model-url','http://127.0.0.1:8000','--input',str(inp),'--output',str(out)])
    with pytest.raises(FileNotFoundError):h.main()
    assert not out.exists()


def test_token_file_symlinks_and_shared_permissions_are_rejected(tmp_path):
    p=tmp_path/'token';p.write_text('t'*40);p.chmod(0o600)
    alias=tmp_path/'alias';alias.symlink_to(p)
    with pytest.raises(OSError):h.Collector('http://127.0.0.1:9000',alias,{})
    p.chmod(0o644)
    with pytest.raises(ValueError):h.Collector('http://127.0.0.1:9000',p,{})


def test_sigterm_preserves_failed_result(tmp_path):
    import subprocess,sys,time,signal,os
    script=tmp_path/'interrupt.py';inp=tmp_path/'input.json';out=tmp_path/'output.json';ready=tmp_path/'ready'
    inp.write_text('{}')
    script.write_text('''import importlib.util,pathlib,sys,time
spec=importlib.util.spec_from_file_location("h",sys.argv[1]);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
ready=pathlib.Path(sys.argv[4])
class Collector:
 def __init__(self,*args):self.failed=h.threading.Event();self.errors=[]
 def emit(self,*args,**kwargs):pass
h.Collector=Collector
def wait(*args):ready.write_text("ready");time.sleep(60)
h.completion=wait
sys.argv=["h","--monitor-url","http://127.0.0.1:9000","--token-file","unused","--model-url","http://127.0.0.1:8000","--input",sys.argv[2],"--output",sys.argv[3]]
raise SystemExit(h.main())
''')
    p=subprocess.Popen([sys.executable,str(script),str(Path(h.__file__)),str(inp),str(out),str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if ready.exists():break
            if p.poll() is not None:raise AssertionError(p.communicate())
            time.sleep(.02)
        assert ready.exists()
        p.send_signal(signal.SIGTERM);p.communicate(timeout=5)
        result=json.loads(out.read_text())
        assert p.returncode==1
        assert result['status']=='failed'
        assert result['error_class']=='HarnessInterrupted'
    finally:
        if p.poll() is None:p.kill();p.communicate()
