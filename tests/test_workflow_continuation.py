import json
import time
import io

import pytest

from swarmtracker_node_ui.session_coordinator import Coordinator, MODEL
from swarmtracker_node_ui.workflows import guidance, attention, records


def review(task_id):
    return dict(id=task_id,purpose="Read-only source critique",dependencies=[],authorization="explicit-read-only-glm-review",
                acceptance_criteria="List specific source risks",expected_outputs="Bounded findings",
                validation_gates="Independent human code review",
                model_settings=dict(model=MODEL,reasoning_effort="low",chat_template_kwargs={"reasoning_effort":"low"},max_tokens=2048))


def work(work_id="guidance", state="working"):
    spec=dict(id=work_id,purpose="Show assigned guidance",dependencies=[],authorization="explicit-product-development",
              acceptance_criteria="Accurate states",expected_outputs="UI and tests",validation_gates="Independent review",
              agent_id="glm-review",session_id="synthetic")
    return dict(id=work_id,spec=json.dumps(spec),state=state,checkpoint=None,evidence=None,updated=100)


def test_guidance_stall_approval_disconnect_and_finished():
    stalled=guidance(work(),reachable=True,heartbeat="sample",now=401)
    assert stalled["state"]=="blocked" and "No recorded task progress" in stalled["reason"]
    assert stalled["heartbeat"]=="sample" and stalled["progress"]=="No verified checkpoint recorded."
    assert guidance(work(state="awaiting_approval"),reachable=True,now=101)["next_action"].startswith("Obtain")
    disconnected=guidance(work(),reachable=False,now=101)
    assert disconnected["state"]=="disconnected" and "unknown" in disconnected["reason"]
    finished=work(state="finished"); finished.update(checkpoint="commit reviewed",evidence="test output")
    assert guidance(finished,reachable=False,now=401)["state"]=="finished"
    assert guidance(work(),reachable=True,active_request=True,now=401)["state"]=="working"


