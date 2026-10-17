"""Append-only, local observations of billable development sessions.

This module observes work; it has no provider credentials or execution path.
"""
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from . import store

DB_PATH = Path(os.environ.get("NODE_UI_SESSIONS_DB", str(store.DB_PATH.with_name("development-sessions.db"))))
KINDS = {"provider", "connection", "inventory", "model", "task", "request", "result", "outcome", "contact", "billing", "shutdown", "resource", "gpu", "note"}
SOURCES = {"provider_api", "provider_console", "public_pricing", "ssh", "model_server", "local_collector", "operator", "test"}
STATES = {"provisioning", "running", "stopping", "terminated", "unknown"}
MODEL_STATES = {"unknown", "absent", "downloading", "downloaded", "loading", "ready", "inference", "failed"}
TASK_STATES = {"ready", "running", "blocked", "completed", "verified"}
OUTCOMES = {"proposed", "queued", "submitted", "acknowledged", "completed", "independently_verified", "failed", "unresolved"}
NEXT_OUTCOME = {None: {"proposed", "queued"}, "proposed": {"queued", "submitted", "failed", "unresolved"},
                "queued": {"submitted", "failed", "unresolved"}, "submitted": {"acknowledged", "failed", "unresolved"},
                "acknowledged": {"completed", "failed", "unresolved"}, "completed": {"independently_verified", "failed", "unresolved"},
                "unresolved": {"acknowledged", "completed", "failed"}, "failed": set(), "independently_verified": set()}


def _text(value, limit=240):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid session text")
    return value


