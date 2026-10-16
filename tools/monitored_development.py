"""Bounded developer harness with metadata-only local telemetry.

Uses an operator-established loopback SSH tunnel for model requests. GPU
collection executes only a fixed nvidia-smi query over SSH. Optional --check
runs an explicit argv without a shell. Telemetry does not govern that command.
This harness does not execute model-generated tools or code automatically.
"""
import argparse
import csv
import json
import os
import pathlib
import signal
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit


def loopback(value):
    p = urlsplit(value)
    if p.scheme != "http" or p.hostname != "127.0.0.1" or p.username or p.password or p.query or p.fragment or p.path not in ("", "/"):
        raise ValueError("Use an exact HTTP 127.0.0.1 origin")
    if not p.port or not 1 <= p.port <= 65535:
        raise ValueError("Explicit loopback port required")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class Collector:
    def __init__(self, origin, token_path, manifest):
        self.origin = loopback(origin)
        token_path = pathlib.Path(token_path)
        fd=os.open(token_path,os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077 or info.st_uid!=os.getuid():
                raise ValueError("Telemetry token file must be private and owned by this user")
            raw=os.read(fd,4097)
        finally:os.close(fd)
        self.token=raw.decode('ascii').strip()
        if not 32<=len(self.token)<=256 or len(raw)>4096: raise ValueError("Invalid collector token")
        self.manifest = manifest
        self.failed = threading.Event()
        self.errors = []

    def emit(self, kind, data=None):
        envelope = dict(manifest=self.manifest, event=dict(event_id="evt-"+uuid.uuid4().hex, kind=kind, data=data or {}))
        req = urllib.request.Request(self.origin + "/api/monitor/events", data=json.dumps(envelope).encode(),
              headers={"Content-Type":"application/json", "Authorization":"Bearer " + self.token})
        try:
            with OPENER.open(req, timeout=10) as response: json.load(response)
        except Exception as e:
            self.failed.set(); self.errors.append(type(e).__name__)
            raise RuntimeError("Telemetry recording failed; activity will not start or be reported complete") from None


def gpu_sample(ssh_host, alias):
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes"]
    if alias: command += ["-o", "HostKeyAlias=" + alias]
    # Host must not be an option; subprocess never invokes a local shell.
    if not ssh_host or ssh_host.startswith("-") or any(c.isspace() for c in ssh_host):
        raise ValueError("Invalid SSH host")
    command += [ssh_host, "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=True)
    gpus = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) != 5: raise ValueError("Unexpected GPU reading")
        gpus.append(dict(index=int(row[0]), name=row[1].strip(), memory_used_mib=float(row[2]), memory_total_mib=float(row[3]), utilization_percent=float(row[4])))
    return {"gpus":gpus}


def completion(origin, payload):
    """Stream real output; timing includes transport, not just GPU decoding."""
    started = time.monotonic(); first = None; first_content = None; content = []; reasoning = []; usage = None; finish = None
    payload = dict(payload, stream=True, stream_options={"include_usage":True})
    req = urllib.request.Request(loopback(origin)+"/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    with OPENER.open(req, timeout=240) as response:
        for raw in response:
            if not raw.startswith(b"data: "): continue
            line = raw[6:].strip()
            if line == b"[DONE]": break
            item = json.loads(line)
            if item.get("usage"): usage = item["usage"]
            for choice in item.get("choices", []):
                delta = choice.get("delta", {})
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                token = delta.get("content") or thought
                if token and first is None: first = time.monotonic()
                if delta.get("content") and first_content is None: first_content=time.monotonic()
                if delta.get("content"): content.append(delta["content"])
                if thought: reasoning.append(thought)
                finish = choice.get("finish_reason") or finish
    elapsed = time.monotonic()-started
    if finish is None: raise RuntimeError("IncompleteStream")
    generated = (usage or {}).get("completion_tokens")
    return dict(content="".join(content), reasoning="".join(reasoning), finish_reason=finish,
                usage=usage, elapsed_seconds=elapsed, ttft_seconds=None if first is None else first-started,
                first_content_seconds=None if first_content is None else first_content-started,
                completion_tokens_per_total_second=generated/elapsed if generated else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-url", required=True); parser.add_argument("--token-file", required=True)
    parser.add_argument("--model-url", required=True); parser.add_argument("--model", default="glm53-w4-mtp")
    parser.add_argument("--input", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", default="dev-"+uuid.uuid4().hex)
    parser.add_argument("--task", default="Bounded development review")
    parser.add_argument("--ssh-host"); parser.add_argument("--host-key-alias")
    parser.add_argument("--check", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    loopback(args.model_url)
    payload = json.loads(pathlib.Path(args.input).read_text())
    payload["model"] = args.model
    output = pathlib.Path(args.output)
    manifest = dict(run_id=args.run_id, agent_id="verity-development-harness", name="Verity development harness", runtime="bounded-harness/v1", model=args.model)
    collector = Collector(args.monitor_url, args.token_file, manifest)
    # Validate collector configuration, then reserve before a billable request.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd,json.dumps({'run_id':args.run_id,'status':'in_progress'}).encode());os.fsync(fd)
    stop = threading.Event(); result = {"run_id":args.run_id, "manifest":manifest}
    class HarnessInterrupted(Exception):pass
    def interrupted(signum,frame):raise HarnessInterrupted()
    previous_handlers={sig:signal.signal(sig,interrupted) for sig in (signal.SIGINT,signal.SIGTERM)}
    def periodic():
        while not stop.wait(8):
            try:
                if args.ssh_host: collector.emit("gpu_sample", gpu_sample(args.ssh_host,args.host_key_alias))
                else: collector.emit("heartbeat")
            except Exception as e:
                collector.failed.set(); collector.errors.append(type(e).__name__)
                stop.set()
    try:
        collector.emit("heartbeat")
        if args.ssh_host: collector.emit("gpu_sample", gpu_sample(args.ssh_host,args.host_key_alias))
        thread = threading.Thread(target=periodic,daemon=True); thread.start()
        if args.check:
            activity = "check-"+uuid.uuid4().hex; started=time.monotonic()
            collector.emit("tool_start",dict(activity_id=activity,operation="explicit_development_check",task=args.task))
            proc = subprocess.run(args.check, capture_output=True, text=True, timeout=300)
            result["check"] = dict(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
            collector.emit("tool_finish",dict(activity_id=activity,status="success" if proc.returncode==0 else "failure",duration_ms=(time.monotonic()-started)*1000))
            if proc.returncode: raise RuntimeError("DevelopmentCheckFailed")
        if collector.failed.is_set(): raise RuntimeError("TelemetryUnavailable")
        activity="request-"+uuid.uuid4().hex; started=time.monotonic()
        collector.emit("request_start",dict(activity_id=activity,operation="chat_completion",task=args.task))
        try:
            result["completion"] = completion(args.model_url,payload)
        except Exception as e:
            collector.emit("request_finish",dict(activity_id=activity,status="failure",duration_ms=(time.monotonic()-started)*1000,error_class=type(e).__name__))
            raise
        if result["completion"]["finish_reason"] == "length":
            collector.emit("request_finish",dict(activity_id=activity,status="failure",duration_ms=(time.monotonic()-started)*1000,error_class="OutputLimitReached"))
            raise RuntimeError("OutputLimitReached")
        collector.emit("request_finish",dict(activity_id=activity,status="success",duration_ms=(time.monotonic()-started)*1000))
        if collector.failed.is_set(): raise RuntimeError("TelemetryIncomplete")
        result["status"]="completed"
    except Exception as e:
        result["status"]="failed"; result["error_class"]=type(e).__name__
    finally:
        for sig,handler in previous_handlers.items():signal.signal(sig,handler)
        stop.set()
        if 'thread' in locals(): thread.join(timeout=15)
        try: collector.emit("disconnect")
        except Exception: pass
        if collector.failed.is_set() and result.get("status")=="completed":
            result["status"]="failed";result["error_class"]="TelemetryIncomplete"
        result["telemetry_errors"]=collector.errors
        # Replace our reserved placeholder only after a complete, synced write.
        import tempfile
        os.close(fd)
        tempfd,name=tempfile.mkstemp(prefix=".development-result-",dir=output.parent)
        try:
            with os.fdopen(tempfd,"w") as f:
                json.dump(result,f,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(name,output)
        finally:
            if os.path.exists(name):os.unlink(name)
    print(json.dumps({"run_id":args.run_id,"status":result["status"],"output":str(output),"telemetry_complete":not collector.failed.is_set()}))
    return 0 if result["status"]=="completed" else 1


if __name__ == "__main__": raise SystemExit(main())