def test_continuation_is_persisted_new_request_and_gated(tmp_path):
    calls=[]
    c=Coordinator(tmp_path/"work.db",adapter=lambda s,r: calls.append(s) or dict(request_id=r,content="bounded",finish_reason="stop"))
    c.configure(rate=1,start=time.time()-10,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"]))
    c.enqueue(review("source"))
    c.bind_source("guidance","source")
    c.control("running")
    assert c.run_one("owner")=="source"
    with pytest.raises(ValueError,match="verification"):
        c.continue_work("guidance","source",approval=True)
    c.verify("source","trusted-runner","Reviewed source against actual code")
    c.update_work("guidance","working",checkpoint="Verified saved source checkpoint",evidence="Reviewed local test log")
    with pytest.raises(ValueError,match="gate"):
        c.continue_work("guidance","source")
    follow=c.continue_work("guidance","source",approval=True)
    assert c.snapshot()["continuations"][0]["task_id"]==follow
    with pytest.raises(ValueError,match="continuation"):
        c.continue_work("guidance","source",approval=True)
    assert c.run_one("owner")==follow
    assert "Verified saved source checkpoint" in calls[-1]["purpose"]
    assert "Original approved review input: Read-only source critique" in calls[-1]["purpose"]
    assert c.snapshot()["tasks"][-1]["state"]=="completed"
    assert c.snapshot()["tasks"][-1]["finish_reason"]=="stop"
    assert c.snapshot()["tasks"][-1]["verifier"] is None


def test_continuation_pause_budget_and_uncertain(tmp_path):
    now=[1000]
    c=Coordinator(tmp_path/"work.db",clock=lambda:now[0])
    c.configure(rate=10,start=900,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"]))
    c.update_work("guidance","working",checkpoint="Verified checkpoint",evidence="Reviewed local test log")
    c.enqueue(review("source"))
    c.bind_source("guidance","source")
    with c.db() as db:
        c._transition(db,"source","completed","synthetic result")
    with pytest.raises(ValueError,match="Paused"):
        c.continue_work("guidance","source",approval=True)
    c.control("running")
    with c.db() as db: c._transition(db,"source","unresolved","ambiguous remote")
    with pytest.raises(ValueError,match="uncertain"):
        c.continue_work("guidance","source",approval=True)
    with c.db() as db: c._transition(db,"source","independently_verified","reconciled synthetic")
    now[0]=900+106*3600/10
    with pytest.raises(ValueError,match="budget"):
        c.continue_work("guidance","source",approval=True)


def test_opt_in_followup_dispatch_and_no_duplicate_or_approval_bypass(tmp_path):
    now=[1000]
    calls=[]
    c=Coordinator(tmp_path/"work.db",clock=lambda:now[0],adapter=lambda s,r: calls.append(r) or dict(request_id=r,content="response",finish_reason="stop"))
    c.configure(rate=1,start=900,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"]))
    c.enqueue(review("source")); c.bind_source("guidance","source")
    with c.db() as db: c._transition(db,"source","independently_verified","synthetic completed review")
    c.update_work("guidance","working",checkpoint="Independent checkpoint",evidence="Reviewed local test log")
    c.control("running")
    assert c.schedule_followups()==[]  # Default disabled.
    c.configure_followup("guidance",mode="running",grace=30,cooldown=60,limit_count=1)
    now[0]+=31
    follow=c.schedule_followups()
    assert len(follow)==1 and c.schedule_followups()==[]
    assert c.snapshot()["followup_settings"][0]["attempts"]==1
    assert c.snapshot()["continuation_authorizations"][0]["mode"]=="automatic"
    assert calls==[]  # Persisted, not yet dispatched.
    assert c.run_one("worker")==follow[0]
    assert len(calls)==1 and c.snapshot()["tasks"][-1]["state"]=="completed"
    assert c.schedule_followups()==[]  # Bound attempt limit.
    c.update_work("guidance","awaiting_approval")
    assert c.schedule_followups()==[]
    c.configure_followup("guidance",mode="paused")
    assert c.schedule_followups()==[]


def test_followup_setting_rechecked_at_dispatch_boundary(tmp_path):
    now=[1000]
    c=Coordinator(tmp_path/"work.db",clock=lambda:now[0])
    c.configure(rate=1,start=900,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"])); c.enqueue(review("source")); c.bind_source("guidance","source")
    with c.db() as db: c._transition(db,"source","independently_verified","synthetic checked")
    c.update_work("guidance","working",checkpoint="Reviewed",evidence="Trusted test")
    c.control("running"); c.configure_followup("guidance",mode="running",grace=30,cooldown=60)
    now[0]+=31
    c.configure_followup("guidance",mode="stopped",grace=30,cooldown=60)
    with pytest.raises(ValueError,match="stopped"):
        c.continue_work("guidance","source",approval=True,automatic=True)
    assert c.snapshot()["continuations"]==[]


def test_approval_wait_cannot_be_cleared_by_resume_without_human_record(tmp_path):
    c=Coordinator(tmp_path/"work.db",clock=lambda:1000)
    c.enqueue_work(json.loads(work()["spec"]))
    c.update_work("guidance","awaiting_approval")
    with pytest.raises(ValueError,match="recorded human"):
        c.update_work("guidance","working")
    c.approve_work("guidance","local-operator","Scoped synthetic approval record")
    c.update_work("guidance","working")
    assert c.snapshot()["work_approvals"][0]["actor"]=="local-operator"
    c.update_work("guidance","awaiting_approval")  # Same timestamp, new generation.
    with pytest.raises(ValueError,match="recorded human"):
        c.update_work("guidance","working")
    c.approve_work("guidance","local-operator","Second scoped decision")
    c.update_work("guidance","working")
    assert c.snapshot()["work_approval_gates"][0]["generation"]==2


def test_assignment_correction_preserves_original_agent_and_evidence(tmp_path):
    c=Coordinator(tmp_path/"work.db")
    c.enqueue_work(json.loads(work()["spec"]))
    c.correct_assignment("guidance","trusted-runner","Implementation belongs to the trusted Mac runner")
    snapshot=c.snapshot()
    assert json.loads(snapshot["work_items"][0]["spec"])["agent_id"]=="trusted-runner"
    assert snapshot["work_corrections"][0]["previous_agent"]=="glm-review"


def test_source_binding_migrates_unique_constraint_and_preserves_history(tmp_path):
    path=tmp_path/"work.db"
    with __import__("sqlite3").connect(path) as db:
        db.execute("CREATE TABLE work_sources (workflow_id TEXT NOT NULL, task_id TEXT UNIQUE NOT NULL, PRIMARY KEY(workflow_id,task_id))")
        db.execute("INSERT INTO work_sources VALUES ('old','source')")
    c=Coordinator(path)
    assert c.snapshot()["work_sources"]==[{"workflow_id":"old","task_id":"source"}]
    c.enqueue_work(json.loads(work("new")["spec"])); c.enqueue(review("source"))
    c.bind_source("new","source")
    assert len(c.snapshot()["work_sources"])==2


def test_followup_does_not_repeat_ambiguous_request(tmp_path):
    now=[1000]
    c=Coordinator(tmp_path/"work.db",clock=lambda:now[0],adapter=lambda s,r: (_ for _ in ()).throw(TimeoutError()))
    c.configure(rate=1,start=900,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"]))
    c.enqueue(review("source")); c.bind_source("guidance","source")
    with c.db() as db: c._transition(db,"source","independently_verified","synthetic")
    c.update_work("guidance","working",checkpoint="Independent checkpoint",evidence="Reviewed local test log")
    c.control("running"); c.configure_followup("guidance",mode="running",grace=30,cooldown=60,limit_count=2)
    now[0]+=31
    follow=c.schedule_followups()[0]
    assert c.run_one("worker")==follow
    assert c.snapshot()["tasks"][-1]["state"]=="unresolved"
    now[0]+=100
    restarted=Coordinator(tmp_path/"work.db",clock=lambda:now[0],adapter=lambda s,r: (_ for _ in ()).throw(AssertionError("duplicate HTTP dispatch")))
    assert restarted.schedule_followups()==[]
    assert restarted.run_one("replacement") is None
    assert restarted.snapshot()["tasks"][-1]["state"]=="unresolved"


def test_automatic_followup_dependency_and_budget_gates(tmp_path):
    now=[1000]
    c=Coordinator(tmp_path/"work.db",clock=lambda:now[0])
    c.configure(rate=10,start=900,ceiling=110)
    parent=json.loads(work("prerequisite")["spec"]); c.enqueue_work(parent)
    child=json.loads(work("child")["spec"]); child["dependencies"]=["prerequisite"]
    c.enqueue_work(child)
    c.enqueue(review("source")); c.bind_source("child","source")
    with c.db() as db: c._transition(db,"source","independently_verified","checked")
    c.update_work("child","working",checkpoint="Reviewed",evidence="Trusted source test")
    c.control("running"); c.configure_followup("child",mode="running",grace=30,cooldown=60)
    now[0]+=31
    assert c.schedule_followups()==[]  # Parent unfinished.
    c.update_work("prerequisite","finished",checkpoint="commit",evidence="Trusted test")
    now[0]=900+106*3600/10
    assert c.schedule_followups()==[]  # Rental dispatch margin exceeded.
    assert c.snapshot()["followup_settings"][0]["attempts"]==0


def test_native_routes_show_guidance_and_persist_operator_followup(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui
    from swarmtracker_node_ui import decision_routes
    monkeypatch.setenv("NODE_UI_PRODUCT_DB",str(tmp_path/"work.db"))
    c=Coordinator(tmp_path/"work.db",adapter=lambda s,r: dict(request_id=r,content="ok",finish_reason="stop"))
    c.configure(rate=1,start=time.time()-1,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"]))
    c.enqueue(review("source")); c.bind_source("guidance","source")
    c.update_work("guidance","working",checkpoint="Independently checked source",evidence="Reviewed local test log")
    c.control("running"); c.run_one("worker")
    c.verify("source","trusted-runner","Reviewed local source and tests")
    client=TestClient(ui.app)
    page=client.get("http://127.0.0.1/workflows")
    assert page.status_code==200 and "Continue approved work" in page.text
    assert "Independently checked source" in page.text
    assert client.get("http://127.0.0.1/agents").status_code==200
    denied=client.post("http://127.0.0.1/workflows/guidance/continue",data={"source_task":"source","approved":"yes","operator_token":"bad"})
    assert denied.status_code==403
    sent=client.post("http://127.0.0.1/workflows/guidance/continue",data={"source_task":"source","approved":"yes","operator_token":decision_routes.TOKEN},follow_redirects=False)
    assert sent.status_code==303
    assert c.snapshot()["tasks"][-1]["state"]=="queued"


def test_unified_detail_preserves_synthetic_disagreement_approval_and_execution(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui, decision_routes, report_routes, report_actions
    from swarmtracker_node_ui.decision_runs import RunStore, synthetic_manifest
    from verity_core.trust import decision
    monkeypatch.setenv("NODE_UI_PRODUCT_DB",str(tmp_path/"work.db"))
    c=Coordinator(tmp_path/"work.db"); c.enqueue_work(json.loads(work("syntheticCase")["spec"]))
    runs=RunStore(tmp_path/"decisions.db"); monkeypatch.setattr(decision_routes,"RUNS",runs)
    denied=runs.create(synthetic_manifest()); assert denied["decision"]=="pending" and not denied["can_execute"]
    rejected=runs.authorize(denied["id"],"rejected","Conflicting evidence","synthetic-operator",denied["head_hash"])
    assert not rejected["can_execute"]
    approved=runs.create(synthetic_manifest())
    c.link_evidence("syntheticCase","decision",rejected["id"],"Synthetic rejected example")
    c.link_evidence("syntheticCase","decision",approved["id"],"Synthetic approval pause")
    approved=runs.authorize(approved["id"],"approved","Synthetic scoped approval","synthetic-operator",approved["head_hash"])
    executed,allowed=runs.execute(approved["id"],"synthetic-operator",approved["head_hash"])
    assert allowed and executed["state"]=="simulated"
    assert decision.inspect(executed["record"])["head_hash"]==executed["head_hash"]
    content="# Synthetic checked summary\n"
    import hashlib
    report=report_actions.Store(tmp_path/"reports.db",tmp_path/"output")
    monkeypatch.setattr(report_routes,"STORE",report)
    manifest=dict(schema=report_actions.SCHEMA,agent_id="synthetic-openclaw",task_id="synthetic-report",
                  model="fixture",provider="test",source_name="synthetic source",source_sha256=hashlib.sha256(b"source").hexdigest(),
                  runtime_event_id="synthetic-event",proposal=content,
                  action={"kind":"write_sanitized_summary","target":"summary.md","sha256":hashlib.sha256(content.encode()).hexdigest()},
                  policy=report_actions.POLICY)
    proposed=report.create(manifest)
    report_approved=report.decide(proposed["id"],"approved","Reviewed synthetic exact text","operator",proposed["head_hash"])
    written=report.execute(proposed["id"],"operator",report_approved["head_hash"])
    assert written["state"]=="written" and report.observed_file()["sha256"]==manifest["action"]["sha256"]
    c.link_evidence("syntheticCase","report_action",written["id"],"Synthetic real fixed-file write; digest checked")
    page=TestClient(ui.app).get("http://127.0.0.1/workflows/syntheticCase")
    assert page.status_code==200
    for marker in ("SYNTHETIC EXAMPLE","disagreement","rejected","approval_granted","action_attempted","action_result","policy SHA-256","independent file digest matches","written","independent verification of external output"):
        assert marker.lower() in page.text.lower()


def test_attention_distinguishes_healthy_idle_queue_from_failure_and_keeps_history(tmp_path):
    c=Coordinator(tmp_path/"work.db",clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    c.control("running")
    snap=c.snapshot(); snap["worker"]={"state":"active","pid":123,"heartbeat":1000}
    items=attention(snap,endpoint_reachable=True)
    assert len(items)==1 and items[0]["id"]=="queue:no-eligible"
    assert "worker and endpoint are reachable" in items[0]["cause"]
    assert "paid compute continues" in items[0]["cause"]
    c.record_attention("queue:no-eligible","acknowledged","No approved task exists",{x["id"] for x in items})
    snap=c.snapshot(); snap["worker"]={"state":"active"}
    assert attention(snap)[0]["acknowledgment"]["detail"]=="No approved task exists"
    c.enqueue(review("blockedSource")); dependent=review("dependent"); dependent["dependencies"]=["blockedSource"]; c.enqueue(dependent)
    assert not any(i["id"]=="queue:no-eligible" for i in attention(c.snapshot()))
    c.record_attention("queue:no-eligible","resolved","Eligible work was added",{i["id"] for i in attention(c.snapshot())})
    assert c.snapshot()["attention_history"][-1]["status"]=="resolved"
    with c.db() as db: c._transition(db,"blockedSource","failed","synthetic failure")
    snap=c.snapshot(); snap["worker"]={"state":"active"}
    issues=attention(snap)
    assert any("blocked by dependency" in i["cause"] for i in issues)
    assert any(i["id"]=="request:blockedSource" for i in issues)
    with pytest.raises(ValueError,match="remains active"):
        c.record_attention("request:blockedSource","resolved","Not fixed",{i["id"] for i in issues})


def test_attention_reports_disconnected_agent_and_stalled_work(tmp_path):
    now=[1000]
    c=Coordinator(tmp_path/"work.db",clock=lambda:now[0]); c.configure(rate=1,start=900,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"])); c.update_work("guidance","working")
    now[0]+=301
    participant={"agent_id":"glm-review","status":"Recently disconnected","last_seen_utc":"sample"}
    result=records(c.snapshot(),[participant],now=now[0])[0]
    assert result["state"]=="disconnected" and result["heartbeat"]=="sample"
    issues=attention(c.snapshot(),participants=[participant],now=now[0])
    assert {x["id"] for x in issues}>={"stalled:guidance","agent:glm-review"}


def test_superseded_blocked_reviews_retain_history_and_never_dispatch(tmp_path):
    c=Coordinator(tmp_path/"work.db",clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    c.enqueue(review("failedSource"))
    for name in ("oldReview", "oldReviewV2"):
        spec=review(name); spec["dependencies"]=["failedSource"]; c.enqueue(spec)
    c.enqueue(review("acceptedReview"))
    with c.db() as db:
        c._transition(db,"failedSource","failed","HTTP infrastructure error")
        db.execute("UPDATE tasks SET evidence='Checked against code and tests' WHERE id='acceptedReview'")
        c._transition(db,"acceptedReview","independently_verified","Checked against code and tests")
    for name in ("oldReview", "oldReviewV2"):
        c.supersede_task(name,"acceptedReview","Same contract covered by independently verified review")
    snap=c.snapshot()
    assert [x["state"] for x in snap["tasks"] if x["id"].startswith("oldReview")]==["queued","queued"]
    assert len(snap["task_supersessions"])==2
    c.control("running")
    assert c.claim("worker") is None
    assert any("Queue exhausted" in x["cause"] for x in attention(c.snapshot()))
    with pytest.raises(ValueError,match="independently verified"):
        c.supersede_task("failedSource","oldReview","Cannot accept a queued successor as evidence")


def test_fixed_runtime_onboarding_connection_failure_and_verified_assignment(tmp_path,monkeypatch):
    c=Coordinator(tmp_path/"pilot.db",adapter=lambda spec,rid:{"request_id":rid,"content":"bounded finding","finish_reason":"stop"},clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    with pytest.raises(ValueError,match="Unsupported runtime"):
        c.register_review_runtime("shell-agent",endpoint="http://127.0.0.1:18001",model=MODEL,provider="local-rental",provenance="test")
    c.register_review_runtime("glm-review",endpoint="http://127.0.0.1:18001",model=MODEL,provider="local-rental",provenance="operator registered")
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    monkeypatch.setattr("swarmtracker_node_ui.session_coordinator.LOCAL_OPENER.open",lambda *a,**kw: (_ for _ in ()).throw(OSError("offline")))
    assert c.check_review_runtime("glm-review")["state"]=="unavailable"
    with pytest.raises(ValueError,match="Connect and test"):
        c.assign_first_review("glm-review",workflow_id="pilot",task_id="pilotReview",purpose="Harmless critique",acceptance="One bounded finding",expected="Text")
    monkeypatch.setattr("swarmtracker_node_ui.session_coordinator.LOCAL_OPENER.open",lambda *a,**kw: Response(json.dumps({"data":[{"id":MODEL}]}).encode()))
    assert c.check_review_runtime("glm-review")["state"]=="connected"
    c.assign_first_review("glm-review",workflow_id="pilot",task_id="pilotReview",purpose="Harmless critique",acceptance="One bounded finding",expected="Text")
    assert c.snapshot()["work_sources"]==[{"workflow_id":"pilot","task_id":"pilotReview"}]
    c.control("running"); assert c.run_one("worker")=="pilotReview"
    assert c.snapshot()["tasks"][0]["state"]=="completed"
    c.verify("pilotReview","independent tester","Checked bounded response against original task")
    c.update_work("pilot","finished",checkpoint="bounded finding",evidence="Independent tester checked response")
    assert c.snapshot()["tasks"][0]["state"]=="independently_verified"
    assert c.snapshot()["agent_registrations"][0]["kind"]=="model-endpoint-review"


def test_operator_queue_dependencies_priority_pause_cancel_and_restart(tmp_path):
    path=tmp_path/"queue.db"
    c=Coordinator(path,adapter=lambda spec,rid:{"request_id":rid,"content":spec["purpose"],"finish_reason":"stop"},clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    first=review("first"); second=review("second"); second["dependencies"]=["first"]
    c.create_operator_task(first,priority=0);c.create_operator_task(second,priority=100)
    c.control("running")
    assert c.claim("owner") is None  # Neither draft is authorized.
    c.authorize_operator_task("first","Operator approved first fixed review scope")
    c.authorize_operator_task("second","Operator approved dependent review scope")
    assert c.run_one("owner")=="first"  # Priority never bypasses a dependency.
    c.verify("first","trusted runner","Checked first bounded finding")
    c.edit_operator_task("second",second,pause=True)
    assert c.run_one("owner") is None
    reopened=Coordinator(path,adapter=c.adapter,clock=lambda:1000)
    assert reopened.snapshot()["task_controls"][1]["paused"]==1
    reopened.edit_operator_task("second",second,pause=False,priority=200)
    assert reopened.run_one("owner")=="second"
    assert reopened.snapshot()["tasks"][1]["state"]=="completed"
    third=review("third"); reopened.create_operator_task(third)
    reopened.authorize_operator_task("third","Operator approved third fixed review scope")
    changed={**third,"purpose":"Different consequential scope"}
    reopened.edit_operator_task("third",changed)
    assert reopened.run_one("owner") is None
    reopened.cancel_operator_task("third")
    assert reopened.snapshot()["tasks"][2]["state"]=="cancelled"
    assert [x["action"] for x in reopened.snapshot()["task_audit"] if x["task_id"]=="third"]==["created","authorized","edited","cancel"]


def test_operator_queue_cycle_and_submitted_cancel_preserve_uncertainty(tmp_path):
    c=Coordinator(tmp_path/"queue.db",clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    a=review("cycleA"); b=review("cycleB"); b["dependencies"]=["cycleA"]
    c.create_operator_task(a);c.create_operator_task(b)
    with pytest.raises(ValueError,match="cycle"):
        c.edit_operator_task("cycleA",{**a,"dependencies":["cycleB"]})
    c.authorize_operator_task("cycleA","Operator approved original task contract")
    c.control("running"); assert c.claim("owner")["spec"]["id"]=="cycleA"
    c.cancel_operator_task("cycleA")
    snap=c.snapshot()
    assert snap["tasks"][0]["state"]=="submitted" and snap["task_controls"][0]["cancellation_requested"]==1
    assert "cannot cancel remote" in snap["task_audit"][-1]["detail"]


def test_versioned_review_policy_enforced_on_queued_request(tmp_path):
    c=Coordinator(tmp_path/"policy.db",adapter=lambda spec,rid:{"request_id":rid,"content":"ok","finish_reason":"stop"},clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    task=review("allowed"); c.create_operator_task(task)
    c.authorize_operator_task("allowed","Operator approved bounded review contract")
    allow={"permitted_actions":["glm-review"],"protected_targets":["filesystem","shell"],"require_approval":True,"spending_limit":100}
    allow_hash=c.activate_review_policy(allow)
    c.control("running");assert c.run_one("owner")=="allowed"
    assert c.snapshot()["review_policy_decisions"][0]["policy_hash"]==allow_hash
    denied=review("denied");c.create_operator_task(denied)
    c.authorize_operator_task("denied","Operator approved second fixed review contract")
    deny_hash=c.activate_review_policy({**allow,"permitted_actions":[]})
    assert c.run_one("owner") is None
    snap=c.snapshot()
    assert snap["tasks"][1]["state"]=="queued" and snap["review_policy_decisions"][-1]["allowed"]==0
    assert snap["review_policy_decisions"][0]["policy_hash"]==allow_hash and snap["review_policy_decisions"][-1]["policy_hash"]==deny_hash
    with pytest.raises(ValueError,match="cannot exceed"):
        c.activate_review_policy({**allow,"spending_limit":111})
    c.activate_review_policy(allow)
    assert c.run_one("owner")=="denied"
    assert c.snapshot()["review_policy_decisions"][-1]["allowed"]==1


def test_tightened_approval_policy_blocks_legacy_queue_until_adopted(tmp_path):
    c=Coordinator(tmp_path/"legacy.db",adapter=lambda spec,rid:{"request_id":rid,"content":"ok","finish_reason":"stop"},clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110);c.enqueue(review("legacy"))
    c.activate_review_policy({"permitted_actions":["glm-review"],"protected_targets":["filesystem","shell"],
                              "require_approval":True,"spending_limit":90})
    c.control("running")
    assert c.claim("worker") is None
    assert any("blocked by dependency, authorization" in x["cause"] for x in attention(c.snapshot()))
    c.adopt_queued_task("legacy")
    assert c.claim("worker") is None
    c.authorize_operator_task("legacy","Operator renewed approval for saved legacy scope")
    assert c.run_one("worker")=="legacy"
    assert c.snapshot()["task_audit"][0]["action"]=="adopted"


def test_recovery_carries_policy_and_queue_audit_without_authorization_body(tmp_path):
    from swarmtracker_node_ui.recovery_package import build,load
    c=Coordinator(tmp_path/"queue.db",clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    c.create_operator_task(review("exportable"),priority=9)
    c.authorize_operator_task("exportable","Operator approved bounded scope with no secrets")
    c.activate_review_policy({"permitted_actions":["glm-review"],"protected_targets":["filesystem","shell"],
                              "require_approval":True,"spending_limit":90})
    package=tmp_path/"handoff.zip";anchor=tmp_path/"separate.sha256"
    build(c.snapshot(),package,anchor,source_commit="a"*40)
    loaded=load(package,anchor)
    assert loaded["task_controls"][0]["priority"]==9
    assert loaded["review_policy_versions"][0]["version"]==1
    assert loaded["task_audit"][-1]["detail"]=="withheld"
    assert b"Operator approved bounded scope" not in package.read_bytes()


def test_in_app_notifications_cover_gates_and_keep_acknowledgment(tmp_path):
    c=Coordinator(tmp_path/"notifications.db",clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110);c.control("running")
    c.enqueue_work(json.loads(work("approvalWait")["spec"]));c.update_work("approvalWait","awaiting_approval")
    c.enqueue_work(json.loads(work("stalledWork")["spec"]));c.update_work("stalledWork","working")
    c.enqueue(review("reviewFailure"))
    with c.db() as db:
        db.execute("UPDATE tasks SET evidence='Rejected: independently checked mismatch' WHERE id='reviewFailure'")
        c._transition(db,"reviewFailure","completed","synthetic complete but rejected")
    snap=c.snapshot();snap["worker"]={"state":"absent"}
    ids={x["id"] for x in attention(snap,now=1401)}
    assert {"work:approvalWait","stalled:stalledWork","review:reviewFailure","worker:absent"}<=ids
    c.record_attention("review:reviewFailure","acknowledged","Review mismatch inspected",ids)
    fresh=Coordinator(c.path,clock=lambda:1000)
    issue=next(x for x in attention(fresh.snapshot(),now=1401) if x["id"]=="review:reviewFailure")
    assert issue["acknowledgment"]["status"]=="acknowledged"
    with pytest.raises(ValueError,match="remains active"):
        fresh.record_attention(issue["id"],"resolved","Cannot resolve mismatch",{x["id"] for x in attention(fresh.snapshot(),now=1401)})
    with fresh.db() as db:
        db.execute("UPDATE tasks SET evidence=NULL WHERE id='reviewFailure'")  # Controlled resolution of synthetic fixture.
    ids={x["id"] for x in attention(fresh.snapshot(),now=1401)}
    fresh.record_attention("review:reviewFailure","resolved","Corrected review recorded elsewhere",ids)
    assert not any(x["id"]=="review:reviewFailure" for x in attention(fresh.snapshot(),now=1401))


def test_accepted_successor_resolves_alert_without_rewriting_failed_history(tmp_path):
    c=Coordinator(tmp_path/"alerts.db",clock=lambda:1000)
    c.configure(rate=1,start=900,ceiling=110)
    c.enqueue_work(json.loads(work("oldWork") ["spec"]))
    c.enqueue_work(json.loads(work("newWork") ["spec"]))
    c.update_work("oldWork","blocked")
    assert any(x["id"]=="work:oldWork" for x in attention(c.snapshot()))
    c.record_successor("oldWork","newWork","Corrected requirement with accepted successor")
    c.update_work("newWork","finished",checkpoint="checked source",evidence="Independent assertion passed")
    assert not any(x["id"]=="work:oldWork" for x in attention(c.snapshot()))
    assert next(x for x in c.snapshot()["work_items"] if x["id"]=="oldWork")["state"]=="blocked"


def test_operator_ui_creates_two_dependent_tasks_and_registration(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui, decision_routes
    monkeypatch.setenv("NODE_UI_PRODUCT_DB",str(tmp_path/"ui.db"))
    c=Coordinator(tmp_path/"ui.db",adapter=lambda spec,rid:{"request_id":rid,"content":"safe result","finish_reason":"stop"})
    c.configure(rate=1,start=time.time()-1,ceiling=110)
    client=TestClient(ui.app);base="http://127.0.0.1"
    register={"operator_token":decision_routes.TOKEN,"agent_id":"glm-review","endpoint":"http://127.0.0.1:18001",
              "model":MODEL,"provider":"local-rental","provenance":"Operator configured fixed local rental"}
    assert client.post(base+"/agents/register-review",data=register,follow_redirects=False).status_code==303
    assert "resume conversation False" in client.get(base+"/agents").text
    for task_id,deps in (("firstUI",""),("secondUI","firstUI")):
        form={"operator_token":decision_routes.TOKEN,"permissions":"read-only-review","id":task_id,
              "purpose":"Review harmless synthetic data","acceptance":"One bounded finding","expected":"Text finding",
              "validation":"Independent tester review","dependencies":deps,"priority":"10"}
        assert client.post(base+"/tasks",data=form,follow_redirects=False).status_code==303
        assert client.post(base+"/tasks/"+task_id+"/control",data={"operator_token":decision_routes.TOKEN,"action":"authorize",
                           "evidence":"Operator approved the saved harmless review contract"},follow_redirects=False).status_code==303
    assert "Dependencies awaiting independent verification" in client.get(base+"/tasks").text
    c.control("running");assert c.run_one("worker")=="firstUI"
    c.verify("firstUI","trusted runner","Compared actual response to saved criteria")
    assert c.run_one("worker")=="secondUI"
    c.verify("secondUI","trusted runner","Compared dependent response to accepted predecessor")
    assert {x["id"] for x in c.snapshot()["tasks"]}=={"firstUI","secondUI"}


def test_disposable_demo_rejection_approval_execution_export_and_reset(tmp_path,monkeypatch):
    from swarmtracker_node_ui import product_demo,decision_runs,report_actions
    monkeypatch.setenv("NODE_UI_DEMO_PROFILE","1")
    monkeypatch.setenv("NODE_UI_DEMO_ROOT",str(tmp_path))
    monkeypatch.setenv("NODE_UI_PRODUCT_DB",str(tmp_path/"product.db"))
    c=Coordinator(tmp_path/"product.db")
    decisions=decision_runs.RunStore(tmp_path/"decisions.db")
    reports=report_actions.Store(tmp_path/"reports.db",tmp_path/"output")
    one=product_demo.setup(c,decisions,reports)
    assert decisions.get(one["rejected"])["decision"]=="rejected"
    settings={"model":"glm-5.3-flash","reasoning_effort":"low","chat_template_kwargs":{"reasoning_effort":"low"},"max_tokens":2048}
    review=dict(schema="verity_demo_review/v1",task_id="demoConflictReview",request_id="a"*32,
                model="glm-5.3-flash",finish_reason="stop",content="The directory conflicts; confirm out of band.",
                settings=settings,verifier="trusted-runner",evidence="Compared model finding to fixed synthetic evidence",usage={"total_tokens":100})
    with pytest.raises(ValueError,match="Unsupported demo review"):
        product_demo.attach_review(c,one["workflow"],dict(review,finish_reason="length"))
    product_demo.attach_review(c,one["workflow"],review)
    product_demo.attach_review(c,one["workflow"],review)
    assert len([x for x in c.snapshot()["workflow_links"] if x["workflow_id"]==one["workflow"] and x["kind"]=="session_task"])==1
    with pytest.raises(ValueError,match="approval required"):
        product_demo.finish(c,one["workflow"],decisions,reports)
    pending=decisions.get(one["pending"])
    decisions.authorize(pending["id"],"approved","Synthetic scoped approval","synthetic-operator",pending["head_hash"])
    proposed=reports.get(one["proposal"])
    approved=reports.decide(proposed["id"],"approved","Reviewed exact synthetic output","synthetic-operator",proposed["head_hash"])
    written=reports.execute(approved["id"],"synthetic-operator",approved["head_hash"])
    assert written["state"]=="written"
    export,anchor=product_demo.finish(c,one["workflow"],decisions,reports)
    assert product_demo.verify_export(export,anchor)
    bundle=json.loads(export.read_text())
    assert bundle["model_review"]["request_id"]=="a"*32
    assert bundle["roles"]["reviewer"].startswith("live GLM review")
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui,decision_routes,report_routes
    monkeypatch.setattr(decision_routes,"RUNS",decisions)
    monkeypatch.setattr(report_routes,"STORE",reports)
    page=TestClient(ui.app,base_url="http://127.0.0.1").get("/workflows/"+one["workflow"])
    assert page.status_code==200 and "Live model review of SYNTHETIC evidence" in page.text
    assert "a"*32 in page.text and "confirm out of band" in page.text
    assert c.snapshot()["work_items"][0]["state"]=="finished"
    two=product_demo.setup(c,decisions,reports)
    assert two["workflow"]!=one["workflow"] and export.exists() and anchor.exists()
    anchor.write_text("0"*64)
    assert not product_demo.verify_export(export,anchor)


def test_disposable_demo_ui_requires_approval_before_export(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui, decision_routes, report_routes, product_demo
    monkeypatch.setenv("NODE_UI_DEMO_PROFILE","1")
    monkeypatch.setenv("NODE_UI_DEMO_ROOT",str(tmp_path))
    monkeypatch.setenv("NODE_UI_PRODUCT_DB",str(tmp_path/"product.db"))
    client=TestClient(ui.app);base="http://127.0.0.1"
    assert client.get(base+"/demo").status_code==200
    start=client.post(base+"/demo/new",data={"operator_token":decision_routes.TOKEN},follow_redirects=False)
    assert start.status_code==303
    work_id=start.headers["location"].split("/")[-1]
    assert "SYNTHETIC EXAMPLE" in client.get(base+start.headers["location"]).text
    blocked=client.post(base+"/demo/"+work_id+"/finish",data={"operator_token":decision_routes.TOKEN})
    assert blocked.status_code==409
    snap=Coordinator(tmp_path/"product.db").snapshot()
    links=[x for x in snap["workflow_links"] if x["workflow_id"]==work_id]
    pending=next(decision_routes.RUNS.get(x["target_id"]) for x in links if x["kind"]=="decision" and decision_routes.RUNS.get(x["target_id"])["decision"]=="pending")
    decision_routes.RUNS.authorize(pending["id"],"approved","Synthetic review accepted","operator",pending["head_hash"])
    proposed=report_routes.STORE.get(next(x["target_id"] for x in links if x["kind"]=="report_action"))
    approved=report_routes.STORE.decide(proposed["id"],"approved","Synthetic exact text approved","operator",proposed["head_hash"])
    report_routes.STORE.execute(approved["id"],"operator",approved["head_hash"])
    assert client.post(base+"/demo/"+work_id+"/finish",data={"operator_token":decision_routes.TOKEN},follow_redirects=False).status_code==303
    history=client.get(base+"/demo/"+work_id+"/export/history")
    checkpoint=client.get(base+"/demo/"+work_id+"/export/checkpoint")
    assert history.status_code==200 and checkpoint.status_code==200
    assert product_demo.verify_export(tmp_path/(work_id+".json"),tmp_path/(work_id+".sha256"))


def test_portable_handoff_separate_process_and_integrity(tmp_path):
    import subprocess,sys,os,zipfile
    from swarmtracker_node_ui.recovery_package import build,load
    c=Coordinator(tmp_path/"source.db",adapter=lambda s,r: dict(request_id=r,content="private response body",finish_reason="stop"))
    c.configure(rate=1,start=time.time()-5,ceiling=110)
    c.enqueue_work(json.loads(work()["spec"])); c.enqueue(review("source"))
    c.control("running"); c.run_one("operator")
    c.verify("source","trusted-runner","Independent synthetic output check")
    package=tmp_path/"handoff.zip"; checkpoint=tmp_path/"separate"/"handoff.sha256"
    build(c.snapshot(),package,checkpoint,source_commit="a"*40,reviewed_outputs={"safe-result.txt":"Synthetic checked output"})
    with zipfile.ZipFile(package) as archive:
        content=archive.read("recovery.json").decode()
        assert "private response body" not in content and "Synthetic checked output" not in content
        assert "outputs/safe-result.txt" in archive.namelist()
    env={**os.environ,"PYTHONPATH":"apps/node"}
    result=subprocess.run([sys.executable,"-m","swarmtracker_node_ui.recovery_package",str(package),str(checkpoint)],
                          capture_output=True,text=True,env=env,check=True)
    reconstructed=json.loads(result.stdout)
    assert reconstructed["request_states"]["independently_verified"]==["source"]
    assert reconstructed["work_items"][0]["spec"]["authorization"]=="explicit-product-development"
    assert reconstructed["next_eligible_work"]==["guidance"]
    assert reconstructed["available_outputs"]["safe-result.txt"]["sha256"]
    assert reconstructed["control"]["ceiling"]==110
    assert load(package,checkpoint)["raw_request_bodies_available"] is False
    package.write_bytes(package.read_bytes()+b"tamper")
    with pytest.raises(ValueError,match="differs"): load(package,checkpoint)


def test_portable_handoff_refuses_secret_output(tmp_path):
    from swarmtracker_node_ui.recovery_package import build
    c=Coordinator(tmp_path/"source.db"); c.configure(rate=1,start=time.time()-5,ceiling=110)
    with pytest.raises(ValueError,match="credential"):
        build(c.snapshot(),tmp_path/"handoff.zip",tmp_path/"checkpoint.sha256",source_commit="a"*40,
              reviewed_outputs={"unsafe.txt":"api_key=exposed"})
