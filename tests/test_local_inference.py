import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig
from apps.node.swarmtracker_node_ui.local_inference import LocalInference


def setup(tmp_path):
    config = CustomerConfig(tmp_path / "customer-config.db")
    config.save("resource", "report", dict(description="Disposable report", provenance="operator-entered", status="unknown"))
    config.save("connection", "openclaw", dict(provider="openclaw", endpoint="http://127.0.0.1:8090",
        model="qwen2.5:7b-instruct", revision="local-pilot", scope="local", credential_ref="none"))
    config.save("agent", "existingOpenClaw", dict(connection_id="openclaw", instructions="Summarize the specimen.",
        allowed_tools=["generate_text"], resources=["report"], external_sharing="none", fallback_id=""))
    return config, LocalInference(tmp_path / "runs.db", config)


def test_allowed_and_denied_routes_are_durable_and_do_not_redispatch(tmp_path):
    config, runs = setup(tmp_path)
    seen = []
    def dispatch(cfg, prompt, purpose):
        seen.append((cfg["model"], prompt, purpose))
        return dict(model=cfg["model"], response="A bounded result.", event_id="evt-1",
                    decision="allow", provider="ollama_local", done=True, done_reason="stop")
    base = dict(agent_id="existingOpenClaw", resource_id="report", purpose="generate_text",
                prompt="Summarize this.", requested_model="")
    allowed = runs.run(task_id="task-1", dispatch=dispatch, **base)
    assert allowed["state"] == "completed"
    assert allowed["configuration"]["agent_version"] == 1
    assert allowed["result"]["runtime_event_id"] == "evt-1"
    assert allowed["result"]["response"] == "A bounded result."
    wrong = runs.run(task_id="task-2", dispatch=dispatch, **dict(base, requested_model="other"))
    purpose = runs.run(task_id="task-3", dispatch=dispatch, **dict(base, purpose="draft_text"))
    assert wrong["state"] == purpose["state"] == "denied"
    assert len(seen) == 1
    with pytest.raises(sqlite3.IntegrityError):
        runs.run(task_id="task-1", dispatch=dispatch, **base)
    assert len(seen) == 1
    assert [x["state"] for x in LocalInference(tmp_path / "runs.db", config).recent()] == [
        "denied", "denied", "completed"]
    assert "Summarize this." not in json.dumps(runs.recent())


def test_runtime_model_mismatch_is_failed_and_version_history_survives(tmp_path):
    config, runs = setup(tmp_path)
    base = dict(agent_id="existingOpenClaw", resource_id="report", purpose="generate_text",
                prompt="Bounded request", requested_model="")
    failed = runs.run(task_id="task-1", dispatch=lambda *_: dict(model="wrong", response="text"), **base)
    assert failed["state"] == "failed" and failed["result"] is None
    agent = config.latest("agent", "existingOpenClaw")["body"]
    config.save("agent", "existingOpenClaw", dict(agent, instructions="New instruction"))
    assert runs.recent()[0]["configuration"]["agent_version"] == 1
    newer = runs.run(task_id="task-2", dispatch=lambda c, *_: dict(
        model=c["model"], response="Complete", id="evt-2", decision="allowed",
        done=True, done_reason="stop"), **base)
    assert newer["configuration"]["agent_version"] == 2
    assert newer["state"] == "completed"
    runtime_denial = runs.run(task_id="task-3", dispatch=lambda c, *_: dict(
        model=c["model"], response="Refused", id="evt-3", decision="denied",
        done=True), **base)
    assert runtime_denial["state"] == "failed" and runtime_denial["result"] is None
    missing_done = runs.run(task_id="task-4", dispatch=lambda c, *_: dict(
        model=c["model"], response="Partial", id="evt-4", decision="allowed"), **base)
    assert missing_done["state"] == "failed" and missing_done["result"] is None
    omitted_reason = runs.run(task_id="omitted-finish", dispatch=lambda c, *_: dict(
        model=c["model"], response="Complete", id="evt-omitted", decision="allowed",
        done=True), **base)
    assert omitted_reason["state"] == "completed" and omitted_reason["result"]["finish_reason"] is None
    for reason in ("length", "cancelled", "error"):
        incomplete = runs.run(task_id="bad-finish-" + str(reason), dispatch=lambda c, *_: dict(
            model=c["model"], response="Looks complete", id="evt-bad", decision="allowed",
            done=True, done_reason=reason), **base)
        assert incomplete["state"] == "failed" and incomplete["result"] is None


