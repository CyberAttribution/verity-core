import json
import time
import threading
import multiprocessing
import os

import pytest

from apps.node.swarmtracker_node_ui.session_coordinator import Coordinator, MODEL, _NoRedirect, glm_review
from apps.node.swarmtracker_node_ui.sessions import SessionStore,derive


def spec(name, deps=()):
    return dict(id=name,purpose="Review this bounded source contract",dependencies=list(deps),
                authorization="explicit-read-only-glm-review",acceptance_criteria="Identify a concrete defect",
                model_settings=dict(model=MODEL,reasoning_effort="low",chat_template_kwargs={"reasoning_effort":"low"},max_tokens=2048),
                expected_outputs="Short review",validation_gates="Human checks source and tests before acceptance")


def test_restart_and_independent_gate(tmp_path):
    path=tmp_path/"queue.db"
    def adapter(s,r): return dict(request_id=r,content="Finding",finish_reason="stop",usage={"completion_tokens":3})
    c=Coordinator(path,adapter=adapter)
    c.configure(rate=22.32,start=time.time()-10,ceiling=110)
    c.enqueue(spec("one"));c.enqueue(spec("two",["one"]));c.control("running")
    assert c.run_one("worker-a") == "one"
    assert c.snapshot()["tasks"][0]["state"] == "completed"
    restarted=Coordinator(path,adapter=adapter)
    assert restarted.run_one("worker-a") is None
    restarted.verify("one","human","Source line and executed test checked")
    assert restarted.run_one("worker-a") == "two"
    assert [x["state"] for x in restarted.snapshot()["tasks"]] == ["independently_verified","completed"]


def test_uncertain_restart_not_replayed(tmp_path):
    calls=[]
    c=Coordinator(tmp_path/"q.db",adapter=lambda s,r: calls.append(r))
    c.configure(rate=1,start=time.time()-5,ceiling=100)
    c.enqueue(spec("one"));c.control("running")
    assert c.claim("dead")["spec"]["id"] == "one"
    # A stale lease lets a new process recover, but remote ambiguity blocks replay.
    with c.db() as db: db.execute("UPDATE control SET heartbeat=0")
    assert c.run_one("new") is None
    assert c.snapshot()["tasks"][0]["state"] == "unresolved"
    assert not calls


def test_same_owner_stale_restart_and_redirect_refusal(tmp_path):
    c=Coordinator(tmp_path/"q.db",adapter=lambda s,r: (_ for _ in ()).throw(AssertionError("replayed")))
    c.configure(rate=1,start=time.time()-5,ceiling=100)
    c.enqueue(spec("one"));c.control("running")
    c.claim("reused")
    another=Coordinator(tmp_path/"q.db",adapter=lambda s,r: (_ for _ in ()).throw(AssertionError("concurrent")))
    assert another.claim("reused") is None  # identical caller label is a different process lease
    with c.db() as db: db.execute("UPDATE control SET heartbeat=0")
    assert another.run_one("reused") is None
    assert c.snapshot()["tasks"][0]["state"] == "unresolved"
    assert _NoRedirect().redirect_request(None,None,302,"redirect",{},"https://example.com") is None
    with pytest.raises(ValueError): glm_review(spec("safe"),"request",endpoint="http://127.0.0.1:18001/v1/chat/completions?forward=https://example.com")


def test_truncation_pause_budget_and_failure(tmp_path):
    now=[1000.]
    c=Coordinator(tmp_path/"q.db",adapter=lambda s,r: dict(request_id=r,content="partial",finish_reason="length"),clock=lambda:now[0])
    c.configure(rate=36,start=900,ceiling=10,margin=1)
    c.enqueue(spec("one"));c.control("running");c.control("paused")
    assert c.run_one("w") is None
    c.control("running")
    now[0]=1800
    assert c.run_one("w") is None  # 9 USD, margin is respected
    now[0]=1000
    assert c.run_one("w") == "one"
    assert c.snapshot()["tasks"][0]["state"] == "failed"
    with pytest.raises(ValueError): c.verify("one","human","Cannot accept truncation")


def test_restart_preserves_rental_budget_gate(tmp_path):
    now=[1000.]
    path=tmp_path/"q.db"
    c=Coordinator(path,adapter=lambda s,r:dict(request_id=r,content="ok",finish_reason="stop"),clock=lambda:now[0])
    c.configure(rate=36,start=900,ceiling=10,margin=1)
    c.enqueue(spec("one"));c.control("running")
    assert c.run_one("w") == "one"
    c.verify("one","human","Checked result against task contract")
    c.enqueue(spec("two",["one"]))
    now[0]=1800
    fresh=Coordinator(path,adapter=lambda s,r: (_ for _ in ()).throw(AssertionError("dispatched")),clock=lambda:now[0])
    assert fresh.run_one("new") is None
    assert fresh.snapshot()["tasks"][1]["state"] == "queued"
    with pytest.raises(ValueError): fresh.configure(rate=36,start=900,ceiling=100)


