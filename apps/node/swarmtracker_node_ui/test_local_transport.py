"""Real loopback traps verify destination, redirect, and proxy boundaries."""
import json
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swarmtracker_node_ui import config, openclaw_client as oc

@pytest.mark.parametrize("url", [
    "http://127.0.0.1", "http://localhost:8090", "http://LOCALHOST:80/base",
    "http://127.0.0.1:65535/private/runtime", "http://localhost/base%20path",
])
def test_exact_loopback_accepts_safe_base_paths(url):
    assert config.assert_local(url) == url

@pytest.mark.parametrize("url", [
    None, 4, "", "https://127.0.0.1", "http://127.0.0.1.evil.test",
    "http://localhost.evil.test", "http://localhost:8090@evil.test",
    "http://secret:password@127.0.0.1", "http://127.0.0.1:abc",
    "http://127.0.0.1:0", "http://localhost:65536", "http://localhost:",
    "http://localhost:80/space path", "http://localhost:80/line\npath",
    "http://localhost:80/tab\tpath", "http://localhost:80/?",
    "http://localhost:80/#", "http://localhost:80/?q=1",
    "http://localhost:80/#fragment", "http://%6cocalhost:80",
    "http://127.0.0.1\\@evil.test", "http://[::1]:8090",
    " http://localhost", "http://localhost/\x7f", "http://localhost/\u00a0",
])
def test_nonlocal_or_ambiguous_urls_are_safely_rejected(url):
    with pytest.raises(ValueError) as error:
        config.assert_local(url)
    assert str(error.value) == "Invalid or non-loopback runtime URL refused by SwarmTracker Node UI"
    assert error.value.__cause__ is None

@pytest.fixture
def http_traps():
    state = {"redirect": False, "runtime": [], "trap": []}
    servers = []
    def start(trap):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.handle_request()
            def do_POST(self): self.handle_request()
            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                state["trap" if trap else "runtime"].append((self.path, raw))
                if not trap and state["redirect"]:
                    self.send_response(307)
                    self.send_header("Location", trap_url + "/redirect-target")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = ({"models": [{"name": "synthetic"}]} if self.path.endswith("/api/tags")
                        else {"ok": True, "response": "synthetic response", "provider": "synthetic"})
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return "http://127.0.0.1:" + str(server.server_port)
    trap_url = start(True)
    runtime_url = start(False)
    try:
        yield state, runtime_url, trap_url
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

def configure(monkeypatch, runtime_url):
    monkeypatch.setattr(config, "OPENCLAW_URL", runtime_url + "/base")
    monkeypatch.setattr(config, "OLLAMA_URL", runtime_url + "/base")

def test_runtime_calls_ignore_inherited_proxy_and_preserve_policy(monkeypatch, http_traps):
    state, runtime_url, trap_url = http_traps
    configure(monkeypatch, runtime_url)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, trap_url)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert oc.healthz()["ok"] is True
    assert oc.ollama_models() == ["synthetic"]
    result = oc.generate("Synthetic specimen", policy="unrecognized-test-policy")
    assert result["ok"] is True and result["response"] == "synthetic response"
    assert len(state["runtime"]) == 3 and state["trap"] == []
    path, raw = state["runtime"][-1]
    assert path == "/base/v0/generate"
    body = json.loads(raw)
    assert body["policy"] == oc.keel_policy.enforced_policy("unrecognized-test-policy")
    assert body["execution_location"] == "local"
    assert body["evidence_boundary_enforced"] is True
    assert body["source_document_role"] == "specimen"

def test_runtime_redirects_are_refused_without_replaying_evidence(monkeypatch, http_traps):
    state, runtime_url, _ = http_traps
    configure(monkeypatch, runtime_url)
    state["redirect"] = True
    assert oc.healthz() == {}
    assert oc.ollama_models() == []
    result = oc.generate("Synthetic private specimen")
    assert result["ok"] is False
    assert "Synthetic private specimen" not in result["error"]
    assert len(state["runtime"]) == 3 and state["trap"] == []

def test_runtime_override_is_revalidated_before_each_request(monkeypatch, http_traps):
    state, runtime_url, _ = http_traps
    configure(monkeypatch, runtime_url)
    monkeypatch.setattr(config, "OPENCLAW_URL", "http://localhost:80@evil.test")
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1.evil.test")
    assert oc.healthz() == {}
    assert oc.ollama_models() == []
    assert oc.generate("Synthetic private specimen")["ok"] is False
    assert state["runtime"] == [] and state["trap"] == []
