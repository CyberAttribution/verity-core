"""
Read-only JSONL ledger reader for logs/openclaw_events.jsonl.

Defensive about field names across event schema versions.
Never writes to the ledger.
"""
import json
from datetime import datetime, timezone

from . import config


def _pick(e: dict, *keys, default=None):
    for k in keys:
        if e.get(k) not in (None, ""):
            return e[k]
    return default


def _fmt_time(v):
    """Render an epoch or ISO timestamp as a short local-ish string."""
    if v in (None, ""):
        return "—"
    try:
        if isinstance(v, (int, float)):
            # tolerate ms epochs
            if v > 1e12:
                v = v / 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        s = str(v)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(v)


def normalize(e: dict) -> dict:
    """Map one raw ledger event to the display fields the UI needs."""
    lat = _pick(e, "latency_ms", "latency", "duration_ms")
    return {
        "time": _fmt_time(_pick(e, "ts", "timestamp", "time", "created_at")),
        "id": str(_pick(e, "id", "event_id", "uuid", default="—")),
        "event": str(_pick(e, "event", "event_type", "type", default="—")),
        "model": str(_pick(e, "model", "default_model", default="—")),
        "policy": str(_pick(e, "policy", "policy_profile", default="—")),
        "decision": str(_pick(e, "decision", default="—")),
        "provider": str(_pick(e, "provider", default="—")),
        "node": str(_pick(e, "node", default="—")),
        "latency_ms": lat if lat is not None else "—",
        "alarm": bool(e.get("governance_alarm")),
        "failures": e.get("governance_failures") or [],
        "raw": json.dumps(e, indent=2, default=str),
    }


def read_events(limit: int = 50) -> list:
    """Last `limit` events, newest first. [] if the ledger is missing."""
    path = config.LEDGER_PATH
    if not path.exists():
        return []
    events = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return [normalize(e) for e in reversed(events[-limit:])]


def read_raw_events(limit: int = 50) -> list:
    """Last `limit` raw event dicts, newest first (for governance scoring)."""
    path = config.LEDGER_PATH
    if not path.exists():
        return []
    events = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(reversed(events[-limit:]))


def count_events() -> int:
    path = config.LEDGER_PATH
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def latest_event_id() -> str | None:
    ev = read_events(limit=1)
    return ev[0]["id"] if ev and ev[0]["id"] != "—" else None
