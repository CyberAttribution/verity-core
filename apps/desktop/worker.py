"""Local desktop HTTP worker; never starts or selects an inference model."""
import argparse
import asyncio
import json
import os
import signal
import socket
import sys
from pathlib import Path

def validate_repo(repo):
    repo = Path(repo).resolve()
    if not (repo / "apps/node/swarmtracker_node_ui/app.py").is_file():
        raise ValueError("repository does not contain the Node application")
    return repo

def bind_server_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.set_inheritable(False)
        return sock
    except BaseException:
        sock.close()
        raise

def setup_environment(repo):
    # Keep application data outside the repo/bundle. Inference remains an
    # independently configured service; existing SDK environment is inherited.
    base = Path(os.environ.get("VERITY_DATA_HOME", str(Path.home() / "Library/Application Support/Verity/data")))
    base.mkdir(parents=True, exist_ok=True)
    for key, name in (("NODE_UI_DB", "verity.db"),
                      ("NODE_UI_MEDIA_LEDGER", "media-ledger.jsonl"),
                      ("NODE_UI_LEDGER", "runtime-ledger.jsonl")):
        os.environ[key] = (os.environ.get("VERITY_READONLY_RUNTIME_LEDGER", str(base / name))
                           if key == "NODE_UI_LEDGER" else str(base / name))
    os.environ["NODE_UI_DATA_DIR"] = str(base)
    sys.path[:0] = [str(repo), str(repo / "apps/node")]

def emit_ready(url):
    print(json.dumps({"url": url, "nonce": os.environ.get("VERITY_DESKTOP_NONCE", "")}), flush=True)

async def serve(sock):
    import uvicorn
    from swarmtracker_node_ui import setup_routes
    if setup_routes.path().exists():
        setup_routes.apply_settings(setup_routes.state()["settings"])
    from swarmtracker_node_ui.app import app
    server = uvicorn.Server(uvicorn.Config(
        app, log_level="warning", access_log=False,
        timeout_graceful_shutdown=3))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if task.done():
            await task
            raise RuntimeError("backend exited before readiness")
        await asyncio.sleep(0.02)
    emit_ready(f"http://127.0.0.1:{sock.getsockname()[1]}")
    await task

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument("--bundled", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.bundled:
            if not getattr(sys, "frozen", False):
                raise ValueError("Bundled mode requires a frozen backend")
            repo = Path(sys._MEIPASS)
        else:
            repo = validate_repo(args.repo)
        setup_environment(repo)
        if args.bundled:
            import platform
            os.environ["VERITY_REQUIRE_ACTIVATION"] = "1"
            os.environ["VERITY_ACTIVATION_PUBLIC_KEY"] = str(Path(sys.executable).parent.parent / "activation-public.pem")
            os.environ.setdefault("NODE_UI_NODE", socket.gethostname())
            os.environ.setdefault("NODE_UI_HARDWARE", platform.machine() + " Mac")
            os.environ.setdefault("NODE_UI_HARDWARE_NEXT", "Not configured")
        # CLI launches can inherit ignored SIGINT from a background shell.
        # Restore ordinary interruption; uvicorn handles graceful shutdown.
        signal.signal(signal.SIGINT, signal.default_int_handler)
        with bind_server_socket() as sock:
            asyncio.run(serve(sock))
        return 0
    except KeyboardInterrupt:
        return 130  # graceful terminal interruption, without raw traceback
    except Exception:
        # Native shell shows actionable generic diagnostics; no credential,
        # evidence, environment dump or raw provider exception is printed.
        print("Verity backend startup failed; verify repository and Python dependencies.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
