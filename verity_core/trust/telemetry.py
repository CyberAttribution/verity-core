"""Bounded integration telemetry; never authorization or outcome certification.

No prompt, response, tool arguments, command text, credentials or environment
capture. Observer-reported fields remain unverified assertions. Local receipt
time determines freshness. Trust Record integrity needs an external checkpoint
to detect wholesale rewriting/truncation; it does not establish completeness.
"""
import math
import re

from . import record as trust

SCHEMA = "development_telemetry/v1"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")
FIELDS = {
    "heartbeat": set(),
    "request_start": {"activity_id", "operation", "task"},
    "request_finish": {"activity_id", "status", "duration_ms", "error_class"},
    "tool_start": {"activity_id", "operation", "task"},
    "tool_finish": {"activity_id", "status", "duration_ms", "error_class"},
    "gpu_sample": {"gpus"},
    "disconnect": set(),
}


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid telemetry identifier")
    return value


def text(value, limit=240):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Invalid telemetry label")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Control characters in telemetry label")
    return value


def number(value, maximum=1e12):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Invalid telemetry measurement")
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Telemetry measurement out of range")
    return value


def manifest(value):
    if not isinstance(value, dict) or set(value) != {"run_id", "agent_id", "name", "runtime", "model"}:
        raise ValueError("Invalid telemetry manifest")
    identifier(value["run_id"]); identifier(value["agent_id"])
    for key in ("name", "runtime", "model"):
        text(value[key])
    return dict(value)


def event(value):
    if not isinstance(value, dict) or set(value) != {"event_id", "kind", "data"}:
        raise ValueError("Invalid telemetry event")
    identifier(value["event_id"])
    kind, data = value["kind"], value["data"]
    if kind not in FIELDS or not isinstance(data, dict) or set(data) - FIELDS[kind]:
        raise ValueError("Unknown telemetry fields")
    if kind.endswith(("_start", "_finish")):
        identifier(data.get("activity_id"))
    if kind.endswith("_start"):
        text(data.get("operation"), 100)
        if "task" in data:
            text(data["task"])
    if kind.endswith("_finish"):
        if data.get("status") not in ("success", "failure"):
            raise ValueError("Invalid activity status")
        number(data.get("duration_ms"))
        if "error_class" in data:
            identifier(data["error_class"])
    if kind == "gpu_sample":
        gpus = data.get("gpus")
        if not isinstance(gpus, list) or not 1 <= len(gpus) <= 16:
            raise ValueError("Invalid GPU sample")
        indexes = set()
        for gpu in gpus:
            if not isinstance(gpu, dict) or set(gpu) != {"index", "name", "memory_used_mib", "memory_total_mib", "utilization_percent"}:
                raise ValueError("Invalid GPU fields")
            index = gpu["index"]
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 16 or index in indexes:
                raise ValueError("Invalid GPU index")
            indexes.add(index); text(gpu["name"], 100)
            used = number(gpu["memory_used_mib"], 1e7)
            total = number(gpu["memory_total_mib"], 1e7)
            if not total or used > total:
                raise ValueError("Invalid GPU memory")
            number(gpu["utilization_percent"], 100)
    # Enforce finite JSON, independent ownership and bounded serialized size.
    encoded = trust.canonical_json(value)
    if len(encoded) > 8192:
        raise ValueError("Telemetry event too large")
    import json
    return json.loads(encoded)


def create(value, received_at):
    value = manifest(value); number(received_at)
    record = trust.new_record({"kind": "development_run", "identifier": value["run_id"]})
    trust.append_entry(record, "note", actor="telemetry-integration", payload={
        "schema": SCHEMA, "manifest": value, "received_at": received_at,
        "provenance": "Integration-reported identity; received locally; not independently verified"})
    return record


