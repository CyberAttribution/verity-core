import copy
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import sqlite3

import pytest

from verity_core.organization_context import ContextStore, Conflict, verify_history, IDENTITY
from verity_core.trust import record as trust

NOW = datetime.datetime(2026,10,19,20,0,tzinfo=datetime.timezone.utc).timestamp()


def body():
    return dict(title="SYNTHETIC vendor profile", entries=[dict(id="vendor-contact",
        kind="vendor", status="fact", subject="Example fixture vendor",
        value="Use the previously confirmed contact",source_ref="Synthetic operator declaration",
        source_sha256=hashlib.sha256(b"Synthetic operator declaration").hexdigest(),
        checked_at="2026-10-19T19:00:00Z",review_before="2026-10-20T19:00:00Z",
        qualifications=[])])


def approve(store, proposal):
    return store.approve(proposal["profile_id"],proposal["version"],proposal["sha256"],
        proposal["parent_generation"],proposal["parent_digest"],
        actor="fixture-operator",reason="Explicit synthetic local review")


def test_model_proposal_never_activates_and_local_approval_is_bound(tmp_path):
    store=ContextStore(tmp_path/"context.db");data=body()
    p=store.propose("fixture-org",data,origin="model")
    data["entries"][0]["value"]="Changed after proposal"
    with pytest.raises(Conflict):store.active_snapshot("fixture-org")
    with pytest.raises(Conflict):
        store.approve("fixture-org",p["version"],"0"*64,0,None,actor="fixture",reason="Wrong digest")
    approve(store,p);snapshot=store.active_snapshot("fixture-org",now=NOW)
    assert snapshot["version"]==1 and snapshot["generation"]==1
    assert snapshot["body"]["entries"][0]["value"]!="Changed after proposal"
    assert snapshot["identity_provenance"]==IDENTITY and "unauthenticated" in IDENTITY
    assert not snapshot["unresolved"]
    snapshot["body"]["entries"][0]["value"]="Caller change"
    assert store.active_snapshot("fixture-org",now=NOW)["body"]["entries"][0]["value"]!="Caller change"
    before=store.export_history("fixture-org")
    with pytest.raises(Conflict):approve(store,p)
    assert store.export_history("fixture-org")==before


def test_withdrawal_empty_pointer_cycle_does_not_revalidate_stale_proposal(tmp_path):
    store=ContextStore(tmp_path/"context.db")
    old=store.propose("fixture-org",body());chosen=store.propose("fixture-org",body())
    approve(store,chosen)
    assert store.withdraw("fixture-org",1,chosen["sha256"],actor="fixture",reason="Withdraw before replacement")["generation"]==2
    with pytest.raises(Conflict):store.active_snapshot("fixture-org")
    with pytest.raises(Conflict):approve(store,old)
    # Even a caller substituting the current generation cannot rebind old parent metadata.
    with pytest.raises(Conflict):
        store.approve("fixture-org",old["version"],old["sha256"],2,None,actor="fixture",reason="Stale proposal")
    fresh=store.propose("fixture-org",body())
    assert fresh["parent_generation"]==2 and fresh["parent_digest"] is None
    approve(store,fresh)
    snap=ContextStore(store.path).active_snapshot("fixture-org",now=NOW)
    assert snap["version"]==fresh["version"] and snap["generation"]==3


def test_rejecting_stale_proposal_keeps_new_active_pointer_and_generation(tmp_path):
    store=ContextStore(tmp_path/"context.db")
    old=store.propose("fixture-org",body());chosen=store.propose("fixture-org",body());approve(store,chosen)
    before=store.active_snapshot("fixture-org",now=NOW)
    rejected=store.reject("fixture-org",old["version"],old["sha256"],actor="fixture",reason="Reject obsolete proposal")
    assert rejected["decision"]=="rejected"
    assert store.active_snapshot("fixture-org",now=NOW)==before
    with pytest.raises(Conflict):approve(store,old)
    with pytest.raises(Conflict):store.reject("fixture-org",old["version"],old["sha256"],actor="fixture",reason="Replay")


def test_two_competing_approvals_have_only_one_winner(tmp_path):
    store=ContextStore(tmp_path/"context.db")
    proposals=[store.propose("fixture-org",body()),store.propose("fixture-org",body())]
    def run(p):
        try:approve(store,p);return "approved"
        except Conflict:return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run,proposals))==["approved","conflict"]
    assert store.active_snapshot("fixture-org",now=NOW)["generation"]==1
    assert len(store.export_history("fixture-org")["record"]["entries"])==3


def test_failed_database_update_does_not_record_approval(tmp_path):
    store=ContextStore(tmp_path/"context.db");p=store.propose("fixture-org",body())
    before=store.export_history("fixture-org")
    with sqlite3.connect(store.path) as con:
        con.execute("CREATE TRIGGER fail_context BEFORE UPDATE ON context_profiles BEGIN SELECT RAISE(ABORT,'controlled failure'); END")
    with pytest.raises(sqlite3.IntegrityError):approve(store,p)
    assert store.export_history("fixture-org")==before
    with pytest.raises(Conflict):store.active_snapshot("fixture-org")
    with sqlite3.connect(store.path) as con:con.execute("DROP TRIGGER fail_context")
    approve(store,p)


