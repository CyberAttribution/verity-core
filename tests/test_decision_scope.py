"""A rehashed approval cannot cross records or omit an actual execution."""
import copy
import pytest
from verity_core.trust import decision, record as trust
from swarmtracker_node_ui.decision_runs import RunStore, synthetic_manifest

def rechain(record,entries):
    target=copy.deepcopy(record);target["entries"]=[]
    for e in entries:
        trust.append_entry(target,e["entry_type"],actor=e["actor"],payload=e["payload"])
    return target

def test_approval_cannot_be_transplanted_to_identical_other_run(tmp_path):
    db=RunStore(tmp_path/"runs.db")
    a=db.create(synthetic_manifest());b=db.create(synthetic_manifest())
    a=db.authorize(a["id"],"approved","Reviewed","operator",a["head_hash"])
    entries=b["record"]["entries"]+[a["record"]["entries"][-1]]
    forged=rechain(b["record"],entries)
    assert trust.chain_valid(forged)
    with pytest.raises(ValueError,match="not bound"):decision.inspect(forged)

def test_new_scoped_execution_has_distinct_events_and_cannot_drop_execution(tmp_path):
    db=RunStore(tmp_path/"runs.db");v=db.create(synthetic_manifest())
    v=db.authorize(v["id"],"approved","Reviewed","operator",v["head_hash"])
    v,allowed=db.execute(v["id"],"operator",v["head_hash"])
    assert allowed
    events=[e["payload"]["event"] for e in v["record"]["entries"]]
    assert events==["run_created","authorization_decided","action_attempted","action_executed","action_result"]
    without=[e for e in v["record"]["entries"] if e["payload"]["event"]!="action_executed"]
    # Rebuild attempt/result references because a hash-valid forged chain must
    # still be rejected semantically rather than just for its chain hashes.
    forged=rechain(v["record"],without)
    with pytest.raises(ValueError):decision.inspect(forged)

def test_legacy_unscoped_export_remains_verifiable():
    manifest=synthetic_manifest()
    record=trust.new_record({"kind":"governed_decision","identifier":manifest["title"]})
    trust.append_entry(record,"note",payload={"event":"run_created","manifest":manifest})
    assert decision.verify(decision.export(record))["verified"]