def validate_event(event):
    if not isinstance(event, dict) or set(event) != {"id", "at", "kind", "source", "data"}:
        raise ValueError("Invalid session event fields")
    _text(event["id"], 100)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", event["id"]):
        raise ValueError("Invalid event ID")
    if event["kind"] not in KINDS or event["source"] not in SOURCES:
        raise ValueError("Unknown event kind or source")
    if not isinstance(event["at"], (int, float)) or isinstance(event["at"], bool) or not 0 < event["at"] < 4102444800:
        raise ValueError("Invalid observation time")
    data = event["data"]
    if not isinstance(data, dict) or len(json.dumps(data, allow_nan=False)) > 8000:
        raise ValueError("Invalid observation data")
    allowed = {
        "provider": {"state", "instance_id", "instance_type", "region", "gpu", "ip", "ssh_key_label"},
        "connection": {"state", "detail"}, "inventory": {"checked", "models", "download_bytes"},
        "model": {"state", "name", "revision", "download_bytes", "detail"},
        "task": {"task_id", "title", "state", "scope", "run_id", "action_id", "commit", "test", "output", "verification"},
        "request": {"task_id", "state", "request_id", "finish_reason"},
        "result": {"task_id", "state", "output", "verification", "finish_reason"},
        "outcome": {"task_id", "state", "result_id", "evidence", "verifier", "detail"},
        "contact": {"state"}, "billing": {"rate", "currency", "billing_start", "estimate_start_proxy", "rate_as_of", "ceiling", "reported_charge", "charge_as_of", "exclusions"},
        "shutdown": {"requested", "confirmed"}, "resource": {"name", "state", "billable"},
        "gpu": {"index", "uuid", "utilization_pct", "memory_used_mib", "memory_total_mib"},
        "note": {"message", "corrects"},
    }[event["kind"]]
    if not data or not set(data) <= allowed:
        raise ValueError("Unknown or empty observation fields")
    for value in data.values():
        if isinstance(value, str):
            _text(value, 500)
        elif isinstance(value, list):
            if len(value) > 100 or not all(isinstance(v, str) and 0 < len(v) <= 240 for v in value):
                raise ValueError("Invalid observation list")
        elif not isinstance(value, (int, float, bool)) or (isinstance(value, float) and not 0 <= value < 1e12):
            raise ValueError("Invalid observation value")
    if event["kind"] == "provider" and "state" in data and data["state"] not in STATES:
        raise ValueError("Invalid provider state")
    if event["kind"] == "model" and "state" in data and data["state"] not in MODEL_STATES:
        raise ValueError("Invalid model state")
    if event["kind"] == "task" and data.get("state") not in TASK_STATES:
        raise ValueError("Invalid task state")
    if event["kind"] == "task" and (not data.get("task_id") or not data.get("title") or data.get("scope") not in {"gpu", "local"}):
        raise ValueError("Task requires identity, title and scope")
    if event["kind"] in {"task", "request", "result", "outcome"} and "task_id" in data and (not isinstance(data["task_id"], str) or not re.fullmatch(r"[A-Za-z0-9._:-]+", data["task_id"])):
        raise ValueError("Invalid task ID")
    if event["kind"] == "task" and data.get("scope") == "gpu" and data.get("state") == "verified":
        raise ValueError("GPU task verification requires a separate outcome observation")
    if event["kind"] in {"request", "result"} and not data.get("task_id"):
        raise ValueError("Request or result requires a task ID")
    if event["kind"] == "resource" and not data.get("name"):
        raise ValueError("Resource requires a name")
    if event["kind"] == "gpu":
        if event["source"] not in {"ssh", "local_collector", "test"} or type(data.get("index")) is not int or not 0 <= data["index"] < 32 or not isinstance(data.get("uuid"),str) or not data["uuid"].startswith("GPU-"):
            raise ValueError("GPU sample requires identity and collector source")
        for name,limit in (("utilization_pct",100),("memory_used_mib",1e7),("memory_total_mib",1e7)):
            if type(data.get(name)) not in (int,float) or not 0 <= data[name] <= limit:
                raise ValueError("Invalid GPU measurement")
    if event["kind"] == "model" and data.get("state") not in MODEL_STATES:
        raise ValueError("Model requires a valid state")
    if event["kind"] == "request" and data.get("state") not in {"running", "completed", "failed"}:
        raise ValueError("Request requires a valid state")
    if event["kind"] == "result" and data.get("finish_reason") not in {"stop", "length", "error", "unknown"}:
        raise ValueError("Result requires a finish reason")
    if event["kind"] == "result" and (data.get("state") not in {"completed", "failed"} or event["source"] != "model_server"):
        raise ValueError("Result requires a model-server observation and valid state")
    if event["kind"] == "outcome":
        if not data.get("task_id") or data.get("state") not in OUTCOMES:
            raise ValueError("Outcome requires task and valid state")
        if data["state"] in {"completed", "independently_verified"} and not data.get("result_id"):
            raise ValueError("Completed outcome requires result ID")
        if data["state"] == "independently_verified" and (not data.get("verifier") or not data.get("evidence") or event["source"] not in {"operator", "test", "local_collector"}):
            raise ValueError("Independent verification requires reviewer and evidence")
    if event["kind"] == "inventory" and not isinstance(data.get("checked"), bool):
        raise ValueError("Inventory requires successful-check flag")
    if event["kind"] == "inventory" and data["checked"] and "models" not in data:
        raise ValueError("Successful inventory requires model list")
    if event["kind"] == "billing" and "rate" in data and (not isinstance(data["rate"], (int, float)) or isinstance(data["rate"], bool) or not 0 <= data["rate"] <= 100000):
        raise ValueError("Invalid rate")
    for field in ("ceiling", "reported_charge"):
        if event["kind"] == "billing" and field in data and (not isinstance(data[field], (int, float)) or isinstance(data[field], bool) or not 0 <= data[field] <= 1e12):
            raise ValueError("Invalid billing amount")
    if event["kind"] in {"inventory", "model"} and "download_bytes" in data and (not isinstance(data["download_bytes"], int) or isinstance(data["download_bytes"], bool) or not 0 <= data["download_bytes"] <= 1e15):
        raise ValueError("Invalid download byte count")
    for field in ("billing_start", "estimate_start_proxy"):
        if event["kind"] == "billing" and field in data and (not isinstance(data[field], (int, float)) or isinstance(data[field], bool) or not 0 < data[field] < 4102444800):
            raise ValueError("Invalid billing time")
    if event["kind"] == "shutdown" and data.get("confirmed") and event["source"] not in {"provider_api", "provider_console"}:
        raise ValueError("Termination requires provider confirmation")
    return event


