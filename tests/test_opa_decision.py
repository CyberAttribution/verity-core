import pytest
from apps.node.swarmtracker_node_ui.opa_decision import decide,execute_if_allowed


def test_isolated_policy_boundary():
    policy=b'package verity\n default allow := false'
    doc={"subject":"operator","action":"read_only_review"}
    calls=[]
    allowed=lambda d: {"result":True}
    result,decision=execute_if_allowed(doc,policy,lambda:calls.append("ran"),allowed,keel_approved=True)
    assert calls == ["ran"] and decision["allow"] and len(decision["policy_sha256"]) == 64
    for evaluator in (lambda d:{"result":False},lambda d:{},lambda d:(_ for _ in ()).throw(ConnectionError())):
        with pytest.raises(PermissionError): execute_if_allowed(doc,policy,lambda:calls.append("bad"),evaluator,keel_approved=True)
    with pytest.raises(PermissionError): execute_if_allowed({},policy,lambda:calls.append("bad"),allowed,keel_approved=True)
    with pytest.raises(PermissionError): execute_if_allowed(doc,policy,lambda:calls.append("bad"),allowed,keel_approved=False)
    assert calls == ["ran"]
