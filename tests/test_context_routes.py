import copy
import pytest
from fastapi.testclient import TestClient
from verity_core.trust import context_analysis


def environment(tmp_path, monkeypatch):
    from swarmtracker_node_ui import app as ui, store, decision_routes, context_routes, customer_routes, local_inference
    monkeypatch.setattr(store, "DB_PATH", tmp_path/"profile.db")
    config = customer_routes.database()
    config.save("resource", "report", dict(description="Synthetic report", provenance="operator-entered", status="unknown"))
    config.save("connection", "openclaw", dict(provider="openclaw",endpoint="http://127.0.0.1:8090",model="fixture-model",revision="fixture-rev",scope="local",credential_ref="none"))
    config.save("agent", "fixture-agent",dict(connection_id="openclaw",instructions="Analyze, do not execute actions.",allowed_tools=["generate_text"],resources=["report"],external_sharing="none",fallback_id=""))
    calls=[]
    def dispatch(cfg,prompt,purpose):
        calls.append(prompt)
        return dict(model=cfg["model"],response="SYNTHETIC discrepancy: the supplied vendor account differs.",event_id="fixture-context-event",decision="allow",done=True,done_reason="stop")
    monkeypatch.setattr(local_inference.LocalInference,"_dispatch",staticmethod(dispatch))
    client=TestClient(ui.app,base_url="http://127.0.0.1")
    proposal=dict(operator_token=decision_routes.TOKEN,profile_id="fixture-org",title="SYNTHETIC vendor",entry_id="vendor",kind="vendor",status="assumption",subject="Fixture vendor",value="Known account ending1234",source_ref="Synthetic operator statement",checked_at="2026-10-18T00:00:00Z",review_before="2026-10-19T00:00:00Z")
    analysis=dict(operator_token=decision_routes.TOKEN,profile_id="fixture-org",agent_id="fixture-agent",resource_id="report",task_id="context-request-1",session_id="",prompt="Vendor asks to use account5678")
    return ui,client,context_routes,proposal,analysis,calls


def decision(routes,proposal,action="approve"):
    p=routes.database().profiles()[0]["state"]["versions"][1]
    return dict(operator_token=proposal["operator_token"],action=action,version="1",sha256=p["sha256"],generation="0",parent_digest="",actor="synthetic-reviewer",reason="Explicit synthetic local review")


def test_context_ui_propose_review_analysis_withdraw_and_historical_export(tmp_path,monkeypatch):
    ui,client,routes,proposal,analysis,calls=environment(tmp_path,monkeypatch)
    assert client.get("/contexts").status_code==200
    assert client.post("/contexts/propose",data=proposal,follow_redirects=False).status_code==303
    assert "no active approval" in client.get("/contexts").text
    assert client.post("/contexts/analyze",data=analysis).status_code==409 and not calls
    approve=decision(routes,proposal)
    assert client.post("/contexts/fixture-org/decision",data=approve,follow_redirects=False).status_code==303
    assert client.post("/contexts/analyze",data=analysis,follow_redirects=False).status_code==303
    assert len(calls)==1 and "Known account ending1234" in calls[0] and "account5678" in calls[0]
    page=client.get("/contexts").text
    assert "now current" in page and "stale" in page and "assumption" in page and "independently unchecked" in page
    run=routes.runs().recent()[0]
    url="/contexts/runs/"+run["id"]
    bundle=client.get(url+"/export").json();anchor=client.get(url+"/checkpoint").json()
    assert context_analysis.verify(bundle,anchor)
    assert client.get(url+"/export").json()==bundle
    withdraw=dict(operator_token=proposal["operator_token"],generation="1",sha256=approve["sha256"],actor="synthetic-reviewer",reason="Withdraw synthetic approval")
    assert client.post("/contexts/fixture-org/withdraw",data=withdraw,follow_redirects=False).status_code==303
    assert "now withdrawn" in client.get("/contexts").text
    assert client.post("/contexts/analyze",data=dict(analysis,task_id="after-withdrawal")).status_code==409
    assert len(calls)==1 and client.get(url+"/export").json()==bundle
    assert context_analysis.verify(bundle,anchor)
    changed=copy.deepcopy(bundle);changed["record"]["entries"][0]["payload"]["input"]["capture"]["snapshot"]["generation"]=2
    assert not context_analysis.verify(changed,anchor)


def test_context_routes_reject_cross_origin_remote_peer_and_forged_approval(tmp_path,monkeypatch):
    ui,client,routes,proposal,analysis,calls=environment(tmp_path,monkeypatch)
    assert client.post("/contexts/propose",data={k:v for k,v in proposal.items() if k!="operator_token"}).status_code==403
    assert client.post("/contexts/propose",data=proposal,headers={"Origin":"https://untrusted.example"}).status_code==403
    remote=TestClient(ui.app,base_url="http://127.0.0.1",client=("203.0.113.7",4312))
    assert remote.get("/contexts",headers={"X-Forwarded-For":"127.0.0.1"}).status_code==403
    assert remote.post("/contexts/propose",data=proposal).status_code==403
    assert client.post("/contexts/propose",data=proposal,follow_redirects=False).status_code==303
    form=decision(routes,proposal);form["sha256"]="0"*64
    assert client.post("/contexts/fixture-org/decision",data=form).status_code==409
    assert client.post("/contexts/analyze",data=dict(analysis,context_capture="forged")).status_code==422
    assert not calls and routes.database().profiles()[0]["state"]["active_version"] is None


def test_context_ui_duplicate_task_does_not_redispatch(tmp_path,monkeypatch):
    _,client,routes,proposal,analysis,calls=environment(tmp_path,monkeypatch)
    client.post("/contexts/propose",data=proposal)
    client.post("/contexts/fixture-org/decision",data=decision(routes,proposal))
    assert client.post("/contexts/analyze",data=analysis,follow_redirects=False).status_code==303
    assert client.post("/contexts/analyze",data=analysis).status_code==409
    assert len(calls)==1


def test_active_approval_keeps_withdrawal_control_after_many_new_proposals(tmp_path,monkeypatch):
    _,client,routes,proposal,_,_=environment(tmp_path,monkeypatch)
    client.post("/contexts/propose",data=proposal)
    client.post("/contexts/fixture-org/decision",data=decision(routes,proposal))
    db=routes.database();body=db.profiles()[0]["state"]["versions"][1]["body"]
    for _ in range(6):db.propose("fixture-org",body)
    page=client.get("/contexts").text
    assert 'action="/contexts/fixture-org/withdraw"' in page
    assert "Version 1" in page and "locally approved version 1" in page
