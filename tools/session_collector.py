"""Supervised local file inbox for observational session events.

Each JSON file is {"session_id": "...", "event": {...}}. A successful import is
renamed to .seen. SQLite event IDs make a crash between commit and rename safe.
No provider credentials, outbound requests, task submission, or shutdown.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "node"))
from swarmtracker_node_ui.sessions import SessionStore, configured_view, derive


def collect_once(inbox, db, now=None):
    inbox = Path(inbox)
    notices = []
    for path in sorted(inbox.glob("*.json")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 12000:
            notices.append(f"Rejected unsafe or oversized inbox file: {path.name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if set(payload) != {"session_id", "event"}:
                raise ValueError("Invalid inbox envelope")
            db.append(payload["session_id"], payload["event"])
            path.rename(path.with_suffix(".seen"))
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            notices.append(f"Rejected inbox file {path.name}: {type(exc).__name__}: {exc}")
    for sid in db.list():
        view = configured_view(db.get(sid)) if now is None else derive(db.get(sid), now=now)
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