def inspect(record, *, now, stale_seconds=30):
    number(now)
    if trust.validate(record) or not trust.chain_valid(record) or not record["entries"]:
        raise ValueError("Telemetry record integrity failed")
    entries = record["entries"]
    if any(e["entry_type"]!="note" or e["actor"]!="telemetry-integration" for e in entries):
        raise ValueError("Telemetry entries must be observational notes")
    first = entries[0]["payload"]
    if set(first) != {"schema", "manifest", "received_at", "provenance"} or first["schema"] != SCHEMA:
        raise ValueError("Invalid telemetry record manifest")
    info = manifest(first["manifest"])
    if record["subject"] != {"kind": "development_run", "identifier": info["run_id"]}:
        raise ValueError("Telemetry subject mismatch")
    last_seen = number(first["received_at"])
    pending, activities, seen, gpus = {}, [], set(), None
    disconnected = False
    recorded_contact_gaps = 0
    for entry in entries[1:]:
        payload = entry["payload"]
        if set(payload) != {"event", "received_at"}:
            raise ValueError("Invalid telemetry record event")
        item = event(payload["event"])
        received = number(payload["received_at"])
        if received < last_seen or item["event_id"] in seen:
            raise ValueError("Duplicate or reordered telemetry")
        kind, data = item["kind"], item["data"]
        if pending and not disconnected and (received-last_seen>stale_seconds or kind=="disconnect"):
            recorded_contact_gaps += 1
        seen.add(item["event_id"]); last_seen = received
        kind, data = item["kind"], item["data"]
        disconnected = kind == "disconnect"
        if kind.endswith("_start"):
            aid = data["activity_id"]
            if aid in pending or any(a["activity_id"] == aid for a in activities):
                raise ValueError("Activity identifier reused")
            pending[aid] = dict(data, kind=kind, received_at=received)
        elif kind.endswith("_finish"):
            aid = data["activity_id"]
            start = pending.get(aid)
            if not start or start["kind"].split("_")[0] != kind.split("_")[0]:
                raise ValueError("Activity finish without matching start")
            activities.append(dict(pending.pop(aid), **data, finished_at=received))
        elif kind == "gpu_sample":
            gpus = {"received_at": received, "gpus": data["gpus"]}
    age = max(0, now - last_seen)
    clock_anomaly=now<last_seen
    status = "Status unknown" if clock_anomaly else "Active" if len(entries) > 1 and age <= stale_seconds and not disconnected else "Recently disconnected" if len(entries) > 1 else "Status unknown"
    head = entries[-1]["entry_hash"]
    return dict(info, status=status, last_seen=last_seen, age_seconds=age,
                pending=list(pending.values()), activities=activities[-100:], gpu_sample=gpus,
                contact_lost_during_activity=bool(pending) and status != "Active",
                recorded_contact_gaps=recorded_contact_gaps, clock_anomaly=clock_anomaly,
                gpu_age_seconds=None if gpus is None else max(0, now-gpus["received_at"]),
                current_task=next((a.get("task") for a in reversed(list(pending.values())) if a.get("task")), "Unknown"),
                record_id=record["record_id"], head=head, entry_count=len(entries))


def append(record, value, received_at):
    item = event(value)
    current = inspect(record, now=received_at)
    for entry in record["entries"][1:]:
        old = entry["payload"]["event"]
        if old["event_id"] == item["event_id"]:
            if old != item:
                raise ValueError("Event identifier reused with different content")
            return False
    if current["entry_count"] >= 5000:
        raise ValueError("Run is full; begin another run")
    import copy
    candidate = copy.deepcopy(record)
    trust.append_entry(candidate, "note", actor="telemetry-integration",
                       payload={"event": item, "received_at": received_at})
    inspect(candidate, now=received_at)
    record.clear(); record.update(candidate)
    return True


def checkpoint(record):
    view = inspect(record, now=record["entries"][-1]["payload"]["received_at"])
    return {"schema": SCHEMA, "run_id": view["run_id"], "record_id": view["record_id"],
            "head": view["head"], "entry_count": view["entry_count"]}


def verify(record, anchor):
    return checkpoint(record) == anchor
