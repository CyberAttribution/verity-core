"""Supervised local file inbox for observational session events.

Each JSON file is {"session_id": "...", "event": {...}}. A successful import is
renamed to .seen. SQLite event IDs make a crash between commit and rename safe.
No provider credentials, outbound requests, task submission, or shutdown.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "node"))
from swarmtracker_node_ui.sessions import SessionStore, configured_view, derive
from swarmtracker_node_ui.session_alerts import AlertJournal


def collect_once(inbox, db, now=None):
    inbox = Path(inbox)
    notices = []
    failed_sessions=set()
    unattributed_failure=False
    for path in sorted(inbox.glob("*.json")):
        payload=None
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 12000:
                notices.append(f"Rejected unsafe or oversized inbox file: {path.name}")
                unattributed_failure=True
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload,dict) or set(payload) != {"session_id", "event"}:
                raise ValueError("Invalid inbox envelope")
            db.append(payload["session_id"], payload["event"])
            path.rename(path.with_suffix(".seen"))
        except (ValueError, KeyError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            notices.append(f"Rejected inbox file {path.name}: {type(exc).__name__}: {exc}")
            if isinstance(payload,dict) and isinstance(payload.get("session_id"),str):
                failed_sessions.add(payload["session_id"])
            else:
                unattributed_failure=True
    try:
        journal=AlertJournal(db.path.with_name("session-alerts.db"))
        sessions=db.list()
    except (OSError,sqlite3.Error) as exc:
        notices.append("Session warning collection unavailable: "+type(exc).__name__)
        return notices
    for sid in sessions:
        if unattributed_failure or sid in failed_sessions:
            notices.append(f"{sid}: warning evaluation unavailable: incomplete inbox import; prior episodes retained")
            continue
        try:
            view = configured_view(db.get(sid)) if now is None else derive(db.get(sid), now=now)
            journal.evaluate(sid,view["alerts"],time.time() if now is None else now)
        except (ValueError,KeyError,OSError,sqlite3.Error) as exc:
            # Failed observation never means existing warnings have cleared.
            notices.append(f"{sid}: warning evaluation unavailable: {type(exc).__name__}")
            continue
        for warning in view["alerts"]:
            notices.append(f"{sid}: {warning}")
    return notices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inbox", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if not 5 <= args.interval <= 3600:
        parser.error("Interval must be 5–3600 seconds")
    args.inbox.mkdir(parents=True, exist_ok=True)
    db = SessionStore()
    last = set()
    while True:
        current = set(collect_once(args.inbox, db))
        for notice in sorted(current - last):
            print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), notice, flush=True)
        last = current
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
