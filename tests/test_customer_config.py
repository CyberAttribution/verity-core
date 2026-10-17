import hashlib
import json
import pytest
from fastapi.testclient import TestClient

from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig
from apps.node.swarmtracker_node_ui import report_actions


def configured(tmp_path):
    c=CustomerConfig(tmp_path/"customer-config.db")
    c.save("machine","macM1",dict(description="M1 OpenClaw; operator reported",provenance="operator-entered",status="unknown"))
    c.save("resource","summary.md",dict(description="Disposable protected report target",provenance="operator-entered",status="unknown"))
    c.save("connection","m1OpenClaw",dict(provider="ollama_local",endpoint="http://127.0.0.1:8090",model="qwen2.5:7b-instruct",revision="unknown",scope="local",credential_ref="none"))
    c.save("connection","backup",dict(provider="local",endpoint="http://127.0.0.1:11434",model="qwen2.5:3b-instruct",revision="unknown",scope="local",credential_ref="none"))
    c.save("link","m1HostsModel",dict(from_kind="machine",from_id="macM1",to_kind="connection",to_id="m1OpenClaw",permission="hosts",provenance="operator-entered",status="unknown"))
    agent=dict(connection_id="m1OpenClaw",instructions="Propose a short report only",allowed_tools=["generate_text"],
               resources=["summary.md"],external_sharing="none",fallback_id="backup")
    c.save("agent","openclawM1",agent)
    c.save("link","agentProposes",dict(from_kind="agent",from_id="openclawM1",to_kind="resource",to_id="summary.md",permission="proposes",provenance="operator-entered",status="unknown"))
    return c,agent


def manifest(cfg,proposal="Harmless summary"):
    sha=hashlib.sha256(proposal.encode()).hexdigest()
    return dict(schema=report_actions.SCHEMA,agent_id="openclawM1",task_id="task01",model=cfg["model"],
                provider=cfg["provider"],source_name="synthetic.txt",source_sha256=sha,
                runtime_event_id="runtime01",proposal=proposal,
                action={"kind":"write_sanitized_summary","target":"summary.md","sha256":sha},
                policy=report_actions.POLICY,effective_configuration=cfg)


def test_versioned_configuration_is_bound_to_old_action(tmp_path):
    c,agent=configured(tmp_path)
    original=c.effective("openclawM1","summary.md")
    assert original["instructions"]=="Propose a short report only"
    assert original["resources"]==["summary.md"]
    assert (original["fallback_id"],original["fallback_model"])==("backup","qwen2.5:3b-instruct")
    store=report_actions.Store(tmp_path/"actions.db",tmp_path/"output",c)
    view=store.create(manifest(original))
    agent["instructions"]="Different instructions";agent["allowed_tools"]=["draft_text"]
    c.save("agent","openclawM1",agent)
    assert c.effective("openclawM1","summary.md")["agent_version"]==2
    assert store.get(view["id"])["manifest"]["effective_configuration"]==original
    assert store.get(view["id"])["manifest"]["effective_configuration"]["instructions"]=="Propose a short report only"
    approved=store.decide(view["id"],"approved","Prior configuration approval","operator",view["head_hash"])
    with pytest.raises(report_actions.Conflict,match="configuration changed"):
        store.execute(view["id"],"operator",approved["head_hash"])
    assert not (tmp_path/"output/summary.md").exists()
    with pytest.raises(ValueError,match="changed"):
        store.create(manifest(original,"New task using stale settings"))
    assert report_actions.verify(report_actions.export(store.get(view["id"])["record"]))


def test_config_rejects_credentials_and_unsupported_tool_claims(tmp_path):
    c,agent=configured(tmp_path)
    agent["allowed_tools"]=["file_write"]
    with pytest.raises(ValueError,match="read-only"):
        c.save("agent","openclawM1",agent)
    with pytest.raises(ValueError,match="credentials"):
        c.save("connection","bad",dict(provider="external",endpoint="https://user:pass@example.test",model="x",revision="",scope="external",credential_ref="none"))
    with pytest.raises(ValueError):
        c.save("connection","bad",dict(provider="external",endpoint="https://example.test",model="x",revision="",scope="external",credential_ref="Bearer secret"))
    with pytest.raises(ValueError,match="mismatch"):
        wrong=manifest(c.effective("openclawM1","summary.md"));wrong["model"]="other"
        report_actions.validate(wrong)
    c.observe("connection","m1OpenClaw","reachable","HTTP answered; identity unconfirmed")
    assert c.observations()[("connection","m1OpenClaw")]["state"]=="reachable"
    from apps.node.swarmtracker_node_ui import customer_config
    observed_at=c.observations()[("connection","m1OpenClaw")]["at"]
    old_clock=customer_config.time.time
    try:
        customer_config.time.time=lambda:observed_at+301
        assert c.observations()[("connection","m1OpenClaw")]["state"]=="stale"
    finally:customer_config.time.time=old_clock
    assert all("secret" not in json.dumps(x).lower() for x in c.all())


def test_native_routes_show_versions_and_bind_imported_proposal(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui,decision_routes,report_routes,store
    monkeypatch.setattr(store,"DB_PATH",tmp_path/"profile.db")
    c,agent=configured(tmp_path)
    # The configuration route and broker use the profile-derived database.
    monkeypatch.setattr(report_routes,"STORE",None)
    client=TestClient(ui.app,base_url="http://127.0.0.1")
    assert "m1OpenClaw v1" in client.get("/configuration").text
    form={"operator_token":decision_routes.TOKEN,"agent_id":"openclawM1","task_id":"uiDemo",
          "runtime_event_id":"observed-001","proposal":"Harmless summary",
          "source_name":"synthetic.txt","source_sha256":hashlib.sha256(b"source").hexdigest()}
    response=client.post("/report-actions/propose",data=form,follow_redirects=False)
    assert response.status_code==303
    page=client.get(response.headers["location"]).text
    assert "Effective agent configuration" in page and "openclawM1 v1" in page
    assert "Propose a short report only" in page and "qwen2.5:3b-instruct" in page
    assert "model connection" in page.lower() and "summary.md v1" in page
    assert "macM1 v1" in page
    agent["instructions"]="New instructions for future actions"
    c.save("agent","openclawM1",agent)
    assert "openclawM1 v1" in client.get(response.headers["location"]).text
    assert "New instructions for future actions" not in client.get(response.headers["location"]).text
    assert "openclawM1 v2" in client.get("/configuration").text
    assert client.post("/report-actions/propose",data=dict(form,proposal="Changed",task_id="uiNew"),follow_redirects=False).status_code==303
