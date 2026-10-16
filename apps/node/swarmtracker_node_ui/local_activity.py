"""Observe the real local analysis boundary without logging evidence content."""
import threading,time,uuid
from . import config,monitoring

def generate(call):
    rid="analysis-"+uuid.uuid4().hex;activity="request-"+uuid.uuid4().hex
    manifest=dict(run_id=rid,agent_id="verity-local-analysis",name="Local analysis",
                  runtime="Verity "+config.RUNTIME_KIND+" integration",model=config.DEFAULT_MODEL)
    stop=threading.Event();failed=threading.Event()
    def emit(kind,data=None):
        monitoring.database().ingest(manifest,dict(event_id="evt-"+uuid.uuid4().hex,kind=kind,data=data or {}))
    def heartbeat():
        while not stop.wait(8):
            try:emit("heartbeat")
            except Exception:failed.set();return
    try:
        emit("heartbeat")
        emit("request_start",dict(activity_id=activity,operation="local_analysis",task="Local evidence analysis"))
    except Exception:
        return {"ok":False,"error":"The local activity record could not be opened. No analysis was started. Check available disk space and restart Verity."}
    thread=threading.Thread(target=heartbeat,daemon=True);started=time.monotonic()
    try:
        thread.start()
        result=call()
        if not isinstance(result,dict):
            result={"ok":False,"error":"The local runtime returned an invalid response. Test its connection and retry."}
    except Exception:
        result={"ok":False,"error":"The local runtime request failed. Test the saved connection in Installation & Runtime, then retry."}
    finally:
        stop.set()
        if thread.ident is not None:thread.join(timeout=12)
    try:
        emit("request_finish",dict(activity_id=activity,status="success" if result.get("ok") else "failure",duration_ms=(time.monotonic()-started)*1000))
    except Exception:failed.set()
    try:emit("disconnect")
    except Exception:failed.set()
    if failed.is_set():
        return {"ok":False,"error":"The analysis request ran, but its activity history could not be saved completely. No completed report was accepted. Check local storage before retrying."}
    result=dict(result,activity_run_id=rid)
    return result
