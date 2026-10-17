"""Local approved GLM review queue. Run behind an existing loopback SSH tunnel.

Set NODE_UI_COORDINATOR_DB to the persistent SQLite location. The run command
loops until paused, stopped, budget gated or no eligible work remains.
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"apps"/"node"))
try:
    from swarmtracker_node_ui.session_coordinator import Coordinator
except ModuleNotFoundError:  # Isolated approved worker bundle on the MacBook.
    from session_coordinator import Coordinator


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("configure","enqueue","resume","pause","stop","run","serve","status","verify","reconcile","retry"))
    p.add_argument("args",nargs="*")
    a=p.parse_args()
    db=Coordinator(os.environ.get("NODE_UI_COORDINATOR_DB","session-data/coordinator.db"))
    if a.action == "configure":
        db.configure(rate=float(a.args[0]),start=float(a.args[1]),ceiling=float(a.args[2]))
    elif a.action == "enqueue": db.enqueue(json.loads(Path(a.args[0]).read_text()))
    elif a.action in {"resume","pause","stop"}: db.control({"resume":"running","pause":"paused","stop":"stopped"}[a.action])
    elif a.action == "verify": db.verify(*a.args)
    elif a.action == "reconcile": db.reconcile(*a.args)
    elif a.action == "retry": db.retry_confirmed_not_executed(*a.args)
    elif a.action == "run":
        owner=uuid.uuid4().hex
        while db.run_one(owner):
            pass
    elif a.action == "serve":
        owner=uuid.uuid4().hex
        idle_since=time.monotonic()
        while True:
            snapshot=db.snapshot()
            control=snapshot["control"]
            if not control or control["mode"] == "stopped" or snapshot["estimated_charge"] >= control["ceiling"]-control["margin"]:
                break
            if control["mode"] == "running" and db.run_one(owner):
                idle_since=time.monotonic()
                continue
            if time.monotonic()-idle_since >= 600:
                break  # No eligible work; do not hold an idle paid instance open indefinitely.
            time.sleep(5)
    print(json.dumps(db.snapshot(),indent=2))


if __name__ == "__main__": main()