def test_error_ambiguous_and_authorization(tmp_path):
    def interrupted(s,r): raise TimeoutError("after send")
    c=Coordinator(tmp_path/"q.db",adapter=interrupted)
    c.configure(rate=1,start=time.time()-5,ceiling=100)
    bad=spec("bad");bad["authorization"]="arbitrary-shell"
    with pytest.raises(ValueError): c.enqueue(bad)
    c.enqueue(spec("one"));c.control("running")
    c.run_one("w")
    assert c.snapshot()["tasks"][0]["state"] == "unresolved"
    c.reconcile("one","failed","Endpoint log searched; no matching result")
    assert c.run_one("w") is None
    c.retry_confirmed_not_executed("one","Endpoint log confirms no matching request execution")
    c.control("paused")
    assert c.run_one("w") is None
    c.control("running")
    c.run_one("w")
    assert c.snapshot()["tasks"][0]["attempts"] == 2
    assert c.snapshot()["tasks"][0]["state"] == "unresolved"
    with pytest.raises(ValueError): c.retry_confirmed_not_executed("one","No execution claimed without reconciliation")


def test_metadata_only_telemetry_outage_and_truncation(tmp_path):
    seen=[]
    emitted=threading.Event()
    def observer(item):
        seen.append(item)
        emitted.set()
        raise ConnectionError("OTLP unavailable")
    def model(s,r): return {"request_id":r,"content":"secret response","finish_reason":"length","usage":{"prompt_tokens":7,"completion_tokens":2,"credential":"SECRET_TOKEN"}}
    c=Coordinator(tmp_path/"q.db",adapter=model,telemetry=observer,session_id="synthetic")
    c.configure(rate=1,start=time.time()-5,ceiling=100)
    c.enqueue(spec("one"));c.control("running");c.run_one("w")
    assert emitted.wait(2)
    assert c.snapshot()["tasks"][0]["state"] == "failed"
    assert seen[0]["session_id"] == "synthetic" and seen[0]["task_id"] == "one"
    assert seen[0]["finish_reason"] == "length" and seen[0]["usage"]["completion_tokens"] == 2
    assert "secret response" not in str(seen) and "Review this bounded" not in str(seen)
    assert "SECRET_TOKEN" not in str(seen)


def test_gpu_samples_stale_without_outcome(tmp_path):
    s=SessionStore(tmp_path/"sessions.db")
    s.create(dict(id="sample",purpose="Review",provider="rental",macs=[],branch="local",commits=[]))
    at=time.time()
    s.append("sample",dict(id="gpu1",at=at,kind="gpu",source="test",data=dict(index=0,uuid="GPU-123",utilization_pct=80,memory_used_mib=2,memory_total_mib=80)))
    view=derive(s.get("sample"),now=at+1000,stale_after=1)
    assert view["gpu_samples"][0]["state"] == "stale"
    assert view["tasks"] == []


def _crash_after_submit(path):
    worker=Coordinator(path)
    worker.register_worker("crashed",os.getpid())
    worker.claim("crashed")  # Request is durable, but network outcome is ambiguous.
    os._exit(27)


def test_unexpected_exit_stale_status_and_safe_operator_recovery(tmp_path):
    path=tmp_path/"crash.db"
    now=[time.time()]
    c=Coordinator(path,clock=lambda:now[0])
    c.configure(rate=1,start=now[0]-1,ceiling=100)
    c.enqueue(spec("ambiguous"))
    c.enqueue(spec("eligible"))
    c.control("running")
    child=multiprocessing.Process(target=_crash_after_submit,args=(path,))
    child.start();child.join(5)
    assert child.exitcode == 27
    assert c.snapshot()["control"]["mode"] == "running"
    assert c.worker_status()["state"] == "stale"  # Mode is not process liveness.
    assert c.snapshot()["tasks"][0]["state"] == "submitted"
    # The spawned child uses real time; expire its actual persisted lease,
    # rather than assuming process startup completed within one second.
    with c.db() as db:
        heartbeat=db.execute("SELECT heartbeat FROM control WHERE id=1").fetchone()[0]
    now[0]=heartbeat+31
    dispatched=[]
    recovered=Coordinator(path,adapter=lambda s,r: (dispatched.append(s["id"]) or
                    {"request_id":r,"content":"review","finish_reason":"stop"}),clock=lambda:now[0])
    recovered.register_worker("recovered",os.getpid())
    assert recovered.run_one("recovered") == "eligible"
    assert dispatched == ["eligible"]
    assert [x["state"] for x in recovered.snapshot()["tasks"][:2]] == ["unresolved","completed"]
    recovered.unregister_worker("recovered")
    assert recovered.worker_status()["state"] == "absent"


