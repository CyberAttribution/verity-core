import json,threading,time
from swarmtracker_node_ui import local_activity,monitoring

def test_actual_request_boundary_is_visible_without_external_collector_token(monkeypatch):
    entered=threading.Event();release=threading.Event();result=[]
    def call():
        entered.set();assert release.wait(3)
        return {"ok":True,"response":"private model response"}
    thread=threading.Thread(target=lambda:result.append(local_activity.generate(call)))
    thread.start();assert entered.wait(3)
    view=monitoring.database().recent()[0]
    assert view["status"]=="Active" and view["current_task"]=="Local evidence analysis"
    release.set();thread.join(3)
    assert result[0]["ok"] and result[0]["activity_run_id"]==view["run_id"]
    record=monitoring.database().get(view["run_id"])
    assert "private model response" not in json.dumps(record)
    after=monitoring.database().recent()[0]
    assert after["activities"][0]["status"]=="success"
    assert after["status"]=="Recently disconnected"

def test_activity_storage_failure_prevents_inference(monkeypatch):
    calls=[]
    monkeypatch.setattr(monitoring,"database",lambda:(_ for _ in ()).throw(OSError("test disk failure")))
    result=local_activity.generate(lambda:calls.append("called"))
    assert not result["ok"] and calls==[]

def test_completion_audit_failure_does_not_claim_report_success(monkeypatch):
    db=monitoring.database();original=db.ingest
    def ingest(manifest,event,**kwargs):
        if event["kind"]=="request_finish":raise OSError("test disk failure")
        return original(manifest,event,**kwargs)
    monkeypatch.setattr(db,"ingest",ingest)
    result=local_activity.generate(lambda:{"ok":True,"response":"unrecorded result"})
    assert not result["ok"] and "ran" in result["error"]
    view=monitoring.database().recent()[0]
    assert len(view["pending"])==1 and view["contact_lost_during_activity"]

def test_thread_start_failure_records_failure_and_does_not_invoke_model(monkeypatch):
    monkeypatch.setattr(threading.Thread,"start",lambda self:(_ for _ in ()).throw(RuntimeError("thread exhaustion")))
    called=[]
    result=local_activity.generate(lambda:called.append(True))
    assert not result["ok"] and called==[]
    view=monitoring.database().recent()[0]
    assert not view["pending"] and view["activities"][0]["status"]=="failure"

def test_invalid_runtime_shape_is_a_runtime_failure_not_a_storage_failure():
    result=local_activity.generate(lambda:None)
    assert not result["ok"] and "invalid response" in result["error"]
    view=monitoring.database().recent()[0]
    assert not view["pending"] and view["activities"][0]["status"]=="failure"
