"""
keel_events — Node-side Keel governance ledger for media-evidence
operations (v0.3.0, additive).

The OpenClaw ledger records model calls; it never sees local evidence
extraction or governed media retrieval. This append-only JSONL ledger
records those steps: intake, tool runs, retrieval attempts, cleanup —
with hashes and tool names, never media content and never full user
URLs beyond the canonical form.

Separate file from logs/openclaw_events.jsonl by design: that file is
runtime-owned and this UI never writes to it.
"""
import json
import os
import time
import uuid
from pathlib import Path

from . import config

MEDIA_LEDGER_PATH = Path(os.environ.get(
    "NODE_UI_MEDIA_LEDGER",
    str(config.PROJECT_ROOT / "logs" / "keel_media_events.jsonl")))


def append_event(event: str, detail: dict = None) -> str:
    """Append one governance event. Returns the event id. Fail-soft:
    a ledger write problem never blocks evidence handling (the evidence
    package's processing_events remain the authoritative record)."""
    eid = "kme-" + uuid.uuid4().hex[:12]
    row = {
        "id": eid,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "layer": "node_ui_keel",
        "policy_version": None,
        "detail": {k: v for k, v in (detail or {}).items()
                   if v is not None},
    }
    try:
        from . import keel_policy
        row["policy_version"] = keel_policy.policy_summary()["version"]
    except Exception:
        pass
    try:
        MEDIA_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MEDIA_LEDGER_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass
    return eid


def read_events(limit: int = 50) -> list:
    if not MEDIA_LEDGER_PATH.exists():
        return []
    rows = []
    try:
        with MEDIA_LEDGER_PATH.open("r", encoding="utf-8",
                                    errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(reversed(rows[-limit:]))


def count_events() -> int:
    if not MEDIA_LEDGER_PATH.exists():
        return 0
    try:
        with MEDIA_LEDGER_PATH.open("r", encoding="utf-8",
                                    errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0
