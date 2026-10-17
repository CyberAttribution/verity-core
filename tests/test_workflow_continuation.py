import json
import time

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
    c=Coordinator(tmp_path/"work.db")
    c.enqueue_work(json.loads(work()["spec"]))
    c.update_work("guidance","awaiting_approval")
    with pytest.raises(ValueError,match="recorded human"):
        c.update_work("guidance","working")
    c.approve_work("guidance","local-operator","Scoped synthetic approval record")
    c.update_work("guidance","working")
    assert c.snapshot()["work_approvals"][0]["actor"]=="local-operator"


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
    with c.db() as db: c._transition(db,"blockedSource","failed","synthetic failure")
    snap=c.snapshot(); snap["worker"]={"state":"active"}
    issues=attention(snap)
    assert any("blocked by unverified or failed dependencies" in i["cause"] for i in issues)
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
    assert load(package,checkpoint)["raw_request_bodies_available"] is False
    package.write_bytes(package.read_bytes()+b"tamper")
    with pytest.raises(ValueError,match="differs"): load(package,checkpoint)


def test_portable_handoff_refuses_secret_output(tmp_path):
    from swarmtracker_node_ui.recovery_package import build
    c=Coordinator(tmp_path/"source.db"); c.configure(rate=1,start=time.time()-5,ceiling=110)
    with pytest.raises(ValueError,match="credential"):
        build(c.snapshot(),tmp_path/"handoff.zip",tmp_path/"checkpoint.sha256",source_commit="a"*40,
              reviewed_outputs={"unsafe.txt":"api_key=exposed"})
