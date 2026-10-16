import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from swarmtracker_node_ui import config,runtime_diagnostics as diagnostics,app as ui,setup_routes,decision_routes

@pytest.fixture
def metadata(monkeypatch,tmp_path):
    state={"calls":[],"models":["test:local"],"health":True}
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            state["calls"].append(self.path)
            if self.path=="/healthz":body={"status":"ok"} if state["health"] else {}
            elif self.path=="/api/version":body={"version":"test"} if state["health"] else {}
            elif self.path=="/api/tags":body={"models":[{"name":m} for m in state["models"]]}
            else:raise AssertionError("Connection diagnostics attempted non-metadata operation")
            raw=json.dumps(body).encode();self.send_response(200);self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(("127.0.0.1",0),H);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url="http://127.0.0.1:"+str(server.server_port)
    for name,value in [("OPENCLAW_URL",url),("OLLAMA_URL",url),("DEFAULT_MODEL","test:local"),("RUNTIME_KIND","openclaw")]:
        monkeypatch.setattr(config,name,value)
    monkeypatch.setattr(setup_routes,"path",lambda:tmp_path/"installation.json")
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(3)

@pytest.mark.parametrize("kind",["openclaw","ollama"])
def test_connection_test_distinguishes_runtime_inventory_and_model_without_inference(metadata,monkeypatch,kind):
    monkeypatch.setattr(config,"RUNTIME_KIND",kind)
    assert diagnostics.check()["state"]=="metadata_ready"
    metadata["models"]=[]
    assert diagnostics.check()["state"]=="model_missing"
    metadata["health"]=False
    assert diagnostics.check()["state"]=="runtime_unreachable"
    assert set(metadata["calls"]).issubset({"/healthz","/api/version","/api/tags"})

def test_connection_test_is_local_operator_form_and_does_not_mutate_activation(metadata):
    client=TestClient(ui.app,base_url="http://127.0.0.1")
    before=setup_routes.state()
    assert client.post("/setup",data={"action":"test_connection"}).status_code==403
    response=client.post("/setup",data={"action":"test_connection","operator_token":decision_routes.TOKEN})
    assert response.status_code==200 and "No model was loaded" in response.text
    assert "selected model is listed" in response.text
    assert setup_routes.state()==before
    assert client.post("/setup",data={"action":"test_connection","operator_token":decision_routes.TOKEN},headers={"origin":"https://evil.invalid"}).status_code==403

def test_setup_error_is_actionable_html_and_preserves_existing_state(metadata):
    client=TestClient(ui.app,base_url="http://127.0.0.1")
    before=setup_routes.state()
    response=client.post("/setup",data={"action":"activate","code":"invalid","operator_token":decision_routes.TOKEN})
    assert response.status_code==422 and "The change was not saved" in response.text
    assert setup_routes.state()==before