def test_native_endpoint_records_denial_without_runtime(tmp_path, monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "profile.db")
    setup(tmp_path)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    form = dict(operator_token=decision_routes.TOKEN, agent_id="existingOpenClaw",
                task_id="denied-ui", resource_id="report", purpose="draft_text",
                prompt="Please draft", requested_model="", session_id="")
    response = client.post("/configuration/inference", data=form)
    assert response.status_code == 200
    assert response.json()["state"] == "denied"
    assert "denied-ui" in client.get("/configuration").text


def test_disabled_connection_denies_before_dispatch_and_preserves_history(tmp_path):
    config, runs = setup(tmp_path)
    old = config.latest("connection", "openclaw")
    disabled = config.disable_connection("openclaw")
    assert disabled["reason"] == "Operator removed model connection"
    assert config.latest("connection", "openclaw") == old
    seen = []
    result = runs.run(agent_id="existingOpenClaw", task_id="disabled-01", resource_id="report",
                      purpose="generate_text", prompt="Bounded", dispatch=lambda *_: seen.append(1))
    assert result["state"] == "denied" and result["decision"] == "denied" and not seen
    assert LocalInference(tmp_path / "runs.db", config).recent()[0]["state"] == "denied"
    with pytest.raises(ValueError):
        config.save("connection", "openclaw", old["body"])


def test_connection_disabled_after_reservation_fails_without_dispatch(tmp_path, monkeypatch):
    config, runs = setup(tmp_path)
    original = config.connection_disabled
    checks = []
    def disabled(identifier):
        checks.append(identifier)
        if len(checks) == 2:
            config.disable_connection(identifier)
        return original(identifier)
    monkeypatch.setattr(config, "connection_disabled", disabled)
    seen = []
    result = runs.run(agent_id="existingOpenClaw", task_id="disabled-race-01", resource_id="report",
                      purpose="generate_text", prompt="Bounded", dispatch=lambda *_: seen.append(1))
    assert result["state"] == "failed" and not seen


