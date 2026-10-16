"""Durable telemetry ingestion and inspection; no remote executor."""
import hmac
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from verity_core.trust import telemetry, record as trust
from verity_core.json_safety import bounded_json_loads
from . import store, decision_routes

STORE = None


class MonitorStore:
    def __init__(self, path=None):
        self.path = Path(path or store.DB_PATH.with_name("development-monitor.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS monitor_runs (id TEXT PRIMARY KEY, record TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        try:
            con.execute("PRAGMA synchronous=FULL")
            yield con
            con.commit()
        except BaseException:
            con.rollback(); raise
        finally:
            con.close()

    def _load(self, con, rid):
        row = con.execute("SELECT record FROM monitor_runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        record = bounded_json_loads(row[0])
        view = telemetry.inspect(record, now=time.time())
        if view["run_id"] != rid:
            raise ValueError("Stored run identifier mismatch")
        return record

    def ingest(self, manifest, item, *, now=None):
        manifest = telemetry.manifest(manifest); item = telemetry.event(item)
        now = time.time() if now is None else now
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                record = self._load(con, manifest["run_id"])
            except KeyError:
                record = telemetry.create(manifest, now)
            if record["entries"][0]["payload"]["manifest"] != manifest:
                raise ValueError("Run identity cannot change")
            telemetry.append(record, item, now)
            con.execute("INSERT INTO monitor_runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                        (manifest["run_id"], trust.canonical_json(record)))
        return telemetry.inspect(record, now=now)

    def get(self, rid):
        telemetry.identifier(rid)
        with self.connection() as con:
            return self._load(con, rid)

    def recent(self, now=None):
        now = time.time() if now is None else now
        with self.connection() as con:
            rows = con.execute("SELECT id FROM monitor_runs ORDER BY rowid DESC LIMIT 100").fetchall()
            views = [telemetry.inspect(self._load(con, row[0]), now=now) for row in rows]
        order = {"Active": 0, "Recently disconnected": 1, "Status unknown": 2}
        views.sort(key=lambda v: (order[v["status"]], -v["last_seen"]))
        for view in views:
            view["last_seen_utc"] = datetime.fromtimestamp(view["last_seen"], timezone.utc).isoformat()
        return views


def database():
    global STORE
    if STORE is None:
        STORE = MonitorStore()
    return STORE


def install(app, render, base_ctx):
    router = APIRouter()

    @router.post("/api/monitor/events")
    async def ingest(request: Request):
        decision_routes.local_request(request)
        # Server must bind to loopback. Require actual loopback peer in addition
        # to Host/Origin; TestClient has its own explicit testing peer name.
        if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "Loopback telemetry collector required")
        token = os.environ.get("VERITY_MONITOR_TOKEN", "")
        if len(token) < 32:
            raise HTTPException(503, "Telemetry ingestion is not enabled")
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            raise HTTPException(403, "Collector authorization required")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON telemetry required")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16384:
                raise HTTPException(413, "Telemetry event too large")
        try:
            body = bounded_json_loads(data.decode("utf-8"))
            if not isinstance(body, dict) or set(body) != {"manifest", "event"}:
                raise ValueError("Invalid envelope")
            view = database().ingest(body["manifest"], body["event"])
        except (ValueError, TypeError, UnicodeError, KeyError, AttributeError, RecursionError):
            raise HTTPException(422, "Telemetry rejected: schema, sequence or integrity failure") from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "Telemetry could not be durably recorded") from None
        return {"run_id": view["run_id"], "head": view["head"], "entry_count": view["entry_count"]}

    def get(request, rid):
        decision_routes.local_request(request)
        try:
            return database().get(rid)
        except KeyError:
            raise HTTPException(404, "Telemetry run not found") from None
        except (ValueError, TypeError, AttributeError, sqlite3.Error, OSError):
            raise HTTPException(503, "Telemetry integrity or storage unavailable") from None

    @router.get("/monitor/runs/{rid}")
    def detail(request: Request, rid: str):
        record = get(request, rid)
        view = telemetry.inspect(record, now=time.time())
        ctx = base_ctx(request, "agents")
        ctx.update(run=view, entries=record["entries"])
        response = render("monitor_run.html", ctx)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/monitor/runs/{rid}/export.json")
    def export(request: Request, rid: str):
        record = get(request, rid)
        return JSONResponse(record, headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{rid}.telemetry.json"'})

    @router.get("/monitor/runs/{rid}/checkpoint.json")
    def checkpoint(request: Request, rid: str):
        return JSONResponse(telemetry.checkpoint(get(request, rid)), headers={"Cache-Control": "no-store"})

    app.include_router(router)
