import json
import time
import threading

import pytest

from apps.node.swarmtracker_node_ui.session_coordinator import Coordinator, MODEL
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
