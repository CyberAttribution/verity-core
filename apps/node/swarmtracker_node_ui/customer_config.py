"""Operator-entered, immutable configuration versions and environment assertions.

These records describe expected access. They do not provision OS permissions,
authenticate machines, or grant a runtime any new tool capability.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def label(value, limit=160):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value) or len(value)>limit:
        raise ValueError("Invalid configuration identifier")
    return value


def _text(value, limit=1000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or "\x00" in value:
        raise ValueError("Invalid configuration text")
    return value.strip()


class CustomerConfig:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS versions (kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY(kind,id,version))")
            db.execute("CREATE TABLE IF NOT EXISTS observations (kind TEXT NOT NULL, id TEXT NOT NULL, at REAL NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL, PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS disabled_connections (id TEXT PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL)")
            columns={row[1] for row in db.execute("PRAGMA table_info(observations)")}
            if not {"config_version", "config_digest"} <= columns:
                db.execute("BEGIN IMMEDIATE")
                columns={row[1] for row in db.execute("PRAGMA table_info(observations)")}
                for name, sql_type in (("config_version", "INTEGER"), ("config_digest", "TEXT")):
                    if name not in columns:
                        db.execute(f"ALTER TABLE observations ADD COLUMN {name} {sql_type}")

    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        return db

    @contextmanager
    def dispatch_guard(self, agent_id, resource_id, expected):
        """Serialize routed inference with configuration writers across processes.

        A request holding this reservation has already started: removal waits
        for that bounded request, and does not cancel an in-flight call. This
        does not constrain direct runtime or same-user database access.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.connection_disabled(expected["connection_id"]):
                raise ValueError("Primary model connection disabled before dispatch")
            observation = self.observations().get(("connection", expected["connection_id"]))
            if expected.get("require_fresh_connection_observation", False) and not observation:
                raise ValueError("Fresh local connection observation required before dispatch")
            if observation and observation["state"] != "reachable":
                raise ValueError("Local connection observation changed before dispatch")
            if self.effective(agent_id, resource_id) != expected:
                raise ValueError("Effective configuration changed before dispatch")
            yield

    def save(self, kind, identifier, body, *, new_only=False):
        label(identifier)
        if kind=="connection" and self.connection_disabled(identifier):
            raise ValueError("Disabled connection IDs cannot be reused")
        if kind not in {"machine","connection","agent","resource","link"} or not isinstance(body,dict):
            raise ValueError("Unsupported configuration")
        if kind=="connection":
            if set(body)!={"provider","endpoint","model","revision","scope","credential_ref"}:
                raise ValueError("Invalid connection fields")
            for key in ("provider","model","scope"):_text(body[key],160)
            if body["scope"] not in {"local","external"}:raise ValueError("Invalid connection scope")
            endpoint=urllib.parse.urlsplit(body["endpoint"])
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ("", "/"):
                raise ValueError("Endpoint cannot contain credentials or path")
            if body["scope"]=="local":
                from .config import assert_local
                assert_local(body["endpoint"])
            elif endpoint.scheme!="https" or not endpoint.hostname or endpoint.hostname in {"localhost","127.0.0.1"}:
                raise ValueError("External model endpoint must be HTTPS")
            if not isinstance(body["revision"],str) or len(body["revision"])>160:raise ValueError("Invalid revision")
            # A reference is descriptive only; this workflow does not accept or
            # export credentials, or silently dispatch to external endpoints.
            if body["credential_ref"] not in ("none", "separately-managed"):
                raise ValueError("Secrets cannot be entered in configuration")
        elif kind=="agent":
            required={"connection_id","instructions","allowed_tools","resources","external_sharing","fallback_id"}
            if not required <= set(body) or set(body)-required-{"daily_request_limit","session_request_limit","adapter_kind","runtime_version","service_identity","require_fresh_connection_observation"}:
                raise ValueError("Invalid agent fields")
            if "daily_request_limit" in body and (type(body["daily_request_limit"]) is not int or not 0<=body["daily_request_limit"]<=10000):
                raise ValueError("Daily request limit must be 0 to 10000; zero is unlimited")
            if "session_request_limit" in body and (type(body["session_request_limit"]) is not int or not 0<=body["session_request_limit"]<=10000):
                raise ValueError("Session request limit must be 0 to 10000; zero is unlimited")
            if "require_fresh_connection_observation" in body and type(body["require_fresh_connection_observation"]) is not bool:
                raise ValueError("Fresh connection observation requirement must be boolean")
            if "adapter_kind" in body:
                if body["adapter_kind"] not in {"openclaw_v0", "custom_unverified"}:
                    raise ValueError("Unsupported agent adapter")
            for key in ("runtime_version", "service_identity"):
                if key in body and (not isinstance(body[key], str) or len(body[key]) > 160 or "\x00" in body[key]):
                    raise ValueError("Invalid declared runtime identity")
            label(body["connection_id"])
            _text(body["instructions"],2000)
            if body["fallback_id"]:label(body["fallback_id"])
            if not isinstance(body["allowed_tools"],list) or any(x not in {"generate_text","draft_text"} for x in body["allowed_tools"]) or len(set(body["allowed_tools"]))!=len(body["allowed_tools"]):
                raise ValueError("Only descriptive read-only tools supported")
            if not isinstance(body["resources"],list) or any(not isinstance(x,str) for x in body["resources"]):raise ValueError("Invalid resources")
            for x in body["resources"]:label(x)
            if body["external_sharing"] not in {"none","explicit_per_job"}:raise ValueError("Invalid sharing rule")
            if not self.latest("connection",body["connection_id"]):raise ValueError("Unknown primary connection")
            if body.get("adapter_kind") == "openclaw_v0":
                primary = self.latest("connection", body["connection_id"])["body"]
                if primary["scope"] != "local" or primary["provider"] != "openclaw":
                    raise ValueError("OpenClaw v0 adapter requires a local OpenClaw connection")
            if body["fallback_id"] and not self.latest("connection",body["fallback_id"]):raise ValueError("Unknown fallback")
            for x in body["resources"]:
                if not self.latest("resource",x):raise ValueError("Unknown resource")
        elif kind=="link":
            if set(body)!={"from_kind","from_id","to_kind","to_id","permission","provenance","status"}:
                raise ValueError("Invalid environment relationship")
            if (body["from_kind"],body["to_kind"],body["permission"]) not in {
                    ("machine","connection","hosts"),("agent","resource","proposes"),
                    ("machine","resource","executor-host")}:
                raise ValueError("Unsupported relationship")
            for key in ("from_id","to_id"):label(body[key])
            if not self.latest(body["from_kind"],body["from_id"]) or not self.latest(body["to_kind"],body["to_id"]):
                raise ValueError("Unknown relationship endpoint")
            if body["provenance"]!="operator-entered" or body["status"]!="unknown":
                raise ValueError("New relationship is unconfirmed")
        else:
            if set(body)!={"description","provenance","status"}:raise ValueError("Invalid environment fields")
            _text(body["description"],500)
            if body["provenance"] not in {"operator-entered","observed","confirmed"} or body["status"] not in {"unknown","current","stale"}:
                raise ValueError("Invalid provenance or status")
        raw=canonical(body)
        if re.search(r"(?i)(bearer\s+|api[_-]?key\s*[:=]|password\s*[:=]|secret\s*[:=])",raw):
            raise ValueError("Credentials cannot be stored in ordinary configuration")
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if new_only and db.execute("SELECT 1 FROM versions WHERE kind=? AND id=?", (kind,identifier)).fetchone():
                raise ValueError("Configuration identifier already exists")
            if kind == "agent":
                previous = db.execute("SELECT body FROM versions WHERE kind='agent' AND id=? ORDER BY version DESC LIMIT 1", (identifier,)).fetchone()
                if previous:
                    declared = json.loads(previous["body"]).get("adapter_kind")
                    if declared and body.get("adapter_kind") != declared:
                        raise ValueError("Declared adapter cannot be removed or changed; use a new agent ID")
            if kind == "connection" and db.execute("SELECT 1 FROM disabled_connections WHERE id=?", (identifier,)).fetchone():
                raise ValueError("Disabled connection IDs cannot be reused")
            row=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM versions WHERE kind=? AND id=?",(kind,identifier)).fetchone()
            version=row[0]
            db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?)",(kind,identifier,version,digest,raw,time.time()))
            if kind == "connection":
                # Reachability of the previous endpoint/version is not evidence
                # about the newly saved connection, even when the ID is reused.
                db.execute("DELETE FROM observations WHERE kind=? AND id=?", (kind,identifier))
        return self.latest(kind,identifier)

    def disable_connection(self, identifier, reason="Operator removed model connection"):
        label(identifier)
        _text(reason, 300)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM versions WHERE kind='connection' AND id=?", (identifier,)).fetchone():
                raise ValueError("Unknown connection")
            db.execute("INSERT OR IGNORE INTO disabled_connections VALUES (?,?,?)", (identifier, time.time(), reason))
        return self.connection_disabled(identifier)

    def connection_disabled(self, identifier):
        with self._db() as db:
            row = db.execute("SELECT at,reason FROM disabled_connections WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def latest(self,kind,identifier):
        with self._db() as db:
            row=db.execute("SELECT * FROM versions WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1",(kind,identifier)).fetchone()
            return dict(row,body=json.loads(row["body"])) if row else None

    def all(self):
        with self._db() as db:
            return [dict(row,body=json.loads(row["body"])) for row in db.execute("SELECT v.* FROM versions v JOIN (SELECT kind,id,MAX(version) version FROM versions GROUP BY kind,id) x ON (v.kind,v.id,v.version)=(x.kind,x.id,x.version) ORDER BY v.kind,v.id")]

    def observe(self,kind,identifier,state,detail, *, expected_digest=None, expected_version=None):
        if not self.latest(kind,identifier) or state not in {"reachable","unavailable","unknown"}:
            raise ValueError("Invalid observation")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT digest,version FROM versions WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1", (kind,identifier)).fetchone()
            if (not latest or (expected_digest is not None and latest["digest"] != expected_digest)
                    or (expected_version is not None and latest["version"] != expected_version)):
                raise ValueError("Configuration changed during health observation; recheck current connection")
            db.execute("INSERT OR REPLACE INTO observations (kind,id,at,state,detail,config_version,config_digest) VALUES (?,?,?,?,?,?,?)",
                       (kind,identifier,time.time(),state,_text(detail,300),latest["version"],latest["digest"]))

    def observations(self):
        with self._db() as db:
            result={(r["kind"],r["id"]):dict(r) for r in db.execute("""SELECT o.*, v.version AS current_version, v.digest AS current_digest
                FROM observations o LEFT JOIN versions v ON v.kind=o.kind AND v.id=o.id
                AND v.version=(SELECT MAX(x.version) FROM versions x WHERE x.kind=o.kind AND x.id=o.id)""")}
        for item in result.values():
            if item["kind"] == "connection" and (item["config_version"] is None
                    or item["config_version"] != item["current_version"] or item["config_digest"] != item["current_digest"]):
                item["state"]="unknown"
                item["detail"]="Health observation lacks the current connection version binding; recheck"
            elif item["state"]=="reachable" and time.time()-item["at"]>300:
                item["state"]="stale"
                item["detail"]="Last health observation is stale; current reachability unknown"
        return result

    def effective(self,agent_id,resource_id):
        agent=self.latest("agent",agent_id)
        if not agent or resource_id not in agent["body"]["resources"]:raise ValueError("Agent resource not configured")
        resource=self.latest("resource",resource_id)
        conn=self.latest("connection",agent["body"]["connection_id"])
        fallback=self.latest("connection",agent["body"]["fallback_id"]) if agent["body"]["fallback_id"] else None
        hosted=[x for x in self.all() if x["kind"]=="link" and x["body"]["to_kind"]=="connection"
                and x["body"]["to_id"]==conn["id"] and x["body"]["permission"]=="hosts"]
        machine=self.latest("machine",hosted[0]["body"]["from_id"]) if len(hosted)==1 else None
        if not resource or not conn:raise ValueError("Incomplete effective configuration")
        result={"agent_id":agent_id,"agent_version":agent["version"],"agent_hash":agent["digest"],
                "connection_id":conn["id"],"connection_version":conn["version"],"connection_hash":conn["digest"],
                "model":conn["body"]["model"],"model_revision":conn["body"]["revision"] or "unknown",
                "provider":conn["body"]["provider"],"endpoint":conn["body"]["endpoint"],
                "instructions_sha256":hashlib.sha256(agent["body"]["instructions"].encode()).hexdigest(),
                "instructions":agent["body"]["instructions"],
                "allowed_tools":agent["body"]["allowed_tools"],"external_sharing":agent["body"]["external_sharing"],
                "resources":agent["body"]["resources"],
                "fallback_id":fallback["id"] if fallback else None,
                "fallback_version":fallback["version"] if fallback else None,
                "fallback_model":fallback["body"]["model"] if fallback else None,
                "fallback_revision":fallback["body"]["revision"] or "unknown" if fallback else None,
                "machine_id":machine["id"] if machine else None,
                "machine_version":machine["version"] if machine else None,
                "machine_hash":machine["digest"] if machine else None,
                "executor_id":"verity-report-broker",
                "resource_id":resource_id,"resource_version":resource["version"],"resource_hash":resource["digest"],
                "provenance":"operator-entered; capabilities and host identity unconfirmed"}
        if "adapter_kind" in agent["body"]:
            result.update(adapter_kind=agent["body"]["adapter_kind"],
                          identity_status="unverified; operator-declared",
                          runtime_version=agent["body"].get("runtime_version", ""),
                          service_identity=agent["body"].get("service_identity", ""))
        for key in ("daily_request_limit","session_request_limit","require_fresh_connection_observation"):
            if key in agent["body"]:
                result[key]=agent["body"][key]
        return result
