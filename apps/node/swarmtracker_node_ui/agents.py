"""Observed integrated runtime participants; no discovery or identity certification."""
from datetime import datetime, timezone
from threading import Lock

from . import config, ledger
from . import openclaw_client as oc

_lock = Lock()
_last = {}


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def participant_snapshot(health, models, events, *, observed_at=None):
    """Use local observation times and exact node/model matching, never global IDs."""
    now = observed_at or datetime.now(timezone.utc).isoformat()
    health = health if isinstance(health, dict) else {}
    online = health.get("status") == "ok"
    node = _text(health.get("node")) if online else None
    model = _text(health.get("default_model")) if online else None
    provider = _text(health.get("provider")) if online else None
    with _lock:
        if online:
            if node and model:
                _last.update(node=node, model=model, provider=provider, seen=now)
        previous = dict(_last)
    if not online:
        node = previous.get("node")
        model = previous.get("model")
        provider = previous.get("provider")
    latest = next((e for e in events if isinstance(e, dict)
                   and node and model and e.get("node") == node
                   and e.get("model") == model), None)
    available = online and isinstance(models, list) and model in models
    return {
        "name": node or "Configured local participant",
        "framework": "Ollama native API (direct local)" if config.RUNTIME_KIND == "ollama" else "Legacy OpenClaw v0 API",
        "node": node or "Not observed",
        "model": model or "Not observed",
        "provider": provider or "Not observed",
        "online": online,
        "status": "Reachable" if online else "Unavailable",
        "checked_at": now,
        "last_seen": previous.get("seen") or "Not observed in this app session",
        "model_available": available,
        "operator": config.OPERATOR,
        "latest_event": latest,
        "identity_scope": "Current reported identity" if online else "Last observed identity",
        "current_task": "Not observed",
        "trust_record": "No verified record link available",
    }


def observe():
    health = oc.healthz()
    models = oc.ollama_models()
    raw = ledger.read_raw_events(limit=100)
    events = [ledger.normalize(e) for e in raw if isinstance(e, dict)]
    return participant_snapshot(health, models, events)
