"""Direct-local transport and provenance regressions with real HTTP traps."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest
from swarmtracker_node_ui import config, ollama_client as ol, openclaw_client as oc

@pytest.fixture
def trap(monkeypatch):
    state = {"mode":"ok", "calls":[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def do_GET(self):
            self.send_json({"version":"test-version"})
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append((self.path,data))
            if state["mode"]=="redirect":
                self.send_response(307);self.send_header("Location","http://127.0.0.1:1/trap")
                self.send_header("Content-Length","0");self.end_headers();return
            if self.path=="/api/show":
                meta={"details":{"format":"gguf"},"model_info":{"general.architecture":"qwen2"}}
                if state["mode"]=="remote":meta["remote_model"]="secret-cloud-alias"
                if state["mode"]=="no_weights":meta={}
                self.send_json(meta);return
            body={"done":True,"done_reason":"stop","response":"Untrusted synthetic assessment","model":"test:latest"}
            if state["mode"]=="partial":body["done"]=False
            if state["mode"]=="length":body["done_reason"]="length"
            if state["mode"]=="empty":body["response"]=""
            if state["mode"]=="bad_shape":body=[]
            self.send_json(body)
        def send_json(self,body):
            raw=json.dumps(body).encode()
            self.send_response(200);self.send_header("Content-Length",str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(config,"OLLAMA_URL","http://127.0.0.1:"+str(server.server_port))
    monkeypatch.setattr(config,"RUNTIME_KIND","ollama")
    monkeypatch.setattr(config,"DEFAULT_MODEL","test:latest")
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(3)

def test_existing_analysis_call_dispatches_to_local_runtime_without_audit_fabrication(trap,monkeypatch):
    for key in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
        monkeypatch.setenv(key,"http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY","")
    assert oc.healthz()["framework"]=="Ollama native API (direct local)"
    result=oc.generate("Untrusted specimen",policy="unknown")
    assert result["ok"] and result["provider"]=="ollama"
    assert result["openclaw_event_id"] is None and result["untrusted"] is True
    path,body=trap["calls"][-1]
    assert path=="/api/generate" and body["stream"] is False and "tools" not in body
    assert body["options"]=={"num_predict":1024,"num_ctx":4096}
    assert result["policy"]==oc.keel_policy.enforced_policy("unknown")

@pytest.mark.parametrize("model",["test:cloud","test-cloud","cloud/model","registry.test/model","bad model"])
def test_cloud_or_ambiguous_model_never_sends_evidence(trap,model):
    assert not oc.generate("Private specimen",model=model)["ok"]
    assert trap["calls"]==[]

@pytest.mark.parametrize("mode",["remote","no_weights","redirect"])
def test_metadata_gate_prevents_evidence_transport(trap,mode):
    trap["mode"]=mode
    assert not oc.generate("Private specimen")["ok"]
    assert len(trap["calls"])==1 and trap["calls"][0][0]=="/api/show"
    assert "Private specimen" not in json.dumps(trap["calls"])

@pytest.mark.parametrize("mode",["partial","length","empty","bad_shape"])
def test_incomplete_or_malformed_generation_cannot_be_reported_success(trap,mode):
    trap["mode"]=mode
    result=oc.generate("Private specimen")
    assert not result["ok"] and not result["response"]

def test_no_local_ledger_event_is_reused_for_direct_analysis(trap,monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui
    monkeypatch.setattr(ui.ledger,"latest_event_id",lambda:"stale-openclaw-event")
    monkeypatch.setattr(ui,"STORE",None)
    ui.GENERIC_JOBS.clear()
    client=TestClient(ui.app)
    response=client.post("/analyze",data={"evidence":"Synthetic payment change from vendor@example.invalid."})
    assert response.status_code==200
    assert len(ui.GENERIC_JOBS)==1
    token,job=next(iter(ui.GENERIC_JOBS.items()))
    assert token!="stale-openclaw-event"
    p=job["decision_participant"]
    assert p["framework"]=="Ollama native API (direct local)" and p["runtime_event_id"] is None