class SessionStore:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, identity TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS session_events (session_id TEXT NOT NULL, id TEXT NOT NULL, received REAL NOT NULL, event TEXT NOT NULL, PRIMARY KEY(session_id,id), FOREIGN KEY(session_id) REFERENCES sessions(id))")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, identity):
        if not isinstance(identity, dict) or set(identity) != {"id", "purpose", "provider", "macs", "branch", "commits"}:
            raise ValueError("Invalid session identity")
        for key in ("id", "purpose", "provider", "branch"):
            _text(identity[key])
        if not re.fullmatch(r"[A-Za-z0-9._-]+", identity["id"]):
            raise ValueError("Invalid session ID")
        for key in ("macs", "commits"):
            if not isinstance(identity[key], list) or len(identity[key]) > 20 or not all(isinstance(s, str) and 0 < len(s) <= 240 for s in identity[key]):
                raise ValueError("Invalid identity list")
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES (?,?)", (identity["id"], json.dumps(identity, sort_keys=True)))

    def append(self, session_id, event):
        validate_event(event)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise KeyError(session_id)
            existing = db.execute("SELECT event FROM session_events WHERE session_id=? AND id=?", (session_id, event["id"])).fetchone()
            encoded = json.dumps(event, sort_keys=True, allow_nan=False)
            if existing:
                if existing[0] != encoded:
                    raise ValueError("Conflicting event ID")
                return False
            if event["kind"] == "outcome":
                prior = [json.loads(row[0]) for row in db.execute("SELECT event FROM session_events WHERE session_id=? ORDER BY rowid", (session_id,))]
                task_id = event["data"]["task_id"]
                if not any(e["kind"] == "task" and e["data"].get("task_id") == task_id for e in prior):
                    raise ValueError("Outcome has no assigned task")
                previous = next((e["data"]["state"] for e in reversed(prior) if e["kind"] == "outcome" and e["data"]["task_id"] == task_id), None)
                state = event["data"]["state"]
                if state not in NEXT_OUTCOME[previous]:
                    raise ValueError("Invalid outcome transition")
                if state in {"completed", "independently_verified"}:
                    result = next((e for e in prior if e["kind"] == "result" and e["id"] == event["data"]["result_id"] and e["data"].get("task_id") == task_id), None)
                    if not result or result["data"].get("state") != "completed" or result["data"].get("finish_reason") != "stop":
                        raise ValueError("Completed outcome requires matching complete result")
            db.execute("INSERT INTO session_events VALUES (?,?,?,?)", (session_id, event["id"], time.time(), encoded))
        return True

    def get(self, session_id):
        with self.connection() as db:
            row = db.execute("SELECT identity FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError(session_id)
            events = [dict(json.loads(raw), received=received) for received, raw in db.execute("SELECT received,event FROM session_events WHERE session_id=? ORDER BY rowid", (session_id,))]
        return {"identity": json.loads(row[0]), "events": events}

    def list(self):
        with self.connection() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM sessions ORDER BY rowid DESC LIMIT 100")]
        return ids


def derive(record, now=None, stale_after=300, idle_after=600, budget_margin=20):
    now = time.time() if now is None else now
    latest = {}
    accumulated = {}
    tasks = {}
    outcomes = {}
    resources = {}
    gpu = {}
    for e in record["events"]:
        kind, data = e["kind"], e["data"]
        latest[kind] = e
        accumulated.setdefault(kind, {}).update(data)
        if kind == "task":
            tasks[data["task_id"]] = e
        if kind == "outcome":
            outcomes[data["task_id"]] = e
        if kind == "resource":
            resources[data["name"]] = e
        if kind == "gpu":
            gpu[data["index"]] = e
    def data(kind):
        return accumulated.get(kind, {})
    state_report = next((e for e in reversed(record["events"]) if e["kind"] == "provider" and "state" in e["data"]), None)
    termination_report = next((e for e in reversed(record["events"]) if e["kind"] == "shutdown" and e["data"].get("confirmed")), None)
    observed_provider = data("provider").get("state", "unknown")
    confirmed = bool(data("shutdown").get("confirmed") or (state_report and state_report["data"]["state"] == "terminated" and state_report["source"] in {"provider_api", "provider_console"}))
    contradictory_provider = bool(termination_report and state_report and state_report["received"] > termination_report["received"] and state_report["source"] in {"provider_api", "provider_console"} and state_report["data"]["state"] == "running")
    if contradictory_provider:
        confirmed = False
    provider = "unknown" if observed_provider == "terminated" and not confirmed else observed_provider
    if contradictory_provider:
        provider = "unknown"
    if confirmed:
        provider = "terminated"
    contact_at = max((e["received"] for e in record["events"] if e["kind"] in {"contact", "provider", "connection", "inventory", "model", "request"}), default=None)
    stale = contact_at is None or now - contact_at > stale_after
    if stale and not confirmed:
        provider = "unknown"
    inventory = data("inventory")
    model = data("model").get("state", "unknown")
    if inventory.get("checked") and not inventory.get("models") and ("model" not in latest or latest["inventory"]["received"] > latest["model"]["received"]):
        model = "absent"
    if stale and not confirmed:
        model = "unknown"
    if data("connection").get("state") == "blocked" and ("inventory" not in latest or latest["connection"]["received"] > latest["inventory"]["received"]):
        model = "unknown"
    running_gpu = [e for e in tasks.values() if e["data"].get("scope") == "gpu" and e["data"]["state"] == "running"]
    last_request = latest.get("request")
    latest_result = latest.get("result")
    activity = bool(last_request and any(e["data"]["task_id"] == last_request["data"].get("task_id") for e in running_gpu) and
                    now - last_request["received"] <= idle_after and last_request["data"].get("state") == "running" and
                    not (latest_result and latest_result["received"] > last_request["received"] and latest_result["data"].get("task_id") == last_request["data"].get("task_id")))
    bill = data("billing")
    rate_event = next((e for e in reversed(record["events"]) if e["kind"] == "billing" and "rate" in e["data"]), None)
    charge_event = next((e for e in reversed(record["events"]) if e["kind"] == "billing" and "reported_charge" in e["data"]), None)
    start, rate = bill.get("billing_start", bill.get("estimate_start_proxy")), bill.get("rate")
    stop_at = termination_report["at"] if confirmed and termination_report else state_report["at"] if confirmed and state_report else None
    elapsed = max(0, (min(now, stop_at) if stop_at else now) - start) if isinstance(start, (int, float)) else None
    estimate = round(elapsed / 3600 * rate, 2) if elapsed is not None and isinstance(rate, (int, float)) else None
    alerts = []
    if contradictory_provider:
        alerts.append("Contradictory provider state after termination; reconcile with provider")
    if provider == "running" and model == "absent":
        alerts.append("Paid instance: successful inventory found no model")
    if provider == "running" and not activity:
        alerts.append("Paid instance: no recent GPU request activity")
        idle_since = max((e["received"] for e in record["events"] if e["kind"] in {"request", "result"} and e["data"].get("state") in {"running", "completed", "failed"}), default=None)
        idle_start = idle_since or (start if isinstance(start, (int, float)) else latest.get("provider", {}).get("received"))
        if idle_start and now - idle_start >= idle_after:
            alerts.append("Prolonged paid idle interval")
    if stale and not confirmed:
        alerts.append("Contact stale or unknown")
    if data("model").get("state") == "failed":
        alerts.append("Model failure reported in last model observation")
    if elapsed and elapsed > idle_after and model in {"unknown", "absent", "downloading", "downloaded", "loading"}:
        alerts.append("Prolonged setup")
    if estimate is not None and isinstance(bill.get("ceiling"), (int, float)) and estimate >= bill["ceiling"] - budget_margin:
        alerts.append("Approaching session budget")
    retained = [e["data"] for e in resources.values() if e["data"].get("billable") and e["data"].get("state") != "released"]
    task_views = []
    for entry in tasks.values():
        item = dict(entry["data"], source=entry["source"])
        outcome = outcomes.get(item["task_id"])
        item["outcome"] = outcome["data"]["state"] if outcome else "unresolved"
        item["outcome_event_id"] = outcome["id"] if outcome else None
        item["outcome_result_id"] = outcome["data"].get("result_id") if outcome else None
        item["outcome_source"] = outcome["source"] if outcome else None
        item["outcome_evidence"] = outcome["data"].get("evidence") if outcome else None
        item["outcome_verifier"] = outcome["data"].get("verifier") if outcome else None
        result = next((e for e in reversed(record["events"]) if e["kind"] == "result" and e["data"].get("task_id") == item["task_id"]), None)
        if result and result["data"].get("finish_reason") == "length":
            item["state"] = "incomplete output"
            item["verification"] = "Model output reached length limit; review incomplete"
        task_views.append(item)
    gpu_samples = [dict(e["data"], observed_at=e["at"], received=e["received"],
                        state="stale" if now-e["received"] > stale_after else "observed") for e in sorted(gpu.values(),key=lambda x:x["data"]["index"])]
    return {"provider": provider, "last_provider_state": observed_provider, "connection": "unknown" if stale else data("connection").get("state", "unknown"), "model": model,
            "activity": "active" if activity else "unknown" if stale else "no recent request", "stale": stale,
            "last_contact": datetime.fromtimestamp(contact_at, timezone.utc).isoformat() if contact_at else None,
            "tasks": task_views, "alerts": alerts, "billing": bill, "elapsed_seconds": elapsed,
            "estimated_charge": estimate, "estimate_basis": "provider billing start" if "billing_start" in bill else "guest boot proxy" if "estimate_start_proxy" in bill else "unknown", "reported_charge": bill.get("reported_charge"),
            "rate_source": rate_event["source"] if rate_event else None, "rate_observed_at": rate_event["at"] if rate_event else None,
            "charge_source": charge_event["source"] if charge_event else None, "charge_observed_at": charge_event["at"] if charge_event else None,
            "model_details": data("model"), "inventory": inventory, "request": data("request"), "retained_resources": retained, "gpu_samples":gpu_samples,
            "shutdown_requested": bool(data("shutdown").get("requested")), "termination_confirmed": bool(confirmed),
            "provider_details": data("provider"), "summary": f"Provider {provider}; connection {'unknown' if stale else data('connection').get('state', 'unknown')}; model {model}; GPU activity {'active' if activity else 'unverified'}; {len(alerts)} alert(s)."}


def install(app, render, base_ctx):
    database = SessionStore()

    @app.get("/sessions")
    def sessions_page(request: Request):
        ctx = base_ctx(request, "sessions")
        ctx["sessions"] = [(record["identity"], configured_view(record)) for sid in database.list() for record in [database.get(sid)]]
        return render("sessions.html", ctx)

    @app.get("/sessions/{session_id}")
    def session_page(session_id: str, request: Request):
        try:
            record = database.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")
        ctx = base_ctx(request, "sessions")
        coordinator=coordinator_view(session_id)
        from .workflows import attention
        try:
            from . import monitoring
            participants=monitoring.participants(monitoring.database().recent())
        except (ValueError,sqlite3.Error,OSError):
            participants=[]
        ctx.update(record=record, view=configured_view(record), coordinator=coordinator,
                   attention=attention(coordinator,participants=participants) if coordinator else [])
        return render("session.html", ctx)

    @app.get("/sessions/{session_id}/coordinator.json")
    def coordinator_export(session_id: str):
        if session_id not in database.list():
            raise HTTPException(404, "Session not found")
        snapshot=coordinator_view(session_id)
        if snapshot is None:
            raise HTTPException(404, "Coordinator record not configured")
        return JSONResponse(snapshot)

    @app.get("/sessions/{session_id}/export.json")
    def session_export(session_id: str):
        try:
            record = database.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")
        return JSONResponse({**record, "view": configured_view(record)}, headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'})


def configured_view(record):
    def threshold(name, default):
        try:
            value = float(os.environ.get(name, default))
            return value if 0 < value < 86400 else default
        except ValueError:
            return default
    return derive(record, stale_after=threshold("NODE_UI_SESSION_STALE_SECONDS", 300),
                  idle_after=threshold("NODE_UI_SESSION_IDLE_SECONDS", 600),
                  budget_margin=threshold("NODE_UI_SESSION_BUDGET_MARGIN", 20))


def coordinator_view(session_id):
    path=os.environ.get("NODE_UI_COORDINATOR_DB")
    if not path or os.environ.get("NODE_UI_COORDINATOR_SESSION_ID") != session_id or not Path(path).is_file():
        return None
    from .session_coordinator import Coordinator
    return Coordinator(path).snapshot()
