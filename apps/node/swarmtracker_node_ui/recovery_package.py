"""Portable, deliberately non-executable recovery metadata and safe output copies."""
import hashlib
import json
import re
import zipfile
from pathlib import Path


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _safe_output(value):
    if not isinstance(value,str) or len(value)>100000:
        raise ValueError("Output must be bounded reviewed text")
    if re.search(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+)",value):
        raise ValueError("Potential credential in output")
    return value


def build(snapshot, destination, checkpoint_path, *, source_commit, reviewed_outputs=None):
    """Checkpoint must be retained separately from the zip (ideally another device)."""
    if not re.fullmatch(r"[0-9a-f]{40}",source_commit): raise ValueError("Full source commit required")
    destination=Path(destination); checkpoint_path=Path(checkpoint_path)
    if destination.resolve()==checkpoint_path.resolve(): raise ValueError("Independent checkpoint location required")
    reviewed_outputs=reviewed_outputs or {}
    for work in snapshot["work_items"]:
        for value in json.loads(work["spec"]).values():
            if isinstance(value,str): _safe_output(value)
    if len(reviewed_outputs)>20: raise ValueError("Too many outputs")
    outputs={}
    for name,content in reviewed_outputs.items():
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}",name): raise ValueError("Invalid output name")
        outputs[name]=_safe_output(content)
    data={"schema":"verity-recovery/v1","source_commit":source_commit,
          "control":{k:snapshot["control"][k] for k in ("mode","rate","start","ceiling","margin")},
          "worker_observation":{k:snapshot["worker"].get(k) for k in ("state","pid","heartbeat","current_task")},
          "estimated_charge":snapshot["estimated_charge"],"provider_billing":"not included; reconcile separately",
          "work_items":[{"id":w["id"],"spec":json.loads(w["spec"]),"state":w["state"],
                         "checkpoint_hash":_digest((w["checkpoint"] or "").encode()) if w["checkpoint"] else None,
                         "evidence_hash":_digest((w["evidence"] or "").encode()) if w["evidence"] else None,
                         "updated":w["updated"]} for w in snapshot["work_items"]],
          "requests":[{"id":t["id"],"state":t["state"],"request_id":t["request_id"],
                       "finish_reason":t["finish_reason"],"dependencies":json.loads(t["spec"])["dependencies"],
                       "authorization":json.loads(t["spec"])["authorization"],
                       "model_settings":json.loads(t["spec"])["model_settings"],
                       "body":"withheld; operator must rehydrate before any queued dispatch",
                       "response_hash":_digest(t["response"].encode()) if t["response"] else None,
                       "evidence_hash":_digest(t["evidence"].encode()) if t["evidence"] else None,
                       "verifier":t["verifier"],"updated":t["updated"]} for t in snapshot["tasks"]],
          "transitions":[{**t,"detail":None,"detail_hash":_digest((t["detail"] or "").encode())} for t in snapshot["transitions"]],
          "continuations":snapshot["continuations"],
          "work_sources":snapshot["work_sources"],"followup_settings":snapshot["followup_settings"],
          "continuation_authorizations":snapshot["continuation_authorizations"],
          "workflow_links":[{**x,"provenance_hash":_digest(x["provenance"].encode()),"provenance":"withheld"} for x in snapshot["workflow_links"]],
          "attention_history":[{**x,"detail_hash":_digest(x["detail"].encode()),"detail":"withheld"} for x in snapshot["attention_history"]],
          "work_approvals":[{**x,"evidence_hash":_digest(x["evidence"].encode()),"evidence":"withheld"} for x in snapshot["work_approvals"]],
          "work_approval_gates":snapshot["work_approval_gates"],
          "work_corrections":[{**x,"evidence_hash":_digest(x["evidence"].encode()),"evidence":"withheld"} for x in snapshot["work_corrections"]],
          "work_successors":[{**x,"reason_hash":_digest(x["reason"].encode()),"reason":"withheld"} for x in snapshot["work_successors"]],
          "outputs":{name:{"sha256":_digest(value.encode()),"path":"outputs/"+name} for name,value in outputs.items()}}
    encoded=json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2).encode()
    readme=("# Verity recovery handoff\n\nThis is an observation package, not an executable worker database. "
            "Check its SHA-256 against the separately retained checkpoint before reading it. "
            "Run `python -m swarmtracker_node_ui.recovery_package PACKAGE CHECKPOINT` in a separate profile.\n\n"
            "Requests marked submitted or unresolved must be reconciled before retry. Queued request bodies are withheld. "
            "Review outputs and approval, dependency, worker and budget gates before any dispatch. "
            "Provider billing is separate from the time-based estimate. Saving this file does not make it available to every chat.\n")
    destination.parent.mkdir(parents=True,exist_ok=True); checkpoint_path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md",readme)
        archive.writestr("recovery.json",encoded)
        for name,content in outputs.items(): archive.writestr("outputs/"+name,content)
    checkpoint_path.write_text(_digest(destination.read_bytes())+"  "+destination.name+"\n")
    return _digest(destination.read_bytes())


def load(package, checkpoint_path):
    package=Path(package); checkpoint_path=Path(checkpoint_path)
    expected=checkpoint_path.read_text().split()[0]
    if _digest(package.read_bytes())!=expected: raise ValueError("Recovery package differs from retained checkpoint")
    with zipfile.ZipFile(package) as archive:
        if any(x.filename.startswith("/") or ".." in Path(x.filename).parts for x in archive.infolist()):
            raise ValueError("Unsafe package path")
        data=json.loads(archive.read("recovery.json"))
        if data.get("schema")!="verity-recovery/v1": raise ValueError("Unknown recovery schema")
        for name,output in data["outputs"].items():
            if _digest(archive.read(output["path"]))!=output["sha256"]: raise ValueError("Output integrity failed")
    states={state:[x["id"] for x in data["requests"] if x["state"]==state] for state in
            ("queued","submitted","acknowledged","completed","independently_verified","failed","unresolved")}
    by_id={w["id"]:w for w in data["work_items"]}
    eligible=[w["id"] for w in data["work_items"] if w["state"]=="queued" and
              all(by_id.get(dep,{}).get("state")=="finished" for dep in w["spec"]["dependencies"])]
    blocked=[{"id":w["id"],"dependencies":[dep for dep in w["spec"]["dependencies"]
                                      if by_id.get(dep,{}).get("state")!="finished"]} for w in data["work_items"]
             if w["state"]=="queued" and w["id"] not in eligible]
    return {"source_commit":data["source_commit"],"work_items":data["work_items"],"request_states":states,
            "worker":data["worker_observation"],"estimated_charge":data["estimated_charge"],
            "budget_remaining_for_dispatch":max(0,data["control"]["ceiling"]-data["control"]["margin"]-data["estimated_charge"]),
            "next_eligible_work":eligible,"blocked_work":blocked,"running_work":[w["id"] for w in data["work_items"] if w["state"]=="working"],
            "next_eligible_requires_live_gate_check":True,"raw_request_bodies_available":False}


if __name__=="__main__":
    import sys
    print(json.dumps(load(sys.argv[1],sys.argv[2]),indent=2))
