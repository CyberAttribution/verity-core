import pytest
import sqlite3
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


def test_policy_decision_linkage_does_not_replace_enforcement(tmp_path):
    ledger=DecisionLedger(tmp_path/"decisions.db")
    doc={"subject":"operator","action":"read_only_review","session_id":"s1",
         "task_id":"t1","request_id":"r1"}
    actions=[]
    _,allowed=execute_if_allowed(doc,b"policy",lambda:actions.append("allowed"),
                                 lambda _: {"result":True},keel_approved=True,ledger=ledger,policy_version="v2")
    with pytest.raises(PermissionError):
        execute_if_allowed(doc,b"policy",lambda:actions.append("denied"),
                           lambda _: {"result":False},keel_approved=True,ledger=ledger,policy_version="v2")
    assert actions==["allowed"]
    rows=ledger.records()
    assert len(rows)==2 and allowed["record_id"]==rows[0]["id"]
    assert all((r["session_id"],r["task_id"],r["request_id"])==("s1","t1","r1") for r in rows)
    assert [r["allow"] for r in rows]==[1,0]


def test_existing_decision_ledger_migrates_without_rewriting_history(tmp_path):
    path=tmp_path/"old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, at REAL NOT NULL, policy_version TEXT NOT NULL, policy_sha256 TEXT NOT NULL, allow INTEGER NOT NULL, reason TEXT NOT NULL, keel_approved INTEGER NOT NULL)")
        db.execute("INSERT INTO decisions VALUES ('old',1,'v1','hash',0,'denied',1)")
    ledger=DecisionLedger(path)
    assert ledger.records()[0]["id"]=="old"
    assert ledger.records()[0]["request_id"] is None
    with pytest.raises(ValueError,match="Invalid decision linkage"):
        ledger.record({"policy_sha256":"hash","allow":True,"reason":"allowed"},"v2",True,{"request_id":""})
