"""Disposable real-response demonstration of the fixed Verity report broker.

The input is a captured OpenClaw response; its runtime event ID is separately
operator-observed, not cryptographically authenticated by this script.
"""
import argparse
import hashlib
import json
from pathlib import Path

from apps.node.swarmtracker_node_ui import report_actions
from apps.node.swarmtracker_node_ui.customer_config import CustomerConfig


def run(response_path,root):
    root=Path(root).absolute()
    if root.exists():raise ValueError("Choose a fresh disposable demonstration directory")
    payload=json.loads(Path(response_path).read_text())
    observed=payload["response"]
    content=observed["response"]
    if observed.get("done") is not True or not content or observed.get("model")!="qwen2.5:7b-instruct" or not observed.get("id"):
        raise ValueError("Incomplete or unidentified actual runtime response")
    if hashlib.sha256(content.encode()).hexdigest()!=observed["response_sha256"]:
        raise ValueError("Runtime response digest mismatch")
    root.mkdir(parents=True)
    c=CustomerConfig(root/"configuration.db")
    for identifier,description in (("macM1","M1 OpenClaw host; operator-entered identity"),("macM2","M2 packaged pilot; operator-entered identity")):
        c.save("machine",identifier,dict(description=description,provenance="operator-entered",status="unknown"))
    c.save("resource","summary.md",dict(description="Disposable fixed output",provenance="operator-entered",status="unknown"))
    c.save("connection","m1OpenClaw",dict(provider=observed["provider"],endpoint="http://127.0.0.1:8090",model=observed["model"],revision="unknown",scope="local",credential_ref="none"))
    c.save("connection","macOllama",dict(provider="ollama_local",endpoint="http://127.0.0.1:11434",model="qwen2.5:3b-instruct",revision="unknown",scope="local",credential_ref="none"))
    c.save("link","m1HostsModel",dict(from_kind="machine",from_id="macM1",to_kind="connection",to_id="m1OpenClaw",permission="hosts",provenance="operator-entered",status="unknown"))
    agent=dict(connection_id="m1OpenClaw",instructions="Propose a harmless summary; do not write files",allowed_tools=["generate_text"],resources=["summary.md"],external_sharing="none",fallback_id="macOllama")
    c.save("agent","m1OpenClawAgent",agent)
    c.save("link","agentProposes",dict(from_kind="agent",from_id="m1OpenClawAgent",to_kind="resource",to_id="summary.md",permission="proposes",provenance="operator-entered",status="unknown"))
    cfg=c.effective("m1OpenClawAgent","summary.md")
    broker=report_actions.Store(root/"actions.db",root/"output",c)
    def manifest(task,text):
        sha=hashlib.sha256(text.encode()).hexdigest()
        return dict(schema=report_actions.SCHEMA,agent_id="m1OpenClawAgent",task_id=task,model=cfg["model"],provider=cfg["provider"],
                    source_name="operator-harmless-prompt",source_sha256=payload["prompt_sha256"],
                    runtime_event_id=observed["id"],proposal=text,
                    action=dict(kind="write_sanitized_summary",target="summary.md",sha256=sha),
                    policy=report_actions.POLICY,response_sha256=sha,effective_configuration=cfg)
    proposed=broker.create(manifest("realOpenClawProposal",content))
    assert not (root/"output/summary.md").exists()
    rejected=broker.create(manifest("rejectionVariant",content))
    rejected=broker.decide(rejected["id"],"rejected","Operator rejection acceptance","test-operator",rejected["head_hash"])
    denied=broker.execute(rejected["id"],"test-operator",rejected["head_hash"])
    assert denied["state"]=="denied" and not (root/"output/summary.md").exists()
    approved=broker.decide(proposed["id"],"approved","Scoped harmless output acceptance","test-operator",proposed["head_hash"])
    before=approved["head_hash"]
    expiring=broker.create(manifest("expiringVariant",content))
    expiring=broker.decide(expiring["id"],"approved","Synthetic time advance test","test-operator",expiring["head_hash"])
    original_clock=report_actions.time.time
    try:
        report_actions.time.time=lambda:expiring["approval_expires_at"]+1
        assert report_actions.Store(root/"actions.db",root/"output",c).get(expiring["id"])["state"]=="expired"
        try:broker.execute(expiring["id"],"test-operator",expiring["head_hash"])
        except report_actions.Conflict:expired_blocked=True
        else:raise AssertionError("Expired approval accepted")
    finally:report_actions.time.time=original_clock
    assert not (root/"output/summary.md").exists()
    try:broker.execute(proposed["id"],"test-operator","wrong-head")
    except report_actions.Conflict:changed_head_blocked=True
    else:raise AssertionError("Changed approval parameters accepted")
    restarted=report_actions.Store(root/"actions.db",root/"output",c)
    completed=restarted.execute(proposed["id"],"test-operator",before)
    assert completed["state"]=="written"
    agent["instructions"]="Updated future instruction only"
    c.save("agent","m1OpenClawAgent",agent)
    assert broker.get(proposed["id"])["manifest"]["effective_configuration"]==cfg
    try:broker.create(manifest("staleConfig",content))
    except ValueError:stale_config_blocked=True
    else:raise AssertionError("Stale configuration accepted")
    try:restarted.execute(proposed["id"],"test-operator",before)
    except report_actions.Conflict:replay_blocked=True
    else:raise AssertionError("Replay accepted")
    actual=(root/"output/summary.md").read_bytes()
    assert actual==content.encode() and hashlib.sha256(actual).hexdigest()==completed["manifest"]["action"]["sha256"]
    bundle=report_actions.export(completed["record"])
    (root/"history.json").write_text(json.dumps(bundle,indent=2,sort_keys=True))
    (root/"checkpoint.json").write_text(json.dumps(bundle["checkpoint"],indent=2,sort_keys=True))
    summary=dict(runtime_event_id=observed["id"],response_sha256=observed["response_sha256"],output_sha256=hashlib.sha256(actual).hexdigest(),
                 approval_expires_at=approved["approval_expires_at"],rejection=denied["state"],changed_head_blocked=changed_head_blocked,
                 expired_blocked=expired_blocked,stale_config_blocked=stale_config_blocked,replay_blocked=replay_blocked,history_verified=report_actions.verify(bundle,bundle["checkpoint"]),
                 effective_agent_version=cfg["agent_version"],current_agent_version=c.latest("agent","m1OpenClawAgent")["version"],
                 filesystem_isolation="not established; M1 OpenClaw runs as same admin user and direct writes are possible")
    (root/"acceptance.json").write_text(json.dumps(summary,indent=2,sort_keys=True))
    return summary


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--response",required=True);p.add_argument("--root",required=True)
    a=p.parse_args();print(json.dumps(run(a.response,a.root),indent=2))
