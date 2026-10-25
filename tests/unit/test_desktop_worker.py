"""Lifecycle tests for the Verity desktop worker.

Unit tests exercise validate_repo / bind_server_socket / setup_environment
without importing the swarmtracker app. Integration tests launch the real
worker as a subprocess against the actual repository layout and verify
readiness JSON, an authenticated-failure endpoint over HTTP, and SIGTERM
shutdown. No network beyond loopback, no model or cloud calls.
"""
import importlib.util
import json
import os
import plistlib
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER = REPO / "apps" / "desktop" / "worker.py"
SENTINEL = "Verity backend startup failed; verify repository and Python dependencies."


def _load_worker():
    spec = importlib.util.spec_from_file_location("verity_worker", WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def worker():
    before_path = list(sys.path)
    keys = ("NODE_UI_DB", "NODE_UI_MEDIA_LEDGER", "NODE_UI_LEDGER", "NODE_UI_DATA_DIR", "NODE_UI_PRODUCT_DB")
    before_env = {key: os.environ.get(key) for key in keys}
    yield _load_worker()
    sys.path[:] = before_path
    for key, value in before_env.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value


# ---------------------------------------------------------------- unit

def test_validate_repo_accepts_actual_layout(worker):
    assert worker.validate_repo(REPO) == REPO.resolve()

def test_frozen_worker_reads_own_bundle_version(worker,tmp_path):
    contents=tmp_path/"Synthetic.app/Contents"
    binary=contents/"Resources/backend/verity-worker"
    binary.parent.mkdir(parents=True)
    binary.write_text("synthetic")
    with (contents/"Info.plist").open("wb") as out:
        plistlib.dump({"CFBundleShortVersionString":"0.3.4"},out)
    assert worker.bundled_version(binary)=="0.3.4"


def test_validate_repo_missing_app_raises_valueerror(worker, tmp_path):
    with pytest.raises(ValueError):
        worker.validate_repo(tmp_path)
    (tmp_path / "apps").mkdir()
    with pytest.raises(ValueError):
        worker.validate_repo(tmp_path)  # apps exists, app.py missing


def test_validate_repo_invalid_path_raises(worker, tmp_path):
    with pytest.raises(ValueError):
        worker.validate_repo(tmp_path / "does_not_exist")


def test_bind_server_socket_loopback_ephemeral_distinct(worker):
    s1 = worker.bind_server_socket()
    try:
        s2 = worker.bind_server_socket()
        try:
            for s in (s1, s2):
                host, port = s.getsockname()
                assert host == "127.0.0.1"
                assert port != 0
            assert s1.getsockname()[1] != s2.getsockname()[1]
        finally:
            s2.close()
    finally:
        s1.close()


def test_setup_environment_paths_before_app_import(worker, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-kept")
    monkeypatch.setenv("NODE_UI_OPENCLAW_URL", "http://127.0.0.1:9/openclaw")
    prior_app = sys.modules.get("swarmtracker_node_ui.app")
    worker.setup_environment(REPO)
    base = fake_home / "Library/Application Support/Verity/data"
    assert os.environ["NODE_UI_DATA_DIR"] == str(base)
    assert os.environ["NODE_UI_DB"] == str(base / "verity.db")
    assert os.environ["NODE_UI_MEDIA_LEDGER"] == str(base / "media-ledger.jsonl")
    assert os.environ["NODE_UI_LEDGER"] == str(base / "runtime-ledger.jsonl")
    assert base.is_dir()
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "proj-kept"
    assert os.environ["NODE_UI_OPENCLAW_URL"] == "http://127.0.0.1:9/openclaw"
    assert str(REPO) in sys.path and str(REPO / "apps" / "node") in sys.path
    assert sys.modules.get("swarmtracker_node_ui.app") is prior_app  # no new app import


# ---------------------------------------------------------- integration

def test_profile_queue_survives_finder_launch_directory(worker, tmp_path, monkeypatch):
    monkeypatch.chdir("/")
    monkeypatch.setenv("VERITY_DATA_HOME", str(tmp_path / "retained"))
    monkeypatch.delenv("NODE_UI_PRODUCT_DB", raising=False)
    monkeypatch.delenv("NODE_UI_COORDINATOR_DB", raising=False)
    worker.setup_environment(REPO)
    assert os.environ["NODE_UI_PRODUCT_DB"] == str(tmp_path / "retained/product-work.db")
    from swarmtracker_node_ui.session_coordinator import Coordinator
    Coordinator(os.environ["NODE_UI_PRODUCT_DB"]).snapshot()
    assert (tmp_path / "retained/product-work.db").is_file()


@pytest.mark.parametrize("binding", ["NODE_UI_PRODUCT_DB", "NODE_UI_COORDINATOR_DB"])
def test_profile_queue_preserves_explicit_binding(worker, tmp_path, monkeypatch, binding):
    monkeypatch.setenv("VERITY_DATA_HOME", str(tmp_path / "retained"))
    monkeypatch.delenv("NODE_UI_PRODUCT_DB", raising=False)
    monkeypatch.delenv("NODE_UI_COORDINATOR_DB", raising=False)
    expected = str(tmp_path / "existing-queue.db")
    monkeypatch.setenv(binding, expected)
    worker.setup_environment(REPO)
    assert os.environ["NODE_UI_PRODUCT_DB"] == expected

uvicorn = pytest.importorskip("uvicorn")


def _read_ready(proc, deadline=30.0):
    end = time.monotonic() + deadline
    buf = b""
    while time.monotonic() < end and proc.poll() is None:
        r, _, _ = select.select([proc.stdout], [], [], 0.5)
        if r:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            buf += chunk
            for line in buf.splitlines():
                line = line.strip()
                if line.startswith(b"{"):
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if "url" in obj:
                        return obj["url"]
    raise AssertionError("worker never emitted readiness JSON: %r" % buf[-500:])


def _launch(tmp_path, repo=REPO):
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")          # isolated HOME
    env["NODE_UI_SDK_PROJECT"] = ""               # empty sdk project
    env.pop("GOOGLE_CLOUD_PROJECT", None)
    return subprocess.Popen(
        [sys.executable, str(WORKER), "--repo", str(repo)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(tmp_path))


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_worker_lifecycle_ready_http_sigterm(tmp_path, stop_signal):
    proc = _launch(tmp_path)
    try:
        url = _read_ready(proc)
        port = int(url.rsplit(":", 1)[1])
        import urllib.request
        with urllib.request.urlopen(url +
                "/api/samples/auth_failed_login_burst", timeout=10) as resp:
            assert resp.status == 200
            resp.read()
        # Both ordinary termination and terminal interrupt release the port.
        proc.send_signal(stop_signal)
        proc.wait(timeout=8)
        assert b"Traceback" not in proc.stderr.read()
        with pytest.raises((ConnectionError, OSError, urllib.error.URLError)):
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            raise AssertionError("port still accepting connections")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_worker_invalid_repo_fails_without_sentinel_leak(tmp_path):
    bad = tmp_path / "not_a_repo"
    bad.mkdir()
    proc = _launch(tmp_path, repo=bad)
    try:
        rc = proc.wait(timeout=15)
        assert rc != 0
        out = proc.stdout.read()
        assert b"\"url\"" not in out  # no success JSON
        err = proc.stderr.read().decode("utf-8", "replace")
        assert SENTINEL in err
        for secret in ("GOOGLE_", "NODE_UI_", "OPENCLAW", "evidence",
                       "media-ledger", "runtime-ledger"):
            assert secret not in err
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_worker_http_intake_exports_and_persistence(tmp_path, monkeypatch, synthetic_runtime):
    """Actual worker, HTTP forms, exports, disk restart; no live providers."""
    import re
    import urllib.request
    import urllib.parse
    runtime_url, calls = synthetic_runtime
    monkeypatch.setenv("NODE_UI_OPENCLAW_URL", runtime_url)
    monkeypatch.setenv("NODE_UI_OLLAMA_URL", runtime_url)
    monkeypatch.setenv("NODE_UI_HEALTH_TIMEOUT", "0.1")
    monkeypatch.setenv("NODE_UI_GENERATE_TIMEOUT", "0.1")
    proc = _launch(tmp_path)
    def get(path):
        with urllib.request.urlopen(url + path, timeout=15) as response:
            return response.read(), response.headers
    def post(path, fields):
        request = urllib.request.Request(url + path,
            data=urllib.parse.urlencode(fields).encode())
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read(), response.headers, response.url
    try:
        url = _read_ready(proc)
        for page in ("/", "/analyze", "/media", "/governance", "/settings", "/investigations"):
            body, _ = get(page)
            assert b"<html" in body.lower()
        health = json.loads(get("/healthz")[0])
        assert health["runtime_online"] is True
        assert health["multimodal_escalation_ready"] is False
        sample = json.loads(get("/api/samples/auth_failed_login_burst")[0])
        assert isinstance(sample, dict) and sample
        for selection in ("", "true", "1"):
            html, _, _ = post("/analyze", {
                "evidence": "Synthetic test specimen <script>alert('specimen')</script>",
                "uploaded_filename": "synthetic.txt",
                "escalate_multimodal": selection})
            assert b"<script>alert('specimen')</script>" not in html
            match = re.search(rb'/report/([^/"?]+)/json', html)
            assert match, "intake did not expose a JSON report"
            token = match.group(1).decode()
            payload, headers = get("/report/" + token + "/json")
            report = json.loads(payload)
            assert "attachment" in headers["Content-Disposition"]
            assert report["execution"]["provider_attempted"] is False
            assert report["execution"]["state"] in (
                "local_only", "unavailable", "denied")
            md, _ = get("/report/" + token + "/report")
            assert report["execution"]["state"].encode() in md
        assert calls and all(c["execution_location"] == "local" and c["evidence_boundary_enforced"] is True for c in calls)
        _, _, project_url = post("/investigations/new", {"name": "Synthetic durable worker test"})
        project_path = urllib.parse.urlsplit(project_url).path
        assert project_path.startswith("/investigations/")
        exported = json.loads(get(project_path + "/export")[0])
        assert exported
        proc.terminate()
        proc.wait(timeout=8)
        proc = _launch(tmp_path)
        url = _read_ready(proc)
        assert b"Synthetic durable worker test" in get(project_path)[0]
        restarted_export = json.loads(get(project_path + "/export")[0])
        exported.pop("exported_at", None)
        restarted_export.pop("exported_at", None)
        assert restarted_export == exported
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=8)


@pytest.fixture
def synthetic_runtime():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send_json(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            self.send_json({"models": [{"name": "qwen2.5:7b-instruct"}]}
                if self.path == "/api/tags" else {"ok": True, "provider": "synthetic"})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            self.send_json({"ok": True, "provider": "synthetic", "response": json.dumps({
                "risk_level": "low", "confidence": 10,
                "verdict": "Synthetic local assessment", "signals": [],
                "recommended_action": "Corroborate locally"})})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
