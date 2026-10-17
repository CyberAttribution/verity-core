import pytest
from apps.node.swarmtracker_node_ui.opa_decision import DecisionLedger,decide,execute_if_allowed


def test_isolated_policy_boundary(tmp_path):
    policy=b'package verity\n default allow := false'
    ledger=DecisionLedger(tmp_path/"decisions.db")
    doc={"subject":"operator","action":"read_only_review"}
    calls=[]
    allowed=lambda d: {"result":True}
    result,decision=execute_if_allowed(doc,policy,lambda:calls.append("ran"),allowed,keel_approved=True,ledger=ledger,policy_version="v1")
    assert calls == ["ran"] and decision["allow"] and len(decision["policy_sha256"]) == 64
    for evaluator in (lambda d:{"result":False},lambda d:{},lambda d:(_ for _ in ()).throw(ConnectionError())):
        with pytest.raises(PermissionError): execute_if_allowed(doc,policy,lambda:calls.append("bad"),evaluator,keel_approved=True,ledger=ledger,policy_version="v1")
    with pytest.raises(PermissionError): execute_if_allowed({},policy,lambda:calls.append("bad"),allowed,keel_approved=True,ledger=ledger,policy_version="v1")
    with pytest.raises(PermissionError): execute_if_allowed(doc,policy,lambda:calls.append("bad"),allowed,keel_approved=False,ledger=ledger,policy_version="v1")
    with pytest.raises(PermissionError,match="ledger required"):
        execute_if_allowed(doc,policy,lambda:calls.append("bad"),allowed,keel_approved=True)
    assert calls == ["ran"]


def test_policy_decisions_are_persisted_before_executor_boundary(tmp_path):
    policy=b'package verity\n default allow := false'
    ledger=DecisionLedger(tmp_path/"decisions.db")
    doc={"subject":"operator","action":"read_only_review"}
    actions=[]
    evaluators=[lambda d:{"result":True},lambda d:{"result":False},
                lambda d:(_ for _ in ()).throw(ConnectionError("unavailable"))]
    result,decision=execute_if_allowed(doc,policy,lambda:actions.append("allowed"),
                                       evaluators[0],keel_approved=True,ledger=ledger,policy_version="synthetic-v1")
    assert decision["record_id"] == ledger.records()[0]["id"]
    for document,evaluator in ((doc,evaluators[1]),({},evaluators[0]),(doc,evaluators[2])):
        with pytest.raises(PermissionError):
            execute_if_allowed(document,policy,lambda:actions.append("denied"),evaluator,
                               keel_approved=True,ledger=ledger,policy_version="synthetic-v1")
    records=DecisionLedger(tmp_path/"decisions.db").records()
    assert len(records)==4 and actions==["allowed"]
    assert [r["reason"] for r in records]==["opa-decision","opa-decision","missing-input","evaluator-unavailable"]
    assert [r["allow"] for r in records]==[1,0,0,0]
    assert all(r["policy_version"]=="synthetic-v1" and r["policy_sha256"]==records[0]["policy_sha256"] for r in records)
    class BrokenLedger:
        def record(self,*args): raise OSError("disk unavailable")
    with pytest.raises(OSError):
        execute_if_allowed(doc,policy,lambda:actions.append("unrecorded"),evaluators[0],
                           keel_approved=True,ledger=BrokenLedger(),policy_version="synthetic-v1")
    assert actions==["allowed"]