def test_operator_can_disable_connection_in_configuration_page(tmp_path, monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "profile.db")
    config, _ = setup(tmp_path)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    response = client.post("/configuration/connections/openclaw/disable",
                           data={"operator_token": decision_routes.TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    assert config.connection_disabled("openclaw")
    assert "disabled permanently" in client.get("/configuration").text
    assert client.post("/configuration/connections/openclaw/check",
                       data={"operator_token": decision_routes.TOKEN}).status_code == 409


def test_daily_agent_cap_is_atomic_durable_and_versioned(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    config, runs = setup(tmp_path)
    agent = config.latest("agent", "existingOpenClaw")["body"]
    new = config.save("agent", "existingOpenClaw", dict(agent, daily_request_limit=1))
    assert new["version"] == 2 and config.effective("existingOpenClaw", "report")["daily_request_limit"] == 1
    seen = []
    def dispatch(cfg, prompt, purpose):
        seen.append(cfg["agent_version"])
        return dict(model=cfg["model"],response="Complete",id="event-"+prompt,
                    decision="allowed",done=True,done_reason="stop")
    def run(i):
        return runs.run(agent_id="existingOpenClaw",task_id="daily-"+str(i),resource_id="report",
                        purpose="generate_text",prompt=str(i),dispatch=dispatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(run,(1,2)))
    assert sorted(x["state"] for x in outcomes) == ["completed","denied"]
    assert len(seen)==1
    assert any(x["reason"]=="Daily agent request cap reached before dispatch" for x in outcomes)
    after_restart=LocalInference(tmp_path / "runs.db",config)
    denied=after_restart.run(agent_id="existingOpenClaw",task_id="daily-3",resource_id="report",
                             purpose="generate_text",prompt="3",dispatch=dispatch)
    assert denied["state"]=="denied" and len(seen)==1
    assert config.latest("agent","existingOpenClaw")["version"]==2
    with pytest.raises(ValueError):
        config.save("agent","existingOpenClaw",dict(agent,daily_request_limit=-1))


def test_session_cap_survives_restart_and_serializes_concurrent_reservations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    config,runs=setup(tmp_path)
    agent=config.latest("agent","existingOpenClaw")["body"]
    config.save("agent","existingOpenClaw",dict(agent,session_request_limit=1))
    seen=[]
    def dispatch(cfg,prompt,purpose):
        seen.append(prompt)
        return dict(model=cfg["model"],response="Complete",id="evt-"+prompt,decision="allowed",done=True)
    def run(i,session):
        return runs.run(agent_id="existingOpenClaw",task_id="session-"+str(i),resource_id="report",
                        purpose="generate_text",prompt=str(i),session_id=session,dispatch=dispatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda i:run(i,"bounded-01"),(1,2)))
    assert sorted(x["state"] for x in outcomes)==["completed","denied"]
    assert len(seen)==1
    assert "Session agent request cap" in next(x["reason"] for x in outcomes if x["state"]=="denied")
    restarted=LocalInference(tmp_path/"runs.db",config)
    assert restarted.run(agent_id="existingOpenClaw",task_id="session-3",resource_id="report",
                         purpose="generate_text",prompt="3",session_id="bounded-01",dispatch=dispatch)["state"]=="denied"
    assert restarted.run(agent_id="existingOpenClaw",task_id="session-4",resource_id="report",
                         purpose="generate_text",prompt="4",dispatch=dispatch)["state"]=="denied"
    assert run(5,"bounded-02")["state"]=="completed"
    assert len(seen)==2
    assert config.effective("existingOpenClaw","report")["session_request_limit"]==1
    with pytest.raises(ValueError):
        config.save("agent","existingOpenClaw",dict(agent,session_request_limit=-1))


def test_unavailable_or_stale_connection_denies_before_dispatch_until_recheck(tmp_path):
    config,runs=setup(tmp_path)
    seen=[]
    def dispatch(cfg,prompt,purpose):
        seen.append(1)
        return dict(model=cfg["model"],response="Complete",id="evt",decision="allowed",done=True)
    base=dict(agent_id="existingOpenClaw",resource_id="report",purpose="generate_text",
              prompt="Bounded",dispatch=dispatch)
    config.observe("connection","openclaw","unavailable","Health request failed")
    unavailable=runs.run(task_id="stale-1",**base)
    assert unavailable["state"]=="denied" and not seen
    config.observe("connection","openclaw","reachable","Loopback health only")
    with config._db() as db:
        db.execute("UPDATE observations SET at=0 WHERE kind='connection' AND id='openclaw'")
    stale=runs.run(task_id="stale-2",**base)
    assert stale["state"]=="denied" and not seen
    config.observe("connection","openclaw","reachable","Loopback health only")
    allowed=runs.run(task_id="stale-3",**base)
    assert allowed["state"]=="completed" and len(seen)==1
    assert allowed["result"]["runtime_event_id"]=="evt"


def test_observation_becomes_unavailable_after_reservation_before_dispatch(tmp_path,monkeypatch):
    config,runs=setup(tmp_path)
    original=config.observations
    checks=[]
    def observations():
        checks.append(1)
        if len(checks)==2:
            config.observe("connection","openclaw","unavailable","Changed")
        return original()
    monkeypatch.setattr(config,"observations",observations)
    seen=[]
    result=runs.run(agent_id="existingOpenClaw",task_id="stale-race",resource_id="report",
                    purpose="generate_text",prompt="Bounded",dispatch=lambda *_:seen.append(1))
    assert result["state"]=="failed" and not seen


def test_remote_peer_with_spoofed_loopback_host_cannot_read_or_change_configuration(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    monkeypatch.setattr(store,"DB_PATH",tmp_path/"profile.db")
    config,_=setup(tmp_path)
    remote=TestClient(ui.app,base_url="http://127.0.0.1",client=("203.0.113.15",43700))
    assert remote.get("/configuration").status_code==403
    assert remote.post("/configuration/connections/openclaw/disable",
         data=dict(operator_token=decision_routes.TOKEN)).status_code==403
    assert remote.post("/configuration/inference",
         data=dict(operator_token=decision_routes.TOKEN,agent_id="existingOpenClaw",
                   task_id="remote-01",resource_id="report",purpose="generate_text",
                   prompt="Attempt",requested_model="",session_id="")).status_code==403
    assert not config.connection_disabled("openclaw")


def test_disable_committed_during_final_snapshot_prevents_dispatch(tmp_path, monkeypatch):
    config, runs = setup(tmp_path)
    original = config.effective
    checks = []
    def effective(*args):
        snapshot = original(*args)
        checks.append(snapshot)
        if len(checks) == 2:
            # Deterministic interleaving: removal commits after the old
            # tombstone check but before the last snapshot returns.
            CustomerConfig(config.path).disable_connection("openclaw")
        return snapshot
    monkeypatch.setattr(config, "effective", effective)
    seen = []
    def dispatch(cfg, *_):
        seen.append(cfg)
        return dict(model=cfg["model"], response="Complete", id="race-event",
                    decision="allowed", done=True)
    result = runs.run(agent_id="existingOpenClaw", task_id="final-check-race",
                      resource_id="report", purpose="generate_text", prompt="Bounded",
                      dispatch=dispatch)
    assert result["state"] == "failed"
    assert not seen


@pytest.mark.parametrize("mutation", ["disable", "deny-purpose"])
def test_policy_writer_in_another_process_waits_for_actual_dispatch(tmp_path, mutation):
    import subprocess
    import sys
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    config, runs = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    witness = tmp_path / "writer-blocked"
    def dispatch(cfg, *_):
        entered.set()
        assert release.wait(10)
        return dict(model=cfg["model"], response="Complete", id="in-flight",
                    decision="allowed", done=True)
    args = dict(agent_id="existingOpenClaw", resource_id="report",
                purpose="generate_text", prompt="Bounded", dispatch=dispatch)
    writer = r"""
import pathlib,sqlite3,sys
from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig
path,witness,mutation=sys.argv[1:]
db=sqlite3.connect(path,timeout=0)
try:
    db.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as exc:
    assert 'locked' in str(exc)
    pathlib.Path(witness).write_text('configuration write reservation observed')
else:
    raise AssertionError('Dispatch did not hold the configuration reservation')
finally:
    db.close()
c=CustomerConfig(path)
if mutation=='disable':
    c.disable_connection('openclaw')
else:
    body=c.latest('agent','existingOpenClaw')['body']
    c.save('agent','existingOpenClaw',dict(body,allowed_tools=[]))
"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runs.run, task_id="in-flight", **args)
        assert entered.wait(5)
        process = subprocess.Popen([sys.executable, "-c", writer, str(config.path), str(witness), mutation],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not witness.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(.01)
            assert witness.exists()
            assert process.poll() is None
        finally:
            release.set()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        completed = future.result(timeout=5)
    assert completed["state"] == "completed"
    assert completed["configuration"]["agent_version"] == 1
    # A new instance represents a restarted reader of the updated policy.
    later = LocalInference(runs.path, CustomerConfig(config.path)).run(task_id="after-change", **args)
    assert later["state"] == "denied"


def test_dispatch_guard_is_released_when_owner_process_dies(tmp_path):
    import subprocess
    import sys
    config, _ = setup(tmp_path)
    code = r"""
import sys,time
from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig
c=CustomerConfig(sys.argv[1])
cfg=c.effective('existingOpenClaw','report')
with c.dispatch_guard('existingOpenClaw','report',cfg):
    print('guard-held',flush=True)
    time.sleep(30)
"""
    process = subprocess.Popen([sys.executable,"-c",code,str(config.path)],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip() == "guard-held"
    finally:
        process.kill()
        process.communicate(timeout=5)
    # OS/SQLite releases the abandoned lock. No persistent lease to clear.
    fresh = CustomerConfig(config.path)
    assert fresh.disable_connection("openclaw")


def test_busy_policy_change_is_not_presented_as_confirmed_removal(tmp_path, monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store, customer_routes
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "profile.db")
    config, _ = setup(tmp_path)
    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(customer_routes.CustomerConfig, "disable_connection", busy)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    response = client.post("/configuration/connections/openclaw/disable",
                           data={"operator_token": decision_routes.TOKEN})
    assert response.status_code == 409
    assert "not confirmed" in response.json()["detail"]
    assert config.connection_disabled("openclaw") is None
    page = client.get("/configuration")
    assert "An already-started request finishes first" in page.text


@pytest.mark.parametrize("case", ["valid", "oversized", "deep", "compressed", "unadvertised"])
def test_runtime_http_envelope_is_bounded_before_completion(tmp_path,case):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    config,runs=setup(tmp_path)
    result=dict(model="qwen2.5:7b-instruct",response="A bounded result",event_id="http-event",decision="allowed",done=True,done_reason="stop")
    if case in {"oversized","compressed","unadvertised"}:result["unused_metadata"]="x"*(256*1024)
    elif case=="deep":
        nested="leaf"
        for _ in range(140):nested=[nested]
        result["unused_metadata"]=nested
    raw=json.dumps(result).encode()
    if case=="compressed":
        import gzip
        raw=gzip.compress(raw)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200);self.send_header("Content-Type","application/json")
            if case!="unadvertised":self.send_header("Content-Length",str(len(raw)))
            if case=="compressed":self.send_header("Content-Encoding","gzip")
            self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        body=config.latest("connection","openclaw")["body"]
        config.save("connection","openclaw",dict(body,endpoint="http://127.0.0.1:"+str(server.server_port)))
        observed=runs.run(agent_id="existingOpenClaw",task_id="http-"+case,resource_id="report",purpose="generate_text",prompt="Specimen")
        assert observed["state"]==("completed" if case=="valid" else "failed")
        if case!="valid":assert observed["result"] is None
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize("event_id", ["", "   ", "bad\nid", "x"*161])
def test_runtime_event_identity_must_be_a_bounded_identifier(tmp_path,event_id):
    config,runs=setup(tmp_path)
    result=runs.run(agent_id="existingOpenClaw",task_id="bad-runtime-event",resource_id="report",purpose="generate_text",prompt="Bounded",dispatch=lambda cfg,*_:dict(model=cfg["model"],response="Complete",id=event_id,decision="allowed",done=True))
    assert result["state"]=="failed" and result["result"] is None
    assert LocalInference(tmp_path/"runs.db",config).recent()[0]["state"]=="failed"


def context_fixture(tmp_path):
    import hashlib
    from verity_core.organization_context import ContextStore
    context=ContextStore(tmp_path/"context.db")
    data=dict(title="SYNTHETIC vendor",entries=[dict(id="vendor",kind="vendor",status="assumption",
        subject="Fixture vendor",value="Previously recorded account ending1234",source_ref="Synthetic operator statement",
        source_sha256=hashlib.sha256(b"Synthetic operator statement").hexdigest(),checked_at="2026-10-19T19:00:00Z",
        review_before="2026-10-21T19:00:00Z",qualifications=[])])
    proposal=context.propose("fixture-org",data)
    context.approve("fixture-org",proposal["version"],proposal["sha256"],0,None,actor="fixture",reason="Synthetic local approval")
    return context,proposal


def test_context_request_binds_actual_prompt_and_stable_export(tmp_path):
    import copy,hashlib
    from verity_core.trust import context_analysis
    config,runs=setup(tmp_path);context,proposal=context_fixture(tmp_path);seen=[]
    def dispatch(cfg,prompt,purpose):
        seen.append(prompt)
        return dict(model=cfg["model"],response="SYNTHETIC discrepancy: request differs from the recorded account.",event_id="context-event",decision="allow",done=True,done_reason="stop")
    run=runs.run(agent_id="existingOpenClaw",task_id="context-bound",resource_id="report",purpose="generate_text",
        prompt="Vendor asks to use account5678",context_profile_id="fixture-org",context_store=context,dispatch=dispatch)
    assert run["state"]=="completed" and "account ending1234" in seen[0] and "account5678" in seen[0]
    assert run["prompt_sha256"]==hashlib.sha256(seen[0].encode()).hexdigest()
    assert run["context_analysis"]["capture"]["snapshot"]["generation"]==1
    assert run["context_analysis"]["capture"]["snapshot"]["unresolved"]  # Assumption is visible.
    bundle=runs.context_export(run["id"]);anchor=copy.deepcopy(bundle["checkpoint"])
    assert context_analysis.verify(bundle,anchor)
    assert LocalInference(runs.path,config).context_export(run["id"])==bundle
    changed=copy.deepcopy(bundle);changed["record"]["entries"][0]["payload"]["input"]["specimen"]="Substituted request"
    assert not context_analysis.verify(changed,anchor)


def test_context_withdrawal_during_inference_preserves_old_history_but_denies_next(tmp_path):
    from verity_core.organization_context import Conflict
    from verity_core.trust import context_analysis
    config,runs=setup(tmp_path);context,proposal=context_fixture(tmp_path);calls=[]
    def dispatch(cfg,prompt,purpose):
        calls.append(prompt)
        context.withdraw("fixture-org",1,proposal["sha256"],actor="fixture",reason="Withdraw after reservation")
        return dict(model=cfg["model"],response="Historical-context response",event_id="after-withdrawal",decision="allow",done=True)
    args=dict(agent_id="existingOpenClaw",resource_id="report",purpose="generate_text",prompt="Check fixture request",context_profile_id="fixture-org",context_store=context,dispatch=dispatch)
    run=runs.run(task_id="before-withdraw",**args)
    assert run["state"]=="completed"
    bundle=runs.context_export(run["id"]);assert context_analysis.verify(bundle,bundle["checkpoint"])
    with pytest.raises(Conflict):runs.run(task_id="after-withdraw",**args)
    assert len(calls)==1 and len(runs.recent())==1


def test_context_required_without_store_or_with_oversized_prompt_never_dispatches(tmp_path):
    config,runs=setup(tmp_path);context,_=context_fixture(tmp_path);calls=[]
    args=dict(agent_id="existingOpenClaw",resource_id="report",purpose="generate_text",prompt="Fixture",context_profile_id="fixture-org",dispatch=lambda *_:calls.append(True))
    with pytest.raises(ValueError):runs.run(task_id="no-store",**args)
    with pytest.raises(ValueError):runs.run(task_id="oversize",context_store=context,**dict(args,prompt="x"*3900))
    assert not calls and not runs.recent()


def test_context_capture_lock_spans_durable_run_reservation(tmp_path,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    config,runs=setup(tmp_path);context,proposal=context_fixture(tmp_path)
    start=threading.Event();done=threading.Event();reserved=[];original=config.effective;first=[True]
    def withdraw():
        assert start.wait(2)
        context.withdraw("fixture-org",1,proposal["sha256"],actor="fixture",reason="Concurrent withdrawal")
        with sqlite3.connect(runs.path) as con:reserved.append(con.execute("SELECT task_id FROM runs").fetchall())
        done.set()
    def effective(*args):
        if first[0]:
            first[0]=False;start.set();assert not done.wait(.05)
        return original(*args)
    monkeypatch.setattr(config,"effective",effective)
    def dispatch(cfg,*_):
        assert done.wait(3)  # Context lock released before inference, not held across model call.
        return dict(model=cfg["model"],response="Synthetic response",event_id="serialized-context",decision="allow",done=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(withdraw)
        run=runs.run(agent_id="existingOpenClaw",task_id="reserved-first",resource_id="report",purpose="generate_text",prompt="Fixture",
            context_profile_id="fixture-org",context_store=context,dispatch=dispatch)
        future.result(timeout=3)
    assert reserved==[[("reserved-first",)]] and run["state"]=="completed"


def test_rehashed_substituted_context_bundle_fails_retained_outer_anchor(tmp_path):
    import copy
    from verity_core.trust import context_analysis,record as trust
    config,runs=setup(tmp_path);context,_=context_fixture(tmp_path)
    run=runs.run(agent_id="existingOpenClaw",task_id="anchored-original",resource_id="report",purpose="generate_text",prompt="Fixture request",
        context_profile_id="fixture-org",context_store=context,dispatch=lambda cfg,*_:dict(model=cfg["model"],response="Synthetic response",event_id="anchor-original",decision="allow",done=True))
    original=runs.context_export(run["id"]);retained=copy.deepcopy(original["checkpoint"])
    # A caller may create a fully self-consistent local ledger. It still cannot
    # substitute that history for an independently retained original run anchor.
    other_dir=tmp_path/"other";other_dir.mkdir();other,proposal=context_fixture(other_dir)
    other.withdraw("fixture-org",1,proposal["sha256"],actor="fixture",reason="New history")
    p=other.propose("fixture-org",proposal["body"])
    other.approve("fixture-org",p["version"],p["sha256"],2,None,actor="fixture",reason="New local approval")
    changed=copy.deepcopy(run);changed["context_analysis"]["capture"]=other.capture_active("fixture-org")
    prompt=context_analysis.compose_prompt(changed["context_analysis"]["capture"],changed["context_analysis"]["specimen"])
    changed["prompt_sha256"]=trust.hashlib.sha256(prompt.encode()).hexdigest()
    changed["context_analysis"]["runtime_prompt_sha256"]=trust.hashlib.sha256((changed["configuration"]["instructions"]+"\n\n"+prompt).encode()).hexdigest()
    forged=context_analysis.export(changed)
    assert context_analysis.verify(forged,forged["checkpoint"])  # Self-consistency, not authenticated origin.
    assert not context_analysis.verify(forged,retained)
    assert runs.context_export(run["id"])==original


@pytest.mark.parametrize("state", [None, "unavailable", "unknown", "stale", "reachable"])
def test_strict_connection_health_is_persisted_and_gates_dispatch(tmp_path, state):
    import time
    config, _ = setup(tmp_path)
    body = config.latest("agent", "existingOpenClaw")["body"]
    config.save("agent", "existingOpenClaw", dict(body, require_fresh_connection_observation=True))
    if state:
        config.observe("connection", "openclaw", "reachable" if state == "stale" else state, "Synthetic health observation")
        if state == "stale":
            with config._db() as db:
                db.execute("UPDATE observations SET at=?", (time.time() - 301,))
    reopened = CustomerConfig(config.path)
    runs = LocalInference(tmp_path / "runs.db", reopened)
    assert reopened.effective("existingOpenClaw", "report")["require_fresh_connection_observation"] is True
    seen = []
    def dispatch(cfg, *_):
        seen.append(1)
        return dict(model=cfg["model"], response="Bounded result", event_id="health-1", decision="allow", done=True, done_reason="stop")
    result = runs.run(agent_id="existingOpenClaw", task_id="strict-health", resource_id="report",
                      purpose="generate_text", prompt="Synthetic specimen", dispatch=dispatch)
    assert result["state"] == ("completed" if state == "reachable" else "denied")
    assert len(seen) == (1 if state == "reachable" else 0)


def test_strict_health_removed_at_final_serialized_guard_never_dispatches(tmp_path, monkeypatch):
    from contextlib import contextmanager
    config, runs = setup(tmp_path)
    body = config.latest("agent", "existingOpenClaw")["body"]
    config.save("agent", "existingOpenClaw", dict(body, require_fresh_connection_observation=True))
    config.observe("connection", "openclaw", "reachable", "Synthetic health observation")
    original = config.dispatch_guard
    @contextmanager
    def remove_then_guard(*args):
        with config._db() as db:
            db.execute("DELETE FROM observations")
        with original(*args):
            yield
    monkeypatch.setattr(config, "dispatch_guard", remove_then_guard)
    seen = []
    result = runs.run(agent_id="existingOpenClaw", task_id="health-race", resource_id="report",
                      purpose="generate_text", prompt="Synthetic specimen", dispatch=lambda *_: seen.append(1))
    assert result["state"] == "failed" and not seen
    assert "ValueError" in result["reason"]


@pytest.mark.parametrize("value", [1, 0, "true", None, []])
def test_health_requirement_rejects_non_boolean_configuration(tmp_path, value):
    config, _ = setup(tmp_path)
    body = config.latest("agent", "existingOpenClaw")["body"]
    with pytest.raises(ValueError, match="boolean"):
        config.save("agent", "existingOpenClaw", dict(body, require_fresh_connection_observation=value))
