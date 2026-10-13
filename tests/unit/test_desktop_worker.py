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
    keys = ("NODE_UI_DB", "NODE_UI_MEDIA_LEDGER", "NODE_UI_LEDGER", "NODE_UI_DATA_DIR")
    before_env = {key: os.environ.get(key) for key in keys}
    yield _load_worker()
    sys.path[:] = before_path
    for key, value in before_env.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value


# ---------------------------------------------------------------- unit

def test_validate_repo_accepts_actual_layout(worker):
    assert worker.validate_repo(REPO) == REPO.resolve()


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


def test_worker_lifecycle_ready_http_sigterm(tmp_path):
    proc = _launch(tmp_path)
    try:
        url = _read_ready(proc)
        port = int(url.rsplit(":", 1)[1])
        import urllib.request
        with urllib.request.urlopen(url +
                "/api/samples/auth_failed_login_burst", timeout=10) as resp:
            assert resp.status == 200
            resp.read()
        # SIGTERM: process exits within 8s and port becomes unreachable
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=8)
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
