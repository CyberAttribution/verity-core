"""Local, source-linked company research proposals from completed governed runs.

A model response is interpretation, never an operator decision or verified work.
This store records the first two stages; later stages remain visibly pending.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from .customer_config import canonical, label


class CompanyWorkStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS research (id TEXT PRIMARY KEY, agent_task_id TEXT NOT NULL UNIQUE, at REAL NOT NULL, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS research_events (seq INTEGER PRIMARY KEY, work_id TEXT NOT NULL, at REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL)")

    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        return db

    def create(self, work_id, source_url, source_claim, run):
        label(work_id)
        if not isinstance(source_url,str) or len(source_url)>1000:
            raise ValueError("HTTPS source URL required")
        url=urlsplit(source_url)
        if url.scheme!="https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("HTTPS source URL required")
        if not isinstance(source_claim,str) or not source_claim.strip() or len(source_claim)>2000:
            raise ValueError("Bounded source claim required")
        if not isinstance(run,dict) or run.get("state")!="completed" or run.get("decision")!="allowed":
            raise ValueError("Complete allowed governed run required")
        result=run.get("result")
        if not isinstance(result,dict) or not isinstance(result.get("response"),str) or not result["response"].strip():
            raise ValueError("Agent interpretation missing")
        if result.get("runtime_decision") not in ("allowed","allow"):
            raise ValueError("Runtime did not allow this interpretation")
        if hashlib.sha256(result["response"].encode()).hexdigest()!=result.get("response_sha256"):
            raise ValueError("Agent response digest mismatch")
        for key in ("task_id","agent_id","model"):
            label(run.get(key))
        label(result.get("runtime_event_id"))
        cfg=run.get("configuration")
        if not isinstance(cfg,dict) or cfg.get("model",run["model"])!=run["model"]:
            raise ValueError("Effective model identity mismatch")
        snapshot={k:cfg[k] for k in ("agent_version","agent_hash","connection_version","connection_hash","model_revision","resource_id") if k in cfg}
        record=dict(id=work_id,at=time.time(),source_claim=dict(url=source_url,text=source_claim,
                provenance="operator-supplied source; independent source review pending"),
            agent_interpretation=dict(task_id=run["task_id"],agent_id=run["agent_id"],model=run["model"],
                response=result["response"],response_sha256=result["response_sha256"],
                runtime_event_id=result["runtime_event_id"],runtime_decision=result["runtime_decision"],
                configuration=snapshot),
            operator_decision=dict(state="pending",detail=None),assigned_task=None,
            observed_work=None,verified_result=None)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO research VALUES (?,?,?,?)",(work_id,run["task_id"],record["at"],canonical(record)))
        return record

    def get(self,work_id):
        label(work_id)
        with self._db() as db:
            row=db.execute("SELECT record FROM research WHERE id=?",(work_id,)).fetchone()
        if not row:raise KeyError(work_id)
        return json.loads(row["record"])

    def recent(self):
        with self._db() as db:
            return [json.loads(row["record"]) for row in db.execute("SELECT record FROM research ORDER BY at DESC LIMIT 20")]

    def decide(self,work_id,state,detail):
        """Record an explicit local operator choice; never infer it from the model."""
        label(work_id)
        if state not in ("accepted","rejected") or not isinstance(detail,str) or not detail.strip() or len(detail)>1000:
            raise ValueError("Bounded operator decision and explanation required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT record FROM research WHERE id=?",(work_id,)).fetchone()
            if not row:raise KeyError(work_id)
            record=json.loads(row["record"])
            if record["operator_decision"]["state"]!="pending":
                raise ValueError("Operator decision is immutable")
            now=time.time()
            record["operator_decision"]=dict(state=state,detail=detail.strip(),at=now,
                                             provenance="local operator form; identity unverified")
            db.execute("UPDATE research SET record=? WHERE id=?",(canonical(record),work_id))
            db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",
                       (work_id,now,"operator_decision",canonical(record["operator_decision"])))
        return record

    def assign(self,work_id,task_id,assignee):
        label(work_id);label(task_id);label(assignee)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT record FROM research WHERE id=?",(work_id,)).fetchone()
            if not row:raise KeyError(work_id)
            record=json.loads(row["record"])
            if record["operator_decision"]["state"]!="accepted" or record["assigned_task"] is not None:
                raise ValueError("Accepted, unassigned operator decision required")
            now=time.time()
            record["assigned_task"]=dict(task_id=task_id,assignee=assignee,at=now,
                                          provenance="local operator form; work not yet observed")
            db.execute("UPDATE research SET record=? WHERE id=?",(canonical(record),work_id))
            db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",
                       (work_id,now,"task_assigned",canonical(record["assigned_task"])))
        return record

    def events(self,work_id):
        label(work_id)
        with self._db() as db:
            return [dict(row) for row in db.execute(
                "SELECT seq,work_id,at,action,detail FROM research_events WHERE work_id=? ORDER BY seq",(work_id,))]
