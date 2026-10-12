"""
store — SwarmTracker Node local persistence (v0.2.0, SQLite).

Design rules (v0.2.0 assessment §3):
- DB at <project root>/data/swarmtracker_node.db (WAL). Ledger and Keel
  policy are never touched by this module.
- Original evidence is immutable: artifacts are never UPDATEd after
  insert; analyst changes are notes/revisions. Analyses link to artifacts
  M:N so one artifact appears in many views without duplication.
- Every entity/relationship/analysis carries epistemic status + provenance.
- Migrations are versioned; a timestamped backup is taken before any
  migration on an existing DB; a corrupted DB is reported, never silently
  reinitialized.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from . import config

DB_DIR = Path(os.environ.get("NODE_UI_DATA_DIR",
                             str(config.PROJECT_ROOT / "data")))
DB_PATH = Path(os.environ.get("NODE_UI_DB", str(DB_DIR / "swarmtracker_node.db")))

SCHEMA_VERSION = 2

INVESTIGATION_STATUSES = ("open", "active", "monitoring", "closed",
                          "archived")

EPISTEMIC_STATUSES = (
    "directly_observed", "deterministic", "model_inferred", "analyst_added",
    "analyst_confirmed", "disputed", "rejected", "superseded")
TIME_STATUSES = ("claimed", "observed", "inferred", "unknown", "disputed")

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  name TEXT NOT NULL, description TEXT DEFAULT '',
  status TEXT DEFAULT 'active',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL, summary TEXT DEFAULT '',
  t_claimed TEXT DEFAULT '', t_observed_start TEXT DEFAULT '',
  t_observed_end TEXT DEFAULT '', time_status TEXT DEFAULT 'unknown',
  position INTEGER DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  event_id TEXT REFERENCES events(id),
  label TEXT NOT NULL, evidence_type TEXT DEFAULT 'freeform_text',
  event_category TEXT DEFAULT '', workflow TEXT DEFAULT 'general_evidence',
  text_content TEXT NOT NULL, sha256 TEXT NOT NULL,
  source TEXT DEFAULT 'manual',
  claimed_sender TEXT DEFAULT '', claimed_org TEXT DEFAULT '',
  requested_action TEXT DEFAULT '',
  media_json TEXT DEFAULT '', t_ingested REAL NOT NULL,
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  event_id TEXT REFERENCES events(id),
  kind TEXT NOT NULL CHECK(kind IN ('single','fused')),
  workflow TEXT DEFAULT 'general_evidence',
  report_id TEXT DEFAULT '', report_json TEXT NOT NULL,
  model TEXT DEFAULT '', policy TEXT DEFAULT '',
  ledger_event_ids_json TEXT DEFAULT '[]',
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_artifacts(
  analysis_id TEXT NOT NULL REFERENCES analyses(id),
  artifact_id TEXT NOT NULL REFERENCES artifacts(id),
  PRIMARY KEY(analysis_id, artifact_id));
CREATE TABLE IF NOT EXISTS entities(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  entity_key TEXT NOT NULL, type TEXT NOT NULL, label TEXT NOT NULL,
  epistemic_status TEXT NOT NULL DEFAULT 'deterministic',
  provenance_json TEXT DEFAULT '{}',
  created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(project_id, entity_key));
CREATE TABLE IF NOT EXISTS relationships(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  source_key TEXT NOT NULL, target_key TEXT NOT NULL,
  relation TEXT NOT NULL, artifact_ids_json TEXT DEFAULT '[]',
  epistemic_status TEXT NOT NULL DEFAULT 'deterministic',
  provenance_json TEXT DEFAULT '{}',
  created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(project_id, source_key, target_key, relation));
CREATE TABLE IF NOT EXISTS notes(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
  body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS timeline_entries(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  event_id TEXT, artifact_id TEXT, analysis_id TEXT,
  label TEXT NOT NULL,
  t_claimed TEXT DEFAULT '', t_observed TEXT DEFAULT '',
  t_ingested REAL, t_range_end TEXT DEFAULT '',
  time_status TEXT NOT NULL DEFAULT 'unknown',
  position INTEGER DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(
  id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
  action TEXT NOT NULL, change_json TEXT DEFAULT '{}',
  actor TEXT DEFAULT 'analyst', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_project ON events(project_id);
CREATE INDEX IF NOT EXISTS ix_artifacts_project ON artifacts(project_id);
CREATE INDEX IF NOT EXISTS ix_analyses_project ON analyses(project_id);
CREATE INDEX IF NOT EXISTS ix_entities_project ON entities(project_id);
CREATE INDEX IF NOT EXISTS ix_rel_project ON relationships(project_id);
CREATE INDEX IF NOT EXISTS ix_timeline_project ON timeline_entries(project_id);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS canonical_cases(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  event_id TEXT REFERENCES events(id),
  analysis_id TEXT REFERENCES analyses(id),
  media_label TEXT DEFAULT '', media_family TEXT DEFAULT '',
  media_sha256 TEXT DEFAULT '', source_kind TEXT DEFAULT 'upload',
  conclusion TEXT NOT NULL,
  corroboration_status TEXT DEFAULT 'uncorroborated',
  case_json TEXT NOT NULL,
  evidence_package_json TEXT NOT NULL,
  assessment_json TEXT DEFAULT '',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cases_project ON canonical_cases(project_id);
"""

_MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2}


class StoreError(Exception):
    pass


class CorruptDatabase(StoreError):
    pass


def _now():
    return time.time()


def _nid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class Store:
    """One connection per Store; FastAPI usage creates it at startup."""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.db_path.exists()
        self.con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        try:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError as e:
            raise CorruptDatabase(f"database unreadable: {e}")
        if existed:
            self._integrity_check()
        self._migrate(existed)

    # ---- integrity / migration / backup ----
    def _integrity_check(self):
        try:
            row = self.con.execute("PRAGMA integrity_check").fetchone()
            if row[0] != "ok":
                raise CorruptDatabase(f"integrity_check: {row[0]}")
        except sqlite3.DatabaseError as e:
            raise CorruptDatabase(f"database unreadable: {e}")

    def current_version(self):
        try:
            row = self.con.execute(
                "SELECT MAX(version) FROM schema_migrations").fetchone()
            return row[0] or 0
        except sqlite3.OperationalError:
            return 0

    def backup(self, tag="manual"):
        """Timestamped copy next to the DB. Returns the backup path."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.db_path.with_name(
            f"{self.db_path.stem}.backup-{tag}-{stamp}.db")
        self.con.commit()
        target = sqlite3.connect(str(dest))
        with target:
            self.con.backup(target)
        target.close()
        return dest

    def _migrate(self, existed):
        v = self.current_version()
        if v >= SCHEMA_VERSION:
            return
        if existed and v > 0:
            self.backup(tag=f"pre-migrate-v{v}-to-v{SCHEMA_VERSION}")
        with self.con:
            for version in range(v + 1, SCHEMA_VERSION + 1):
                self.con.executescript(_MIGRATIONS[version])
                self.con.execute(
                    "INSERT OR REPLACE INTO schema_migrations VALUES (?,?)",
                    (version, _now()))
        # default workspace on first init
        if not self.con.execute("SELECT 1 FROM workspaces LIMIT 1").fetchone():
            with self.con:
                self.con.execute(
                    "INSERT INTO workspaces VALUES (?,?,?,?)",
                    ("ws-default", "Default Workspace", _now(), _now()))

    def _rev(self, kind, target_id, action, change=None, actor="analyst"):
        self.con.execute(
            "INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
            (_nid("rev"), kind, target_id, action,
             json.dumps(change or {}, default=str), actor, _now()))

    # ---- projects ----
    def create_project(self, name, description="", workspace_id="ws-default"):
        pid = _nid("proj")
        with self.con:
            self.con.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?,?)",
                (pid, workspace_id, name.strip()[:200],
                 description.strip()[:2000], "open", _now(), _now()))
            self._rev("project", pid, "create", {"name": name})
        return pid

    def update_project(self, pid, name=None, description=None, status=None):
        p = self.get_project(pid)
        if not p:
            raise StoreError("project not found")
        if status is not None and status not in INVESTIGATION_STATUSES:
            raise StoreError(f"invalid status '{status}'")
        with self.con:
            self.con.execute(
                "UPDATE projects SET name=?, description=?, status=?, "
                "updated=? WHERE id=?",
                (name.strip()[:200] if name is not None else p["name"],
                 description.strip()[:2000] if description is not None
                 else p["description"],
                 status if status is not None else p["status"],
                 _now(), pid))
            self._rev("project", pid, "update",
                      {"name": name, "description": bool(description),
                       "status": status})

    def list_projects(self, include_archived=False):
        q = """SELECT p.*,
               (SELECT COUNT(*) FROM events e WHERE e.project_id=p.id) AS n_events,
               (SELECT COUNT(*) FROM artifacts a WHERE a.project_id=p.id) AS n_artifacts,
               (SELECT COUNT(*) FROM analyses x WHERE x.project_id=p.id) AS n_analyses
               FROM projects p"""
        if not include_archived:
            q += " WHERE status != 'archived'"
        return [dict(r) for r in self.con.execute(q + " ORDER BY updated DESC")]

    def get_project(self, pid):
        r = self.con.execute("SELECT * FROM projects WHERE id=?",
                             (pid,)).fetchone()
        return dict(r) if r else None

    def delete_project(self, pid, confirm_name):
        """Explicit-confirmation delete: caller must pass the exact name."""
        p = self.get_project(pid)
        if not p:
            raise StoreError("project not found")
        if (confirm_name or "").strip() != p["name"]:
            raise StoreError("confirmation name does not match project name")
        with self.con:
            self._rev("project", pid, "delete", {
                "name": p["name"],
                "counts": {k: self.con.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE project_id=?",
                    (pid,)).fetchone()[0]
                    for k, t in [("events", "events"),
                                 ("artifacts", "artifacts"),
                                 ("analyses", "analyses")]}})
            for t in ("timeline_entries", "notes", "relationships", "entities",
                      "canonical_cases", "analysis_artifacts"):
                if t == "analysis_artifacts":
                    self.con.execute(
                        "DELETE FROM analysis_artifacts WHERE analysis_id IN "
                        "(SELECT id FROM analyses WHERE project_id=?)", (pid,))
                else:
                    self.con.execute(
                        f"DELETE FROM {t} WHERE project_id=?", (pid,))
            for t in ("analyses", "artifacts", "events"):
                self.con.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
            self.con.execute("DELETE FROM projects WHERE id=?", (pid,))

    # ---- events ----
    def create_event(self, pid, name, summary="", t_claimed="",
                     time_status="unknown"):
        if time_status not in TIME_STATUSES:
            time_status = "unknown"
        eid = _nid("evt")
        pos = self.con.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM events WHERE project_id=?",
            (pid,)).fetchone()[0]
        with self.con:
            self.con.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (eid, pid, name.strip()[:200], summary.strip()[:2000],
                 t_claimed[:64], "", "", time_status, pos, _now(), _now()))
            self.con.execute("UPDATE projects SET updated=? WHERE id=?",
                             (_now(), pid))
            self._rev("event", eid, "create", {"project": pid, "name": name})
            self.con.execute(
                "INSERT INTO timeline_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_nid("tl"), pid, eid, None, None, name.strip()[:200],
                 t_claimed[:64], "", _now(), "", time_status, pos, _now()))
        return eid

    def list_events(self, pid):
        return [dict(r) for r in self.con.execute(
            """SELECT e.*,
               (SELECT COUNT(*) FROM artifacts a WHERE a.event_id=e.id) AS n_artifacts
               FROM events e WHERE project_id=? ORDER BY position""", (pid,))]

    # ---- artifacts (immutable after insert) ----
    def add_artifact(self, pid, event_id, *, label, text, evidence_type,
                     event_category="", workflow="general_evidence",
                     source="manual", claimed_sender="", claimed_org="",
                     requested_action="", media=None):
        aid = _nid("art")
        sha = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        with self.con:
            self.con.execute(
                "INSERT INTO artifacts VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, pid, event_id, label.strip()[:200], evidence_type,
                 event_category, workflow, text, sha, source,
                 claimed_sender[:200], claimed_org[:200],
                 requested_action[:400],
                 json.dumps(media, default=str) if media else "",
                 _now(), _now()))
            self.con.execute("UPDATE projects SET updated=? WHERE id=?",
                             (_now(), pid))
            self._rev("artifact", aid, "create",
                      {"project": pid, "event": event_id, "sha256": sha})
        return aid

    def list_artifacts(self, pid, event_id=None, evidence_type=None,
                       workflow=None):
        q = "SELECT * FROM artifacts WHERE project_id=?"
        args = [pid]
        if event_id:
            q += " AND event_id=?"; args.append(event_id)
        if evidence_type:
            q += " AND evidence_type=?"; args.append(evidence_type)
        if workflow:
            q += " AND workflow=?"; args.append(workflow)
        return [dict(r) for r in self.con.execute(q + " ORDER BY created",
                                                  args)]

    def get_artifact(self, aid):
        r = self.con.execute("SELECT * FROM artifacts WHERE id=?",
                             (aid,)).fetchone()
        return dict(r) if r else None

    # ---- analyses + graph merge ----
    def save_analysis(self, pid, *, kind, report, artifact_ids,
                      event_id=None, workflow="general_evidence",
                      model="", policy="", ledger_event_ids=None,
                      artifact_id_map=None):
        """Persist a report dict and merge its entities/relationships into
        the project graph with epistemic statuses + provenance.

        artifact_id_map translates the report's pipeline artifact ids to
        stored artifact ids so graph provenance points at persisted
        evidence (the analytical pipeline mints its own ids per run)."""
        xid = _nid("anl")
        report_id = report.get("report_id", "")
        with self.con:
            self.con.execute(
                "INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (xid, pid, event_id, kind, workflow, report_id,
                 json.dumps(report, default=str), model, policy,
                 json.dumps(ledger_event_ids or [], default=str), _now()))
            for aid in artifact_ids:
                self.con.execute(
                    "INSERT OR IGNORE INTO analysis_artifacts VALUES (?,?)",
                    (xid, aid))
            self.con.execute("UPDATE projects SET updated=? WHERE id=?",
                             (_now(), pid))
            self._rev("analysis", xid, "create",
                      {"project": pid, "kind": kind, "report_id": report_id})
            prov = {"report_id": report_id, "analysis_id": xid,
                    "workflow": workflow, "model": model, "policy": policy,
                    "supporting_artifact_ids": list(artifact_ids),
                    "ledger_event_ids": ledger_event_ids or []}
            self._merge_graph(pid, report, prov, artifact_id_map or {})
            self.con.execute(
                "INSERT INTO timeline_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_nid("tl"), pid, event_id, None, xid,
                 ("Fused analysis " if kind == "fused" else "Analysis ") +
                 (report_id or xid), "", "", _now(), "", "observed",
                 0, _now()))
        return xid

    def _merge_graph(self, pid, report, prov, idmap):
        # entities/relationships from FusedReport dicts; entity extraction is
        # deterministic; attribution/campaign edges are model-derived.
        def m(v):
            return idmap.get(v, v)

        def mlist(vs):
            return [idmap.get(v, v) for v in (vs or [])]
        for e in report.get("entities", []) or []:
            status = ("deterministic" if e.get("type") != "campaign"
                      else "deterministic")
            self.con.execute(
                """INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, entity_key) DO UPDATE SET
                   updated=excluded.updated""",
                (_nid("ent"), pid, m(e["id"]), e.get("type", "artifact"),
                 e.get("label", e["id"]), status,
                 json.dumps(prov, default=str), _now(), _now()))
        for r in report.get("relationships", []) or []:
            status = "deterministic"
            if r.get("relation") == "clusters":
                status = "deterministic"   # union-find clustering
            self.con.execute(
                """INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, source_key, target_key, relation)
                   DO UPDATE SET
                     artifact_ids_json=excluded.artifact_ids_json,
                     updated=excluded.updated""",
                (_nid("rel"), pid, m(r["source"]), m(r["target"]),
                 r.get("relation", "references"),
                 json.dumps(mlist(r.get("artifact_ids")), default=str),
                 status, json.dumps(prov, default=str), _now(), _now()))
        # attribution hypotheses become model_inferred edges from campaign
        # nodes when campaigns exist (bounded, provenance-carrying)
        for h in report.get("attribution", []) or []:
            for c in report.get("campaigns", []) or []:
                key = "hypothesis:" + hashlib.sha256(
                    (h.get("hypothesis", "") or "").encode()).hexdigest()[:10]
                self.con.execute(
                    """INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(project_id, entity_key) DO UPDATE SET
                       updated=excluded.updated""",
                    (_nid("ent"), pid, key, "hypothesis",
                     (h.get("campaign_type", "hypothesis") or "hypothesis")[:60],
                     "model_inferred",
                     json.dumps({**prov, "confidence": h.get("confidence")},
                                default=str), _now(), _now()))
                self.con.execute(
                    """INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(project_id, source_key, target_key, relation)
                       DO UPDATE SET updated=excluded.updated""",
                    (_nid("rel"), pid, key, c.get("campaign_id", ""),
                     "hypothesizes",
                     json.dumps(mlist(c.get("artifact_ids")), default=str),
                     "model_inferred",
                     json.dumps({**prov, "confidence": h.get("confidence")},
                                default=str), _now(), _now()))

    # ---- canonical cases (v0.3.0 media parity) ----
    def save_canonical_case(self, pid, *, case, package, assessment,
                            analysis_id=None, event_id=None):
        """Persist a Canonical Case and surface it in Atlas + Timeline.

        The case row is immutable evidence (like artifacts); analyst
        changes land in revisions. Atlas: the case becomes a
        `canonical_case` graph node linked to a `media` node keyed by the
        source sha256 — same graph contract as the shared renderer."""
        cid = case.get("case_id") or _nid("DFD")
        media = case.get("media") or {}
        sha = media.get("sha256") or ""
        with self.con:
            self.con.execute(
                "INSERT INTO canonical_cases VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, pid, event_id, analysis_id,
                 (media.get("label") or "")[:200],
                 media.get("family") or "",
                 sha, media.get("source_kind") or "upload",
                 case.get("assessment_conclusion", ""),
                 case.get("corroboration_status", "uncorroborated"),
                 json.dumps(case, default=str),
                 json.dumps(package or {}, default=str),
                 json.dumps(assessment or {}, default=str),
                 _now(), _now()))
            self._rev("canonical_case", cid, "create",
                      {"project": pid, "sha256": sha,
                       "conclusion": case.get("assessment_conclusion")})
            prov = {"canonical_case_id": cid, "analysis_id": analysis_id,
                    "workflow": "media_authenticity"}
            # Atlas nodes: the case + the media specimen (hash-keyed)
            self.con.execute(
                """INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, entity_key) DO UPDATE SET
                   updated=excluded.updated""",
                (_nid("ent"), pid, cid, "canonical_case",
                 cid, "deterministic", json.dumps(prov, default=str),
                 _now(), _now()))
            if sha:
                media_key = "media:" + sha[:16]
                self.con.execute(
                    """INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(project_id, entity_key) DO UPDATE SET
                       updated=excluded.updated""",
                    (_nid("ent"), pid, media_key, "media",
                     (media.get("label") or "media specimen")[:80],
                     "directly_observed",
                     json.dumps({**prov, "sha256": sha}, default=str),
                     _now(), _now()))
                self.con.execute(
                    """INSERT INTO relationships VALUES
                       (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(project_id, source_key, target_key,
                                   relation) DO UPDATE SET
                       updated=excluded.updated""",
                    (_nid("rel"), pid, cid, media_key,
                     "assesses", json.dumps([], default=str),
                     "deterministic", json.dumps(prov, default=str),
                     _now(), _now()))
            self.con.execute(
                "INSERT INTO timeline_entries VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_nid("tl"), pid, event_id, None, analysis_id,
                 f"Canonical Case {cid} · "
                 + (case.get("assessment_conclusion") or "")
                 .replace("_", " "),
                 "", "", _now(), "", "observed", 0, _now()))
            self.con.execute("UPDATE projects SET updated=? WHERE id=?",
                             (_now(), pid))
        return cid

    def list_canonical_cases(self, pid=None):
        q = ("SELECT id, project_id, event_id, analysis_id, media_label, "
             "media_family, media_sha256, source_kind, conclusion, "
             "corroboration_status, created FROM canonical_cases")
        args = []
        if pid:
            q += " WHERE project_id=?"; args.append(pid)
        return [dict(r) for r in self.con.execute(
            q + " ORDER BY created DESC", args)]

    def get_canonical_case(self, cid):
        r = self.con.execute("SELECT * FROM canonical_cases WHERE id=?",
                             (cid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["case"] = json.loads(d["case_json"])
        d["package"] = json.loads(d["evidence_package_json"])
        d["assessment"] = (json.loads(d["assessment_json"])
                           if d["assessment_json"] else None)
        return d

    def list_analyses(self, pid, event_id=None):
        q = "SELECT * FROM analyses WHERE project_id=?"
        args = [pid]
        if event_id:
            q += " AND event_id=?"; args.append(event_id)
        rows = [dict(r) for r in self.con.execute(
            q + " ORDER BY created DESC", args)]
        for r in rows:
            r["artifact_ids"] = [x[0] for x in self.con.execute(
                "SELECT artifact_id FROM analysis_artifacts WHERE analysis_id=?",
                (r["id"],))]
        return rows

    def get_analysis(self, xid):
        r = self.con.execute("SELECT * FROM analyses WHERE id=?",
                             (xid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["report"] = json.loads(d["report_json"])
        return d

    # ---- project graph (Atlas aggregation) ----
    def project_graph(self, pid, event_id=None, evidence_type=None,
                      workflow=None, epistemic_status=None):
        """Aggregated entities/relationships across all saved analyses,
        with optional filters. Filters by event/type/workflow narrow to
        relationships supported by matching artifacts."""
        allowed_artifacts = None
        if event_id or evidence_type or workflow:
            allowed_artifacts = {a["id"] for a in self.list_artifacts(
                pid, event_id=event_id, evidence_type=evidence_type,
                workflow=workflow)}
        ents, rels = [], []
        keep_keys = set()
        for r in self.con.execute(
                "SELECT * FROM relationships WHERE project_id=?", (pid,)):
            d = dict(r)
            if epistemic_status and d["epistemic_status"] != epistemic_status:
                continue
            if allowed_artifacts is not None:
                aids = set(json.loads(d["artifact_ids_json"] or "[]"))
                if aids and not (aids & allowed_artifacts):
                    continue
            rels.append({"source": d["source_key"],
                         "target": d["target_key"],
                         "relation": d["relation"],
                         "artifact_ids": json.loads(
                             d["artifact_ids_json"] or "[]"),
                         "epistemic_status": d["epistemic_status"],
                         "provenance": json.loads(
                             d["provenance_json"] or "{}")})
            keep_keys.add(d["source_key"]); keep_keys.add(d["target_key"])
        for e in self.con.execute(
                "SELECT * FROM entities WHERE project_id=?", (pid,)):
            d = dict(e)
            if keep_keys and d["entity_key"] not in keep_keys:
                if allowed_artifacts is not None or epistemic_status:
                    continue
            ents.append({"id": d["entity_key"], "type": d["type"],
                         "label": d["label"],
                         "epistemic_status": d["epistemic_status"],
                         "provenance": json.loads(
                             d["provenance_json"] or "{}")})
        return {"entities": ents, "relationships": rels}

    # ---- notes ----
    def add_note(self, pid, target_kind, target_id, body):
        nid = _nid("note")
        with self.con:
            self.con.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?)",
                             (nid, pid, target_kind, target_id,
                              body.strip()[:5000], _now(), _now()))
            self._rev("note", nid, "create",
                      {"target": f"{target_kind}:{target_id}"})
        return nid

    def list_notes(self, pid, target_kind=None, target_id=None):
        q = "SELECT * FROM notes WHERE project_id=?"
        args = [pid]
        if target_kind:
            q += " AND target_kind=?"; args.append(target_kind)
        if target_id:
            q += " AND target_id=?"; args.append(target_id)
        return [dict(r) for r in self.con.execute(q + " ORDER BY created",
                                                  args)]

    # ---- timeline ----
    def timeline(self, pid):
        return [dict(r) for r in self.con.execute(
            """SELECT * FROM timeline_entries WHERE project_id=?
               ORDER BY CASE WHEN t_claimed != '' THEN t_claimed ELSE
                 datetime(t_ingested, 'unixepoch') END, position""", (pid,))]

    def add_marker(self, pid, label, t_claimed="", time_status="unknown"):
        if time_status not in TIME_STATUSES:
            time_status = "unknown"
        tid = _nid("tl")
        with self.con:
            self.con.execute(
                "INSERT INTO timeline_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, pid, None, None, None, label.strip()[:200],
                 t_claimed[:64], "", _now(), "", time_status, 0, _now()))
            self._rev("timeline", tid, "create", {"label": label})
        return tid

    # ---- export / import ----
    PACKAGE_VERSION = 1

    def export_project(self, pid):
        p = self.get_project(pid)
        if not p:
            raise StoreError("project not found")
        def rows(table):
            return [dict(r) for r in self.con.execute(
                f"SELECT * FROM {table} WHERE project_id=?", (pid,))]
        pkg = {
            "package": "swarmtracker-project",
            "package_version": self.PACKAGE_VERSION,
            "exported_at": _now(),
            "project": p,
            "events": rows("events"),
            "artifacts": rows("artifacts"),
            "analyses": rows("analyses"),
            "analysis_artifacts": [dict(r) for r in self.con.execute(
                "SELECT aa.* FROM analysis_artifacts aa JOIN analyses a "
                "ON a.id=aa.analysis_id WHERE a.project_id=?", (pid,))],
            "entities": rows("entities"),
            "relationships": rows("relationships"),
            "notes": rows("notes"),
            "timeline_entries": rows("timeline_entries"),
            "canonical_cases": rows("canonical_cases"),
        }
        return pkg

    def import_project(self, pkg):
        if not isinstance(pkg, dict) or \
                pkg.get("package") != "swarmtracker-project":
            raise StoreError("not a swarmtracker project package")
        if pkg.get("package_version", 0) > self.PACKAGE_VERSION:
            raise StoreError("package from a newer version; upgrade Node")
        old_pid = pkg["project"]["id"]
        pid = _nid("proj")   # new id: import never overwrites
        idmap = {old_pid: pid}

        def remap(v):
            return idmap.get(v, v) if v else v
        with self.con:
            p = pkg["project"]
            self.con.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?,?)",
                (pid, "ws-default", (p["name"] + " (imported)")[:200],
                 p.get("description", ""), "active", _now(), _now()))
            for table, key in (("events", "evt"), ("artifacts", "art"),
                               ("analyses", "anl"), ("notes", "note"),
                               ("entities", "ent"), ("relationships", "rel"),
                               ("timeline_entries", "tl")):
                for row in pkg.get(table, []):
                    idmap[row["id"]] = _nid(key)
            for row in pkg.get("events", []):
                self.con.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, row["name"],
                     row.get("summary", ""), row.get("t_claimed", ""),
                     row.get("t_observed_start", ""),
                     row.get("t_observed_end", ""),
                     row.get("time_status", "unknown"),
                     row.get("position", 0), _now(), _now()))
            for row in pkg.get("artifacts", []):
                self.con.execute(
                    "INSERT INTO artifacts VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, remap(row.get("event_id")),
                     row["label"], row.get("evidence_type", "freeform_text"),
                     row.get("event_category", ""),
                     row.get("workflow", "general_evidence"),
                     row["text_content"], row["sha256"],
                     row.get("source", "import"),
                     row.get("claimed_sender", ""),
                     row.get("claimed_org", ""),
                     row.get("requested_action", ""),
                     row.get("media_json", ""),
                     row.get("t_ingested", _now()), _now()))
            for row in pkg.get("analyses", []):
                self.con.execute(
                    "INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, remap(row.get("event_id")),
                     row.get("kind", "single"),
                     row.get("workflow", "general_evidence"),
                     row.get("report_id", ""), row["report_json"],
                     row.get("model", ""), row.get("policy", ""),
                     row.get("ledger_event_ids_json", "[]"), _now()))
            for row in pkg.get("canonical_cases", []):
                # DFD ids are immutable/global: preserved on import. If
                # the same case already exists locally (re-import), the
                # record is kept under a suffixed id so the imported
                # project stays complete.
                case_id = row["id"]
                if self.con.execute(
                        "SELECT 1 FROM canonical_cases WHERE id=?",
                        (case_id,)).fetchone():
                    case_id = row["id"] + "-imp-" + uuid.uuid4().hex[:6]
                self.con.execute(
                    "INSERT INTO canonical_cases VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (case_id, pid, remap(row.get("event_id")),
                     idmap.get(row.get("analysis_id"),
                               row.get("analysis_id")),
                     row.get("media_label", ""),
                     row.get("media_family", ""),
                     row.get("media_sha256", ""),
                     row.get("source_kind", "upload"),
                     row.get("conclusion", ""),
                     row.get("corroboration_status", "uncorroborated"),
                     row.get("case_json", "{}"),
                     row.get("evidence_package_json", "{}"),
                     row.get("assessment_json", ""), _now(), _now()))
            for row in pkg.get("analysis_artifacts", []):
                self.con.execute(
                    "INSERT OR IGNORE INTO analysis_artifacts VALUES (?,?)",
                    (idmap.get(row["analysis_id"], row["analysis_id"]),
                     idmap.get(row["artifact_id"], row["artifact_id"])))
            for row in pkg.get("entities", []):
                self.con.execute(
                    "INSERT OR IGNORE INTO entities VALUES (?,?,?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, row["entity_key"], row["type"],
                     row["label"],
                     row.get("epistemic_status", "deterministic"),
                     row.get("provenance_json", "{}"), _now(), _now()))
            for row in pkg.get("relationships", []):
                self.con.execute(
                    "INSERT OR IGNORE INTO relationships VALUES "
                    "(?,?,?,?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, row["source_key"],
                     row["target_key"], row["relation"],
                     row.get("artifact_ids_json", "[]"),
                     row.get("epistemic_status", "deterministic"),
                     row.get("provenance_json", "{}"), _now(), _now()))
            for row in pkg.get("notes", []):
                self.con.execute(
                    "INSERT INTO notes VALUES (?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, row["target_kind"],
                     remap(row["target_id"]), row["body"], _now(), _now()))
            for row in pkg.get("timeline_entries", []):
                self.con.execute(
                    "INSERT INTO timeline_entries VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (idmap[row["id"]], pid, remap(row.get("event_id")),
                     remap(row.get("artifact_id")),
                     remap(row.get("analysis_id")), row["label"],
                     row.get("t_claimed", ""), row.get("t_observed", ""),
                     row.get("t_ingested"), row.get("t_range_end", ""),
                     row.get("time_status", "unknown"),
                     row.get("position", 0), _now()))
            self._rev("project", pid, "import",
                      {"source_project": old_pid,
                       "package_version": pkg.get("package_version")})
        return pid
