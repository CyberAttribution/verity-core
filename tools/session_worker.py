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


def run_worker(db, *, serve=False, poll_seconds=5):
    """Drain prepared contracts, or observe holds, without a chat heartbeat.

    Never creates implementation work or verifies its own results. A killed
    worker leaves uncertain submissions for the existing reconciliation gate.
    """
    if not 0.1 <= poll_seconds <= 60:
        raise ValueError('Poll interval outside 0.1–60 seconds')
    owner=uuid.uuid4().hex
    db.register_worker(owner,os.getpid())
    try:
        while True:
            db.worker_heartbeat(owner)
            db.recover_stale()
            control=db.snapshot()['control']
            gate=db.dispatch_gate(control)
            if gate and gate != 'paused':
                break
            if not gate and db.run_one(owner):
                continue
            if not serve:
                break
            if not gate and db.schedule_followups():
                continue
            time.sleep(poll_seconds)
    finally:
        db.unregister_worker(owner)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("configure","enqueue","resume","pause","stop","run","serve","status","verify","reject","reconcile","retry"))
    p.add_argument("args",nargs="*")
    p.add_argument('--poll-seconds',type=float,default=5)
    a=p.parse_args()
    db=Coordinator(os.environ.get("NODE_UI_COORDINATOR_DB","session-data/coordinator.db"))
    if a.action == "configure":
        db.configure(rate=float(a.args[0]),start=float(a.args[1]),ceiling=float(a.args[2]),
                     deadline=float(a.args[3]) if len(a.args)>3 else None)
    elif a.action == "enqueue": db.enqueue(json.loads(Path(a.args[0]).read_text()))
    elif a.action in {"resume","pause","stop"}: db.control({"resume":"running","pause":"paused","stop":"stopped"}[a.action])
    elif a.action == "verify": db.verify(*a.args)
    elif a.action == "reject": db.reject_review(*a.args)
    elif a.action == "reconcile": db.reconcile(*a.args)
    elif a.action == "retry": db.retry_confirmed_not_executed(*a.args)
    elif a.action == "run":
        run_worker(db,poll_seconds=a.poll_seconds)
    elif a.action == "serve":
        run_worker(db,serve=True,poll_seconds=a.poll_seconds)
    snapshot=db.snapshot()
    if a.action == "status":
        print(json.dumps({"control":snapshot["control"],"worker":snapshot["worker"],"estimated_charge":snapshot["estimated_charge"],
                          "tasks":[{"id":x["id"],"state":x["state"],"finish_reason":x["finish_reason"]} for x in snapshot["tasks"]]},indent=2))
    else:
        print(json.dumps(snapshot,indent=2))


if __name__ == "__main__": main()
