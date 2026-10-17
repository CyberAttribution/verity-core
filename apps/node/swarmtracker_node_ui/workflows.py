"""Saved product-work guidance. Telemetry is reachability, not task authority."""
import json
import os
import time
from pathlib import Path

from .session_coordinator import Coordinator


def coordinator():
    return Coordinator(os.environ.get("NODE_UI_PRODUCT_DB") or os.environ.get("NODE_UI_COORDINATOR_DB", "session-data/product-work.db"))


def guidance(work, *, reachable=None, heartbeat=None, now=None):
    now=time.time() if now is None else now
    spec=json.loads(work["spec"])
    state=work["state"]
    if reachable is False and state not in {"finished","failed"}:
        display="disconnected"
        reason="Runtime contact is lost; execution outcome is unknown."
        action="Inspect the request ledger and reconcile any uncertain submission before continuing."
    elif state=="awaiting_approval":
        display="awaiting approval"; reason="Required approval has not been recorded."
        action="Obtain the scoped approval before dispatch."
    elif state=="awaiting_review":
        display="awaiting review"; reason="Output exists but independent acceptance is pending."
        action="Review the output against the saved acceptance criteria."
    elif state=="working" and now-work["updated"]>300:
        display="blocked"; reason="No recorded task progress for more than five minutes; this does not establish a runtime failure."
        action="Inspect the request and checkpoint before a scoped follow-up."
    else:
        display=state
        reason={"queued":"Waiting for dependencies and a worker.","working":"No stop recorded.",
                "paused":"The session or workflow is paused.","blocked":"A dependency or gate is blocking work.",
                "failed":"The recorded attempt failed.","finished":"Independent acceptance evidence is recorded.",
                "unknown":"The outcome is not established."}.get(state,"No cause recorded.")
        action={"queued":"Check eligibility and worker ownership.","working":"Observe actual task progress.",
                "paused":"Resume only after checking approval and budget gates.","blocked":"Resolve the recorded blocker.",
                "failed":"Inspect failure and preserve history before a corrected successor.",
                "finished":"No continuation needed.","unknown":"Inspect evidence and reconcile."}.get(state,"Inspect the workflow.")
    return {"id":work["id"],"session_id":spec["session_id"],"agent_id":spec["agent_id"],
            "purpose":spec["purpose"],"acceptance_criteria":spec["acceptance_criteria"],
            "state":display,"progress":work["checkpoint"] or "No verified checkpoint recorded.",
            "reason":reason,"next_action":action,"evidence":work["evidence"] or "None recorded",
            "evidence_at":work["updated"] if work["evidence"] else None,
            "approval": "Explicit product development scope; dispatch requires a separate scoped request gate",
            "reachable":reachable,"heartbeat":heartbeat,"updated":work["updated"]}


def records(snapshot, participants=(), now=None):
    agents={p["agent_id"]:p for p in participants}
    result=[]
    for work in snapshot["work_items"]:
        spec=json.loads(work["spec"])
        p=agents.get(spec["agent_id"])
        reach=None if p is None else p["status"]=="Active"
        result.append(guidance(work,reachable=reach,heartbeat=p.get("last_seen_utc") if p else None,now=now))
    return result


CAPABILITIES={"glm-review":{"observe":True,"submit_new_request":True,"resume_conversation":False,
                             "retrieve_remote_status":False,"pause_remote":False,"cancel_remote":False}}


def attention(snapshot, *, endpoint_reachable=None):
    """Explain blocked work from saved gates, never from utilization or self-assessment."""
    issues=[]
    control=snapshot["control"]
    tasks={x["id"]:x for x in snapshot["tasks"]}
    def add(issue_id,cause,link): issues.append({"id":issue_id,"cause":cause,"link":link})
    for w in snapshot["work_items"]:
        state=w["state"]
        if state in {"awaiting_approval","blocked","failed","unknown"}:
            add("work:"+w["id"],{"awaiting_approval":"Missing approval", "blocked":"Blocked work dependency or gate",
                 "failed":"Recorded work failure", "unknown":"Work outcome unresolved"}[state],"/workflows/"+w["id"])
    for task in snapshot["tasks"]:
        if task["state"]=="unresolved": add("request:"+task["id"],"Submission or observation unresolved; reconcile before retry","/workflows")
        elif task["state"]=="failed": add("request:"+task["id"],"Failed request (finish "+str(task["finish_reason"] or "unknown")+")","/workflows")
        elif task["state"]=="completed" and task["evidence"] and task["evidence"].startswith("Rejected:"):
            add("review:"+task["id"],"Independent review rejected the result","/workflows")
    if control:
        if control["mode"]=="running" and snapshot["worker"]["state"]!="active":
            add("worker:absent","Running control has no active worker; inspect PID and heartbeat","/sessions")
        budget=snapshot["estimated_charge"]>=control["ceiling"]-control["margin"]
        if budget: add("budget:limited","Rental estimate reached dispatch margin; no new request eligible","/sessions")
        queued=[x for x in snapshot["tasks"] if x["state"]=="queued"]
        eligible=[x for x in queued if all(tasks.get(dep,{}).get("state")=="independently_verified" for dep in json.loads(x["spec"])["dependencies"])]
        if control["mode"]=="running" and not budget and not eligible and not any(x["state"] in {"submitted","acknowledged"} for x in snapshot["tasks"]):
            cause=("Queued requests are blocked by unverified or failed dependencies" if queued else
                   "Queue exhausted: no eligible approved request")
            if snapshot["worker"]["state"]=="active" and endpoint_reachable is True:
                cause+="; worker and endpoint are reachable, but no task may be dispatched"
            add("queue:no-eligible",cause,"/workflows?attention=1")
    history={}
    for e in snapshot.get("attention_history",[]): history[e["issue_id"]]=e
    for issue in issues: issue["acknowledgment"]=history.get(issue["id"])
    return issues
