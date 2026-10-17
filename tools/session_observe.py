"""Import manually confirmed observations without exposing a write API in the UI.

Usage: python tools/session_observe.py create identity.json
       python tools/session_observe.py append session-id observation.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "node"))
from swarmtracker_node_ui.sessions import SessionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "append", "export"))
    parser.add_argument("first", help="identity path, session ID, or session ID for export")
    parser.add_argument("event", nargs="?", help="event JSON path for append")
    args = parser.parse_args()
    db = SessionStore()
    if args.action == "create":
        db.create(json.loads(Path(args.first).read_text()))
    elif args.action == "append":
        if not args.event:
            parser.error("append requires an event JSON file")
        print("Appended" if db.append(args.first, json.loads(Path(args.event).read_text())) else "Already recorded")
    else:
        print(json.dumps(db.get(args.first), indent=2))


if __name__ == "__main__":
    main()