def test_stale_submission_reconciled_while_paused_and_budget_gated(tmp_path):
    now=[1000.]
    c=Coordinator(tmp_path/"q.db",clock=lambda:now[0],adapter=lambda s,r: (_ for _ in ()).throw(AssertionError("replay")))
    c.configure(rate=36,start=900,ceiling=20,margin=1)
    c.enqueue(spec("ambiguous"));c.control("running")
    c.claim("dead")
    c.control("paused")
    now[0]=3000.  # Rental estimate is well beyond the dispatch budget.
    assert c.recover_stale()
    assert c.snapshot()["tasks"][0]["state"] == "unresolved"
    assert c.snapshot()["control"]["mode"] == "paused"
    assert c.run_one("new") is None


def test_inaccessible_live_pid_is_not_replaced(tmp_path,monkeypatch):
    now=[time.time()]
    c=Coordinator(tmp_path/"q.db",clock=lambda:now[0])
    c.register_worker("first",os.getpid())
    monkeypatch.setattr(os,"kill",lambda pid,sig: (_ for _ in ()).throw(PermissionError()))
    assert c.worker_status()["state"] == "active"
    with pytest.raises(RuntimeError): c.register_worker("second",os.getpid())
    now[0]+=31
    assert c.worker_status()["state"] == "stale"
    c.register_worker("second",os.getpid())


def test_read_only_coordinator_export_survives_restart_without_mutation(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from apps.node.swarmtracker_node_ui import sessions

    session_store=SessionStore(tmp_path/"sessions.db")
    session_store.create(dict(id="synthetic",purpose="Review",provider="test",macs=[],branch="local",commits=[]))
    path=tmp_path/"coordinator.db"
    c=Coordinator(path,adapter=lambda s,r:{"request_id":r,"content":"checked","finish_reason":"stop"})
    c.configure(rate=1,start=time.time()-5,ceiling=100)
    c.enqueue(spec("first"));c.control("running");c.run_one("w")
    c.verify("first","human","Checked source and test")
    c.register_worker("live",os.getpid())
    monkeypatch.setenv("NODE_UI_COORDINATOR_DB",str(path))
    monkeypatch.setenv("NODE_UI_COORDINATOR_SESSION_ID","synthetic")
    monkeypatch.setattr(sessions,"SessionStore",lambda:session_store)
    app=FastAPI()
    sessions.install(app,lambda *args: None,lambda *args: {})
    client=TestClient(app)
    before=len(c.snapshot()["transitions"])
    first=client.get("/sessions/synthetic/coordinator.json")
    assert first.status_code == 200
    exported=first.json()
    assert exported["worker"]["state"] == "active" and exported["worker"]["pid"] == os.getpid()
    assert exported["tasks"][0]["state"] == "independently_verified"
    assert exported["tasks"][0]["response"] and exported["tasks"][0]["request_id"]
    assert exported["transitions"][-1]["state"] == "independently_verified"
    assert client.post("/sessions/synthetic/coordinator.json").status_code == 405
    assert len(Coordinator(path).snapshot()["transitions"]) == before
    assert client.get("/sessions/synthetic/coordinator.json").json()["tasks"] == exported["tasks"]
    c.unregister_worker("live")


def test_persisted_deadline_blocks_new_dispatch_and_survives_restart(tmp_path):
    now=[1000.0];calls=[]
    c=Coordinator(tmp_path/'deadline.db',clock=lambda:now[0],adapter=lambda s,r:calls.append(r))
    c.configure(rate=1,start=990,ceiling=100,deadline=1010)
    c.enqueue(spec('held'));c.control('running')
    now[0]=1010
    restarted=Coordinator(c.path,clock=lambda:now[0],adapter=c.adapter)
    assert restarted.run_one('worker') is None and not calls
    assert restarted.snapshot()['tasks'][0]['state']=='queued'
    assert restarted.dispatch_gate(restarted.snapshot()['control'])=='deadline reached'
    restarted.control('paused')
    assert restarted.dispatch_gate(restarted.snapshot()['control'])=='deadline reached'
    with pytest.raises(ValueError):restarted.configure(rate=1,start=990,ceiling=100,deadline=1020)


def test_run_worker_registers_and_drains_independent_work_then_holds_dependency(tmp_path):
    from tools.session_worker import run_worker
    seen=[]
    c=None
    def adapter(s,r):
        seen.append((s['id'],c.worker_status()['state']))
        return dict(request_id=r,content='synthetic contract check',finish_reason='stop')
    c=Coordinator(tmp_path/'worker.db',adapter=adapter)
    c.configure(rate=1,start=time.time()-1,ceiling=100,deadline=time.time()+60)
    c.enqueue(spec('first'));c.enqueue(spec('dependent',['first']));c.enqueue(spec('independent'))
    c.control('running');run_worker(c)
    assert seen==[('first','active'),('independent','active')]
    assert c.worker_status()['state']=='absent'
    assert [x['state']for x in c.snapshot()['tasks']]==['completed','queued','completed']
    assert [x['attempts']for x in c.snapshot()['tasks']]==[1,0,1]
    assert c.snapshot()['tasks'][0]['verifier'] is None
