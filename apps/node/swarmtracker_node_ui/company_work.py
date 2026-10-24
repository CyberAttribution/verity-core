"""Local, source-linked company research proposals from completed governed runs.

A model response is interpretation, never an operator decision or verified work.
This store records the first two stages; later stages remain visibly pending.
"""
import hashlib
import datetime
import uuid
import json
import re
import subprocess
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from .customer_config import canonical, label
from verity_core.trust import evidence_check


class CompanyWorkStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS research (id TEXT PRIMARY KEY, agent_task_id TEXT NOT NULL UNIQUE, at REAL NOT NULL, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS research_receipts (work_id TEXT PRIMARY KEY, bundle TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS research_events (seq INTEGER PRIMARY KEY, work_id TEXT NOT NULL, at REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL)")

    def check_quotation(self, work_id, snapshot):
        """Deterministic quotation presence only; no semantic or factual verdict."""
        began = time.perf_counter()
        evidence_check.text(snapshot, 2000)
        work = self.get(work_id)
        claim = work["source_claim"]["text"]
        start = snapshot.find(claim)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        identity = "quote-" + uuid.uuid4().hex
        check = dict(schema=evidence_check.SCHEMA,id=identity,
            claim=dict(id=work_id+":source-claim",text=claim,sha256=evidence_check.digest(claim)),
            sources=[dict(id="operator-snapshot",reference=work["source_claim"]["url"],
                revision="operator-supplied; source revision unknown",retrieved_at=stamp,
                text=snapshot,sha256=evidence_check.digest(snapshot))],
            assessment=dict(outcome="supported" if start >= 0 else "insufficient",
                citations=[dict(source_id="operator-snapshot",start=start,end=start+len(claim),passage=claim)] if start >= 0 else [],
                reason="Exact claim quotation occurs in the supplied snapshot." if start >= 0 else "Exact claim quotation was not found; semantic support was not assessed.",
                unresolved=start < 0),
            method=dict(kind="exact_quote_presence",version="1",checker_model="none; deterministic quotation check",
                checker_revision="exact_quote_presence/v1",request_id=identity,attempt=1,max_requests=1,
                latency_ms=(time.perf_counter()-began)*1000,usage=dict(prompt_tokens=0,completion_tokens=0),billing_source="local string comparison; no model request"),
            checked_at=stamp,context_ref=None,limitations=[
                "Source support is not guaranteed factual truth or authorization.",
                "Snapshot supplied by the operator; origin and freshness were not independently verified. Retrieval time records intake time.",
                "Exact quotation only; paraphrases, contradictions and semantic relevance are not assessed."])
        return self.attach_check(work_id,check)

    def attach_check(self, work_id, check):
        """Retain a source-claim check without changing any authorization state."""
        label(work_id)
        # Serialize first to isolate caller mutation and bound storage.
        encoded = canonical(check)
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("Evidence check too large")
        check = json.loads(encoded)
        evidence_check.validate(check)
        if check["context_ref"] is not None:
            raise ValueError("Approved context store is not connected")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM research WHERE id=?", (work_id,)).fetchone()
            if not row:
                raise KeyError(work_id)
            work = json.loads(row["record"])
            if check["claim"]["id"] != work_id + ":source-claim" or check["claim"]["text"] != work["source_claim"]["text"]:
                raise ValueError("Check must bind the exact research source claim")
            if not any(s["reference"] == work["source_claim"]["url"] for s in check["sources"]):
                raise ValueError("Research source snapshot required")
            previous = self._checks(db, work_id)
            if len(previous) >= 20 or any(c["record"]["entries"][0]["payload"]["check"]["id"] == check["id"] for c in previous):
                raise ValueError("Duplicate check or research check allowance exceeded")
            bundle = evidence_check.export(check)
            db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",
                       (work_id,time.time(),"evidence_check_attached",canonical(bundle)))
        return bundle

    @staticmethod
    def _checks(db, work_id):
        bundles = [json.loads(r["detail"]) for r in db.execute(
            "SELECT detail FROM research_events WHERE work_id=? AND action='evidence_check_attached' ORDER BY seq", (work_id,))]
        for bundle in bundles:
            if not evidence_check.verify(bundle, bundle.get("checkpoint")):
                raise ValueError("Stored evidence integrity check failed")
        return bundles

    def checks(self, work_id):
        self.get(work_id)
        with self._db() as db:
            return self._checks(db, work_id)

    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        return db

    def create(self, work_id, source_url, source_claim, run, *, article=None, request_bundle=None, intake_identity=None):
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
        if article is not None:
            from verity_core.trust import research_record
            record["article"] = article
            record["request_id"] = run["id"]
            record["configuration"] = cfg
            record["request_bundle"] = request_bundle
            if intake_identity is not None:
                record["intake_identity"] = intake_identity
            record["proposal_binding"] = research_record.binding(record)
            bundle = research_record.propose(record)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if article is not None:
                db.execute("INSERT INTO research_receipts VALUES (?,?)", (work_id,canonical(bundle)))
            db.execute("INSERT INTO research VALUES (?,?,?,?)",(work_id,run["task_id"],record["at"],canonical(record)))
        return record

    def get(self,work_id):
        label(work_id)
        with self._db() as db:
            row=db.execute("SELECT record FROM research WHERE id=?",(work_id,)).fetchone()
        if not row:raise KeyError(work_id)
        record=json.loads(row["record"])
        if "article" in record:
            from verity_core.trust import research_record
            try:bundle=self.receipt(work_id)
            except KeyError:raise ValueError("Required research receipt missing") from None
            if bundle["record"]["entries"][0]["payload"]["proposal"]!=research_record.proposal(record):
                raise ValueError("Research display differs from bound proposal")
            entries=bundle["record"]["entries"]
            state=entries[-1]["payload"]["state"] if len(entries)==2 else "pending"
            if record["operator_decision"]["state"]!=state:raise ValueError("Research decision differs from receipt")
            detail=entries[-1]["payload"]["reason"] if len(entries)==2 else None
            if record["operator_decision"]["detail"]!=detail:
                raise ValueError("Research operator qualifications differ from receipt")
        return record

    def recent(self):
        with self._db() as db:
            identifiers=[row["id"] for row in db.execute("SELECT id FROM research ORDER BY at DESC LIMIT 20")]
        return [self.get(identifier) for identifier in identifiers]

    def decide(self,work_id,state,detail, *, expected_binding=None):
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
            if "article" in record:
                from verity_core.trust import research_record
                if expected_binding != record["proposal_binding"] or expected_binding != research_record.binding(record):
                    raise ValueError("Exact reviewed article, output and configuration required")
                row = db.execute("SELECT bundle FROM research_receipts WHERE work_id=?",(work_id,)).fetchone()
                if not row:raise ValueError("Required research receipt missing")
                bundle=json.loads(row[0])
                if not research_record.verify(bundle,bundle["checkpoint"]):
                    raise ValueError("Research evidence integrity failed")
                bundle=research_record.decide(bundle,state,detail)
                db.execute("UPDATE research_receipts SET bundle=? WHERE work_id=?",(canonical(bundle),work_id))
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

    def observe_commit(self,work_id,commit_sha,repository):
        """Check a local Git commit object; this does not verify task relevance."""
        label(work_id)
        if not isinstance(commit_sha,str) or not re.fullmatch(r"[0-9a-f]{40}",commit_sha):
            raise ValueError("Full lowercase commit SHA required")
        root=Path(repository).resolve()
        if not (root/".git").exists():
            raise ValueError("Local source checkout unavailable")
        try:
            result=subprocess.run(["git","-C",str(root),"cat-file","-t",commit_sha],
                                  capture_output=True,text=True,timeout=5,check=True)
        except (OSError,subprocess.SubprocessError):
            raise ValueError("Git commit could not be verified") from None
        if result.stdout.strip()!="commit":
            raise ValueError("Git object is not a commit")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT record FROM research WHERE id=?",(work_id,)).fetchone()
            if not row:raise KeyError(work_id)
            record=json.loads(row["record"])
            if record["operator_decision"]["state"]!="accepted" or not record["assigned_task"] or record["observed_work"] is not None:
                raise ValueError("Accepted, assigned, unobserved task required")
            now=time.time()
            record["observed_work"]=dict(commit_sha=commit_sha,at=now,
                provenance="local Git commit object verified; task relevance and tests not verified")
            db.execute("UPDATE research SET record=? WHERE id=?",(canonical(record),work_id))
            db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",
                       (work_id,now,"commit_observed",canonical(record["observed_work"])))
        return record

    def receipt(self,work_id):
        from verity_core.trust import research_record
        with self._db() as db:
            row=db.execute("SELECT bundle FROM research_receipts WHERE work_id=?",(work_id,)).fetchone()
        if not row:raise KeyError(work_id)
        bundle=json.loads(row[0])
        if not research_record.verify(bundle,bundle["checkpoint"]):
            raise ValueError("Research evidence integrity failed")
        return bundle

    @staticmethod
    def _correction_rows(db, work_id):
        return [json.loads(row[0]) for row in db.execute(
            "SELECT detail FROM research_events WHERE work_id=? AND action='research_correction' ORDER BY seq",(work_id,))]

    def corrections(self,work_id):
        from verity_core.trust import evidence_profile
        decision=self.receipt(work_id)
        with self._db() as db:items=self._correction_rows(db,work_id)
        return evidence_profile.validate_corrections(decision,items)

    def correct(self,work_id,text,*,kind,expected_binding,expected_head):
        from verity_core.trust import evidence_profile
        label(work_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            work=self.get(work_id);decision=self.receipt(work_id)
            if work.get('proposal_binding')!=expected_binding or 'intake_identity' not in work:
                raise ValueError('Bound reviewed intake required')
            previous=self._correction_rows(db,work_id)
            evidence_profile.validate_corrections(decision,previous)
            head=previous[-1]['checkpoint']['head_hash'] if previous else ''
            if expected_head!=head:raise ValueError('Correction history changed; review current history')
            if len(previous)>=20:raise ValueError('Correction history allowance exceeded')
            bundle=evidence_profile.correction(decision,previous,text,kind=kind)
            db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",
                (work_id,time.time(),'research_correction',canonical(bundle)))
        return bundle

    def prepare_profile(self,work_id,denial):
        from verity_core.trust import evidence_profile
        label(work_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            decision=self.receipt(work_id);corrections=self._correction_rows(db,work_id)
            bundle=evidence_profile.assemble(decision,denial,corrections)
            rows=db.execute("SELECT detail FROM research_events WHERE work_id=? AND action='provisional_profile_exported' ORDER BY seq",(work_id,)).fetchall()
            # Repeated export of unchanged inputs returns the same checkpoint.
            wanted=bundle['record']['entries'][0]['payload']
            for old in self.profiles(work_id):
                if old['record']['entries'][0]['payload']==wanted:return old
            if len(rows)>=20:raise ValueError('Profile export allowance exceeded')
            db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",
                (work_id,time.time(),'provisional_profile_exported',canonical(bundle)))
        return bundle

    def profiles(self,work_id):
        from verity_core.trust import evidence_profile
        label(work_id)
        with self._db() as db:
            items=[json.loads(row[0]) for row in db.execute("SELECT detail FROM research_events WHERE work_id=? AND action='provisional_profile_exported' ORDER BY seq",(work_id,))]
        decision=self.receipt(work_id);corrections=self.corrections(work_id)
        for bundle in items:
            if not evidence_profile.verify(bundle,bundle['checkpoint']):raise ValueError('Retained profile integrity differs')
            payload=bundle['record']['entries'][0]['payload']
            if (bundle['record']['subject']['identifier']!=work_id or payload['decision']!=decision
                or payload['corrections']!=corrections[:len(payload['corrections'])]):
                raise ValueError('Retained profile differs from this work and its correction history')
        return items

    def disclosure_parent(self,work_id,head,*,current=False):
        items=[b for b in self.profiles(work_id) if b['checkpoint']['head_hash']==head]
        if len(items)!=1:raise KeyError('Exact retained parent profile unavailable')
        parent=items[0]
        if current and len(parent['record']['entries'][0]['payload']['corrections'])!=len(self.corrections(work_id)):
            raise ValueError('Profile cutoff is stale; prepare a new full profile before reduced export')
        return parent

    def prepare_disclosure(self,work_id,parent_head):
        from verity_core.trust import selective_profile
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            parent=self.disclosure_parent(work_id,parent_head,current=True)
            wanted=selective_profile.projection(parent)
            old=self.disclosures(work_id)
            for bundle in old:
                if {k:v for k,v in bundle['record']['entries'][0]['payload'].items() if k!='schema'}==wanted:return bundle
            if len(old)>=20:raise ValueError('Reduced export allowance exceeded')
            bundle=selective_profile.create(parent)
            db.execute('INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)',(work_id,time.time(),'reduced_profile_exported',canonical(bundle)))
        return bundle

    def disclosures(self,work_id):
        from verity_core.trust import selective_profile
        label(work_id)
        with self._db() as db:
            items=[json.loads(row[0]) for row in db.execute("SELECT detail FROM research_events WHERE work_id=? AND action='reduced_profile_exported' ORDER BY seq",(work_id,))]
        for bundle in items:
            if bundle['record']['subject']!={'kind':'internal_reduced_profile','identifier':work_id}:raise ValueError('Reduced copy belongs to different work')
            ref=bundle['record']['entries'][0]['payload']['parent_references']['profile']
            parent=self.disclosure_parent(work_id,ref['head_hash'])
            checks=selective_profile.verify(bundle,bundle['checkpoint'],parent=parent)
            if not checks['child_integrity'] or checks['correspondence'] is not True:raise ValueError('Reduced copy or parent correspondence differs')
        return items
