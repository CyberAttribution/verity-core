"""Isolated experimental OPA decision adapter; never substitutes Keel approval.

The caller must enforce the returned decision at its own execution boundary.
"""
import hashlib
import json
import urllib.parse
import urllib.request
import sqlite3
import time
import uuid
from pathlib import Path


class DecisionLedger:
    """Local evidence for the isolated experiment, not an authenticated audit log."""
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, at REAL NOT NULL, policy_version TEXT NOT NULL, policy_sha256 TEXT NOT NULL, allow INTEGER NOT NULL, reason TEXT NOT NULL, keel_approved INTEGER NOT NULL)")
            columns={row[1] for row in db.execute("PRAGMA table_info(decisions)")}
            for name in ("session_id", "task_id", "request_id"):
                if name not in columns:
                    db.execute(f"ALTER TABLE decisions ADD COLUMN {name} TEXT")

    def record(self, decision, policy_version, keel_approved, linkage=None):
        if not isinstance(policy_version,str) or not policy_version or len(policy_version)>120:
            raise ValueError("Policy version required")
        linkage=linkage or {}
        if not isinstance(linkage,dict) or any(k not in {"session_id","task_id","request_id"} for k in linkage):
            raise ValueError("Unexpected decision linkage")
        if any(not isinstance(v,str) or not v or len(v)>160 for v in linkage.values()):
            raise ValueError("Invalid decision linkage")
        identifier=uuid.uuid4().hex
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute("INSERT INTO decisions (id,at,policy_version,policy_sha256,allow,reason,keel_approved,session_id,task_id,request_id) VALUES (?,?,?,?,?,?,?,?,?,?)",(
                identifier,time.time(),policy_version,decision["policy_sha256"],
                int(decision["allow"]),decision["reason"],int(bool(keel_approved)),
                linkage.get("session_id"),linkage.get("task_id"),linkage.get("request_id")))
        return identifier

    def records(self):
        with sqlite3.connect(self.path) as db:
            return [dict(zip(("id","at","policy_version","policy_sha256","allow","reason","keel_approved","session_id","task_id","request_id"),row))
                    for row in db.execute("SELECT * FROM decisions ORDER BY rowid")]


def decide(input_document, policy_bytes, evaluator=None):
    if not isinstance(input_document,dict) or not input_document.get("subject") or not input_document.get("action"):
        return {"allow":False,"reason":"missing-input","policy_sha256":hashlib.sha256(policy_bytes).hexdigest()}
    digest=hashlib.sha256(policy_bytes).hexdigest()
    try:
        response=(evaluator or local_opa)(input_document)
        if not isinstance(response,dict) or type(response.get("result")) is not bool:
            return {"allow":False,"reason":"undefined-decision","policy_sha256":digest}
        return {"allow":response["result"],"reason":"opa-decision","policy_sha256":digest}
    except Exception:
        return {"allow":False,"reason":"evaluator-unavailable","policy_sha256":digest}


def local_opa(input_document, endpoint="http://127.0.0.1:8181/v1/data/verity/allow"):
    url=urllib.parse.urlparse(endpoint)
    if (url.scheme,url.hostname,url.port,url.path) != ("http","127.0.0.1",8181,"/v1/data/verity/allow"):
        raise ValueError("OPA endpoint must be local and fixed")
    req=urllib.request.Request(endpoint,data=json.dumps({"input":input_document}).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=2) as response:
        return json.load(response)


def execute_if_allowed(document, policy_bytes, action, evaluator=None, keel_approved=False,
                       ledger=None, policy_version=None):
    if ledger is None:
        raise PermissionError("Decision ledger required")
    decision=decide(document,policy_bytes,evaluator)
    # Fail closed if evidence cannot be stored, including on an allow.
    linkage={k:document[k] for k in ("session_id","task_id","request_id") if isinstance(document,dict) and k in document}
    decision["record_id"]=ledger.record(decision,policy_version,keel_approved,linkage)
    if not keel_approved or not decision["allow"]:
        raise PermissionError(decision["reason"] if keel_approved else "Keel approval required")
    return action(),decision
