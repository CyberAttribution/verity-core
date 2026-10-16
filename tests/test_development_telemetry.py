import copy
import math
from concurrent.futures import ThreadPoolExecutor

import pytest
from verity_core.trust import telemetry as t

MANIFEST = dict(run_id="run-1", agent_id="builder-1", name="Coding harness", runtime="bounded-harness", model="glm53-w4")


def ev(kind="heartbeat", data=None, eid="event-1"):
    return dict(event_id=eid, kind=kind, data=data or {})


def test_freshness_uses_local_receipt_and_disconnect_does_not_complete_request():
    r = t.create(MANIFEST, 100)
    t.append(r, ev("request_start", dict(activity_id="request-1", operation="completion", task="Review monitoring")), 101)
    assert t.inspect(r, now=102)["status"] == "Active"
    v = t.inspect(r, now=132)
    assert v["contact_lost_during_activity"]
    assert v["current_task"] == "Review monitoring"
    t.append(r, ev("disconnect", eid="event-2"), 133)
    assert t.inspect(r, now=133)["status"] == "Recently disconnected"
    assert len(t.inspect(r, now=133)["pending"]) == 1


def test_request_completion_and_retry_are_distinct_from_verified_outcome():
    r = t.create(MANIFEST, 1)
    start = ev("request_start", dict(activity_id="req", operation="completion"))
    assert t.append(r, start, 2)
    assert not t.append(r, start, 3)
    t.append(r, ev("request_finish", dict(activity_id="req", status="success", duration_ms=250), "event-2"), 4)
    v = t.inspect(r, now=4)
    assert not v["pending"]
    assert v["current_task"] == "Unknown"
    assert v["activities"][0]["status"] == "success"
    assert all(e["entry_type"] == "note" for e in r["entries"])


@pytest.mark.parametrize("bad", [
    ev("heartbeat", {"prompt":"private"}),
    ev("request_start", {"activity_id":"a", "operation":"shell", "arguments":"secret"}),
    ev("request_finish", {"activity_id":"a", "status":"success", "duration_ms":math.nan}),
    ev("request_finish", {"activity_id":"a", "status":"approved", "duration_ms":1}),
    ev("heartbeat", eid="../../escape"),
    ev("gpu_sample", {"gpus":[]}),
])
def test_rejects_unknown_fields_and_invalid_measurements(bad):
    with pytest.raises(ValueError): t.event(bad)


def test_failed_sequence_preserves_record_exactly():
    r = t.create(MANIFEST, 1); original = copy.deepcopy(r)
    with pytest.raises(ValueError):
        t.append(r, ev("request_finish", dict(activity_id="missing", status="failure", duration_ms=2)), 2)
    assert r == original


def test_retained_checkpoint_detects_rewrite_and_truncation():
    r = t.create(MANIFEST, 1); t.append(r, ev(), 2)
    anchor = t.checkpoint(r)
    assert t.verify(copy.deepcopy(r), anchor)
    truncated = copy.deepcopy(r); truncated["entries"].pop()
    assert not t.verify(truncated, anchor)
    changed = copy.deepcopy(r); changed["entries"][1]["payload"]["received_at"] = 12
    with pytest.raises(ValueError): t.checkpoint(changed)


def test_manifest_subject_bound_by_hashed_first_entry():
    r = t.create(MANIFEST, 1)
    r["subject"]["identifier"] = "different"
    with pytest.raises(ValueError): t.inspect(r, now=2)


def test_stale_gpu_sample_does_not_become_fresh_with_heartbeat():
    r = t.create(MANIFEST, 1)
    t.append(r, ev("gpu_sample", {"gpus":[dict(index=0,name="H100", memory_used_mib=200,memory_total_mib=80000,utilization_percent=50)]}), 2)
    t.append(r, ev(eid="heartbeat-new"), 100)
    view = t.inspect(r, now=100)
    assert view["gpu_sample"]["received_at"] == 2
    assert view["gpu_age_seconds"] == 98