def test_missing_stale_conflicting_and_assumed_entries_remain_visible(tmp_path):
    store=ContextStore(tmp_path/"context.db");data=body()
    data["entries"][0].update(status="assumption",qualifications=["conflicting"])
    p=store.propose("fixture-org",data);approve(store,p)
    at=datetime.datetime(2026,10,20,19,0,tzinfo=datetime.timezone.utc).timestamp()
    snap=store.active_snapshot("fixture-org",now=at,required_entries=["missing-vendor"])
    flags={entry["id"]:entry["qualifications"] for entry in snap["qualifications"]}
    assert flags["vendor-contact"]==["conflicting","stale","assumption"]
    assert flags["missing-vendor"]==["missing"] and snap["unresolved"]


def test_history_reconstructs_against_retained_anchor_and_rejects_tampering(tmp_path):
    store=ContextStore(tmp_path/"context.db");p=store.propose("fixture-org",body());approve(store,p)
    bundle=json.loads(json.dumps(store.export_history("fixture-org")));anchor=copy.deepcopy(bundle["checkpoint"])
    assert verify_history(bundle,anchor)
    changed=copy.deepcopy(bundle);changed["record"]["entries"][0]["payload"]["proposal"]["body"]["title"]="Changed"
    assert not verify_history(changed,anchor)
    changed=copy.deepcopy(bundle);changed["record"]["subject"]["identifier"]="another-org"
    assert not verify_history(changed,anchor)
    store.withdraw("fixture-org",1,p["sha256"],actor="fixture",reason="Withdrawal")
    assert not verify_history(store.export_history("fixture-org"),anchor)
    assert verify_history(bundle,anchor)


def test_hash_valid_approval_without_proposal_fails_semantic_replay():
    record=trust.new_record(dict(kind="organization_context",identifier="fixture-org"))
    trust.append_entry(record,"approval_granted",actor="fixture",payload=dict(event="context_approved",
        profile_id="fixture-org",version=1,sha256="0"*64,expected_generation=0,
        expected_parent_digest=None,reason="Forged direct activation",identity_provenance=IDENTITY))
    anchor=dict(record_id=record["record_id"],entry_count=1,head_hash=record["entries"][-1]["entry_hash"])
    assert trust.chain_valid(record)
    assert not verify_history(dict(schema="organization_context_history/v0.1",record=record,checkpoint=anchor),anchor)


@pytest.mark.parametrize("change",["approved_field","bad_source_hash","duplicate_entry","wrong_kind","wrong_status","bad_review_date","unknown_qualification","large_body"])
def test_malformed_or_self_approved_proposals_do_not_create_a_profile(tmp_path,change):
    store=ContextStore(tmp_path/"context.db");data=body()
    if change=="approved_field":data["approval_status"]="approved"
    elif change=="bad_source_hash":data["entries"][0]["source_sha256"]="not-a-hash"
    elif change=="duplicate_entry":data["entries"].append(copy.deepcopy(data["entries"][0]))
    elif change=="wrong_kind":data["entries"][0]["kind"]="execute_payment"
    elif change=="wrong_status":data["entries"][0]["status"]="authenticated"
    elif change=="bad_review_date":data["entries"][0]["review_before"]=data["entries"][0]["checked_at"]
    elif change=="unknown_qualification":data["entries"][0]["qualifications"]=["approved_by_model"]
    else:data["entries"]=[dict(data["entries"][0],id="entry-"+str(i),source_ref="x"*1000,value="x"*500) for i in range(20)]
    with pytest.raises(ValueError):store.propose("fixture-org",data)
    with pytest.raises(Conflict):store.export_history("fixture-org")


def test_wrong_profile_withdrawal_and_stale_expected_generation_are_refused(tmp_path):
    store=ContextStore(tmp_path/"context.db");p=store.propose("fixture-org",body());approve(store,p)
    with pytest.raises(Conflict):store.withdraw("other-org",1,p["sha256"],actor="fixture",reason="Wrong profile")
    with pytest.raises(Conflict):store.withdraw("fixture-org",0,p["sha256"],actor="fixture",reason="Wrong generation")
    with pytest.raises(Conflict):store.withdraw("fixture-org",True,p["sha256"],actor="fixture",reason="Boolean generation")
    assert store.active_snapshot("fixture-org",now=NOW)["generation"]==1


def test_store_refuses_public_and_symlink_database(tmp_path):
    public=tmp_path/"public.db";public.touch();public.chmod(0o644)
    with pytest.raises(ValueError):ContextStore(public)
    public.chmod(0o600);link=tmp_path/"link.db";link.symlink_to(public)
    with pytest.raises(ValueError):ContextStore(link)


def test_overlong_history_is_rejected_before_chain_hash_work(monkeypatch):
    from verity_core import organization_context as context
    def unexpected_hash_work(record):
        raise AssertionError("Overlong history reached chain verification")
    monkeypatch.setattr(context.trust, "chain_valid", unexpected_hash_work)
    with pytest.raises(ValueError, match="bounded context history"):
        context.inspect_record({"entries": [{}] * (context.MAX_EVENTS + 1)})
