import http.client,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from swarmtracker_node_ui.bounded_loopback import request_json


def test_trickling_body_is_interrupted_by_total_deadline():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Length','10000');self.end_headers()
            try:
                for i in range(1000):self.wfile.write(b' ');self.wfile.flush();time.sleep(.01)
            except (OSError,BrokenPipeError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();start=time.monotonic()
    try:
        with pytest.raises((TimeoutError,OSError,ValueError,http.client.HTTPException)):
            request_json('http://127.0.0.1:'+str(server.server_port),'GET','/api/tags',timeout=.2)
        assert time.monotonic()-start<1
    finally:server.shutdown();server.server_close()


@pytest.mark.parametrize('code,body',[(302,b'{}'),(200,b' '*2000)])
def test_redirect_or_oversized_envelope_refused(code,body):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with pytest.raises(ValueError):request_json('http://127.0.0.1:'+str(server.server_port),'GET','/api/tags',max_bytes=1000)
    finally:server.shutdown();server.server_close()


def test_inference_wait_can_exceed_short_connect_timeout():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            time.sleep(5.2)
            body=b'{"done":true}'
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        assert request_json('http://127.0.0.1:'+str(server.server_port),'POST','/api/generate',{},timeout=7)=={'done':True}
    finally:server.shutdown();server.server_close()


def test_establishment_has_short_bound_no_request_or_retry(monkeypatch):
    calls=[]
    class Refused:
        def __init__(self,host,port,timeout):calls.append(('bound',timeout))
        def connect(self):calls.append('connect');raise TimeoutError('connect deadline')
        def request(self,*args,**kwargs):raise AssertionError('must not send after failed connect')
        def close(self):calls.append('close')
    monkeypatch.setattr('swarmtracker_node_ui.bounded_loopback.http.client.HTTPConnection',Refused)
    with pytest.raises(TimeoutError):request_json('http://127.0.0.1:9','GET','/api/tags',timeout=90)
    assert calls==[('bound',5),'connect','close']


def test_establishment_consumes_overall_deadline(monkeypatch):
    from swarmtracker_node_ui import bounded_loopback as transport
    clock=iter([10.,10.3]);calls=[]
    class Connected:
        def __init__(self,*args,**kw):self.sock=self
        def connect(self):pass
        def settimeout(self,value):calls.append(value)
        def close(self):calls.append('closed')
    monkeypatch.setattr(transport.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(transport.http.client,'HTTPConnection',Connected)
    with pytest.raises(TimeoutError,match='during connect'):request_json('http://127.0.0.1:9','GET','/api/tags',timeout=.2)
    assert calls==['closed']