def test_repeated_activity_and_conflicting_event_ids_rejected():
    r = t.create(MANIFEST, 1)
    t.append(r, ev(), 2)
    with pytest.raises(ValueError): t.append(r, ev("disconnect"), 3)


def test_reconnection_preserves_history_of_contact_gap():
    r=t.create(MANIFEST,1)
    t.append(r,ev("request_start",dict(activity_id="req",operation="completion")),2)
    t.append(r,ev(eid="back-online"),50)
    view=t.inspect(r,now=50)
    assert view["status"]=="Active"
    assert not view["contact_lost_during_activity"]
    assert view["recorded_contact_gaps"]==1


def test_disconnect_after_delay_counts_one_interruption_and_clock_skew_is_unknown():
    r=t.create(MANIFEST,1)
    t.append(r,ev("request_start",dict(activity_id="req",operation="completion")),2)
    t.append(r,ev("disconnect",eid="disconnect"),50)
    assert t.inspect(r,now=50)["recorded_contact_gaps"]==1
    view=t.inspect(r,now=40)
    assert view["clock_anomaly"]
    assert view["status"]=="Status unknown"


def test_agent_grouping_preserves_old_unfinished_alert_and_all_links():
    from swarmtracker_node_ui.monitoring import participants
    old=t.create(MANIFEST,1)
    t.append(old,ev("request_start",dict(activity_id="req",operation="completion",task="Older unfinished review")),2)
    new=t.create(dict(MANIFEST,run_id="run-2"),100)
    t.append(new,ev(),101)
    recent=t.inspect(new,now=102);stale=t.inspect(old,now=102)
    grouped=participants([recent,stale])
    assert len(grouped)==1
    assert len(grouped[0]["recent_runs"])==2
    assert grouped[0]["run_id"]=="run-2"
    assert grouped[0]["unfinished_disconnected"]==1


def test_store_atomic_concurrent_idempotency_and_restart(tmp_path):
    from swarmtracker_node_ui.monitoring import MonitorStore
    path = tmp_path / "monitor.db"
    db = MonitorStore(path)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: db.ingest(MANIFEST, ev()), range(5)))
    assert all(v["entry_count"] == 2 for v in results)
    assert MonitorStore(path).recent()[0]["entry_count"] == 2
    changed = dict(MANIFEST, model="different")
    with pytest.raises(ValueError): db.ingest(changed, ev(eid="later"))
    assert len(db.get("run-1")["entries"]) == 2


def test_ingestion_auth_size_cross_site_and_html_escape(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui, monitoring as m
    monkeypatch.setattr(m, "STORE", m.MonitorStore(tmp_path / "api.db"))
    monkeypatch.setenv("VERITY_MONITOR_TOKEN", "t" * 40)
    client = TestClient(ui.app, base_url="http://127.0.0.1")
    body = dict(manifest=dict(MANIFEST, name="<script>bad</script>"), event=ev())
    assert client.post("/api/monitor/events", json=body).status_code == 403
    headers = {"authorization":"Bearer " + "t" * 40}
    assert client.post("/api/monitor/events", json=body, headers=dict(headers, origin="http://evil.invalid")).status_code == 403
    assert client.post("/api/monitor/events", json=body, headers=headers).status_code == 200
    assert client.post("/api/monitor/events", content="x" * 17000, headers=dict(headers, **{"content-type":"application/json"})).status_code == 413
    response = client.get("/monitor/runs/run-1")
    assert response.status_code == 200
    assert "<script>bad</script>" not in response.text
    assert "&lt;script&gt;bad&lt;/script&gt;" in response.text
    exported = client.get("/monitor/runs/run-1/export.json").json()
    anchor = client.get("/monitor/runs/run-1/checkpoint.json").json()
    assert t.verify(exported, anchor)
    assert "telemetry" in client.get("/agents").text.lower()
