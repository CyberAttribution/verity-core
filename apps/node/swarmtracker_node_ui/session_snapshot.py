"""Import trusted SSH collector observations, never tasks or provider billing facts.

The caller owns transport authentication and the local snapshot path. Importing
this JSON is not authentication. Source timestamps stay intact across replay.
"""
import hashlib
import fcntl
import json
import math
import time

from .sessions import derive


def stamp(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<4102444800:
        raise ValueError("Invalid observation timestamp")
    return value


def observation_events(snapshot,*,now=None):
    now=time.time() if now is None else stamp(now)
    if not isinstance(snapshot,dict):
        raise ValueError("Invalid collector snapshot")
    encoded=json.dumps(snapshot,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    if len(encoded)>1048576:
        raise ValueError("Collector snapshot exceeds bound")
    at=stamp(snapshot.get("at"))
    if at>now+5:
        raise ValueError("Collector source clock is in the future")
    identifier="snapshot."+hashlib.sha256(encoded).hexdigest()
    events=[]
    def add(suffix,kind,data,observed_at):
        events.append(dict(id=identifier+"."+suffix,at=observed_at,kind=kind,source="ssh",data=data))
    add("contact","contact",dict(state="reachable"),at)
    add("connection","connection",dict(state="connected",detail="Authenticated SSH collector sample; provider allocation and billing remain separately unverified"),at)
    setup=snapshot.get("setup")
    if not isinstance(setup,dict):
        add("model","model",dict(state="unknown",detail="Replica service observations unavailable"),at)
        return events
    setup_at=stamp(setup.get("at"))
    if setup_at>at+5:
        raise ValueError("Replica observation clock is ahead of its collector sample")
    observed_at=min(at,setup_at)
    replicas=setup.get("replicas")
    services=[]
    if isinstance(replicas,dict):
        for index in ("0","1"):
            replica=replicas.get(index)
            services.append(replica.get("service") if isinstance(replica,dict) else None)
    ready=len(services)==2
    models=set()
    running=0
    for service in services:
        if not isinstance(service,dict) or service.get("state")!="ready":
            ready=False
            continue
        names=service.get("models")
        count=service.get("running_requests")
        if (not isinstance(names,list) or not names or len(names)>16 or any(
            not isinstance(name,str) or not name or len(name)>240 or any(ord(c)<32 for c in name) for name in names)
            or type(count) not in (int,float) or not math.isfinite(count) or not 0<=count<=10000 or int(count)!=count):
            ready=False
            continue
        models.update(names)
        running+=int(count)
    if not ready:
        add("inventory","inventory",dict(checked=False,models=sorted(models)),observed_at)
        add("model","model",dict(state="unknown",detail="Both replica service observations are not confirmed; worker task markers do not establish readiness"),observed_at)
    else:
        add("inventory","inventory",dict(checked=True,models=sorted(models)),observed_at)
        add("model","model",dict(state="inference" if running else "ready",
            name=next(iter(models)) if len(models)==1 else "Multiple model IDs; see inventory",
            revision="Not established by service API; launch pin recorded separately",
            detail="Two independent loopback service observations; running requests="+str(running)+". This does not assign tasks or verify outputs."),observed_at)
    return events


def import_snapshot(database,session_id,snapshot,*,now=None):
    # Mac/Linux single-writer admission across collector restart/overlap. The OS
    # releases the lock on process death; no indefinite wait on a live worker.
    path=database.path.with_name(database.path.name+".snapshot.lock")
    with path.open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _import_locked(database,session_id,snapshot,now=now)


def _import_locked(database,session_id,snapshot,*,now=None):
    now=time.time() if now is None else stamp(now)
    record=database.get(session_id)
    if derive(record,now=now)["termination_confirmed"]:
        return dict(imported=0,state="provider-confirmed session ended; snapshot not imported")
    events=observation_events(snapshot,now=now)
    prior=[e for e in record["events"] if e["id"].startswith("snapshot.") and e["kind"]=="contact"]
    if prior:
        latest=max(prior,key=lambda e:e["at"])
        if snapshot["at"]<latest["at"]:
            return dict(imported=0,state="older source snapshot ignored",source_at=snapshot["at"])
        if snapshot["at"]==latest["at"] and events[0]["id"]!=latest["id"]:
            raise ValueError("Conflicting collector snapshots at the same source time")
    model_facts=[e for e in record["events"] if e["kind"] in {"inventory","model"}]
    if model_facts:
        latest_model_at=max(e["at"] for e in model_facts)
        prefix=events[0]["id"].rsplit(".",1)[0]+"."
        same_sample_at_latest=any(e["at"]==latest_model_at and e["id"].startswith(prefix) for e in model_facts)
        # Guest contact can advance while a cached replica probe goes backwards.
        # Keep contact current without overwriting newer model/inventory facts.
        # Equal-time events from this exact sample may finish a partial import.
        events=[e for e in events if e["kind"] not in {"inventory","model"}
                or e["at"]>latest_model_at or (e["at"]==latest_model_at and same_sample_at_latest)]
    imported=0
    for event in events:
        imported+=bool(database.append(session_id,event))
    return dict(imported=imported,state="observations imported",source_at=snapshot["at"])
