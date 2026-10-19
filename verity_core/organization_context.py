"""Disposable local organization-context lifecycle prototype.

Uses existing SQLite and Trust Record patterns. Local review labels are not
authenticated identities. This module grants no actions and is not connected
to evidence_check.context_ref imports or Atlas policy enforcement.
"""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from contextlib import contextmanager

from .json_safety import bounded_json_loads
from .trust import record as trust
from .trust import evidence_check as evidence

SCHEMA = "organization_context_history/v0.1"
IDENTITY = "local operator intent; unauthenticated"
MAX_EVENTS = 100
MAX_HISTORY_BYTES = 2 * 1024 * 1024


class Conflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", value):
        raise ValueError("Invalid context identifier")
    return value


def validate_body(body):
    evidence.fields(body, "title entries")
    evidence.text(body["title"], 120)
    entries = body["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError("One to twenty bounded context entries required")
    seen = set()
    for entry in entries:
        evidence.fields(entry, "id kind status subject value source_ref source_sha256 checked_at review_before qualifications")
        identifier(entry["id"])
        if entry["id"] in seen:
            raise ValueError("Duplicate context entry")
        seen.add(entry["id"])
        if entry["kind"] not in ("person", "vendor", "domain", "system", "relationship", "policy"):
            raise ValueError("Unknown context entry kind")
        if entry["status"] not in ("fact", "assumption", "proposal"):
            raise ValueError("Unknown context assertion status")
        for key, limit in (("subject", 120), ("value", 500), ("source_ref", 1000)):
            evidence.text(entry[key], limit)
        if not trust._valid_sha256(entry["source_sha256"]):
            raise ValueError("Context source digest required")
        for key in ("checked_at", "review_before"):
            evidence.timestamp(entry[key])
        if entry["review_before"] <= entry["checked_at"]:
            raise ValueError("Context review interval must be positive")
        flags = entry["qualifications"]
        if (not isinstance(flags, list) or len(flags) > 2
                or any(x not in ("missing", "conflicting") for x in flags)
                or len(set(flags)) != len(flags)):
            raise ValueError("Invalid context qualifications")
    if len(trust.canonical_json(body).encode("utf-8")) > 16384:
        raise ValueError("Context body exceeds byte allowance")
    return body


def _digest(proposal):
    body = {k: proposal[k] for k in ("profile_id", "version", "parent_generation", "parent_digest", "body")}
    return hashlib.sha256(trust.canonical_json(body).encode("utf-8")).hexdigest()


def _active_digest(state):
    return state["versions"][state["active_version"]]["sha256"] if state["active_version"] is not None else None


def inspect_record(record):
    """Replay semantic state; a valid chain alone cannot confer context approval."""
    if not trust.chain_valid(record) or len(record["entries"]) > MAX_EVENTS:
        raise ValueError("Invalid bounded context history")
    evidence.fields(record["subject"], "kind identifier")
    profile_id = identifier(record["subject"]["identifier"])
    if record["subject"]["kind"] != "organization_context" or record["refs"] != {}:
        raise ValueError("Unexpected context record subject")
    state = dict(profile_id=profile_id, generation=0, active_version=None, versions={})
    for entry in record["entries"]:
        payload = entry["payload"]
        if not isinstance(payload, dict) or payload.get("profile_id") != profile_id:
            raise ValueError("Context event belongs to another profile")
        event = payload.get("event")
        if event == "context_proposed":
            evidence.fields(payload, "event profile_id proposal origin")
            if entry["entry_type"] != "note" or payload["origin"] not in ("operator", "model", "import"):
                raise ValueError("Invalid context proposal origin")
            proposal = payload["proposal"]
            evidence.fields(proposal, "profile_id version parent_generation parent_digest body sha256")
            version = proposal["version"]
            if (proposal["profile_id"] != profile_id or type(version) is not int
                    or version != len(state["versions"]) + 1
                    or type(proposal["parent_generation"]) is not int
                    or proposal["parent_generation"] != state["generation"]
                    or proposal["parent_digest"] != _active_digest(state)):
                raise ValueError("Invalid context proposal binding")
            validate_body(proposal["body"])
            if proposal["sha256"] != _digest(proposal):
                raise ValueError("Context proposal digest mismatch")
            state["versions"][version] = dict(proposal, decision="proposed",
                origin=payload["origin"], approval_event_hash=None)
            continue
        evidence.text(entry["actor"], 120)
        if payload.get("identity_provenance") != IDENTITY:
            raise ValueError("Context history does not authenticate operator labels")
        evidence.text(payload.get("reason"), 500)
        if event in ("context_approved", "context_rejected"):
            expected = "event profile_id version sha256 reason identity_provenance"
            if event == "context_approved":
                expected += " expected_generation expected_parent_digest"
            evidence.fields(payload, expected)
            version = payload["version"]
            proposal = state["versions"].get(version) if type(version) is int else None
            if not proposal or proposal["decision"] != "proposed" or payload["sha256"] != proposal["sha256"]:
                raise ValueError("Context decision lacks an exact undecided proposal")
            if event == "context_approved":
                if (entry["entry_type"] != "approval_granted"
                        or type(payload["expected_generation"]) is not int
                        or payload["expected_generation"] != state["generation"]
                        or proposal["parent_generation"] != state["generation"]
                        or payload["expected_parent_digest"] != _active_digest(state)
                        or proposal["parent_digest"] != _active_digest(state)):
                    raise ValueError("Stale context approval")
                state["generation"] += 1
                state["active_version"] = version
                proposal["decision"] = "approved"
                proposal["approval_event_hash"] = entry["entry_hash"]
            else:
                if entry["entry_type"] != "note":
                    raise ValueError("Invalid context rejection")
                proposal["decision"] = "rejected"
        elif event == "context_withdrawn":
            evidence.fields(payload, "event profile_id expected_generation expected_digest reason identity_provenance")
            if (entry["entry_type"] != "note" or state["active_version"] is None
                    or type(payload["expected_generation"]) is not int
                    or payload["expected_generation"] != state["generation"]
                    or payload["expected_digest"] != _active_digest(state)):
                raise ValueError("Stale context withdrawal")
            state["generation"] += 1
            state["active_version"] = None
        else:
            raise ValueError("Unknown context event")
    return state


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate context JSON field")
        result[key] = value
    return result


class ContextStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Context store must be an owner-only regular file")
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS context_profiles (id TEXT PRIMARY KEY, record TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        try:
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _load(self, con, profile_id, create=False):
        identifier(profile_id)
        row = con.execute("SELECT record FROM context_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            if not create:
                raise Conflict("Unknown context profile")
            record = trust.new_record(dict(kind="organization_context", identifier=profile_id))
        else:
            if len(row[0].encode("utf-8")) > MAX_HISTORY_BYTES:
                raise ValueError("Context history exceeds byte allowance")
            record = bounded_json_loads(row[0], max_depth=32, object_pairs_hook=_unique)
        state = inspect_record(record)
        if state["profile_id"] != profile_id:
            raise ValueError("Stored context profile binding mismatch")
        return record, state

    def _save(self, con, record):
        state = inspect_record(record)
        raw = trust.canonical_json(record)
        if len(raw.encode("utf-8")) > MAX_HISTORY_BYTES:
            raise ValueError("Context history exceeds byte allowance")
        con.execute("INSERT INTO context_profiles VALUES (?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                    (state["profile_id"], raw))
        return state

    def propose(self, profile_id, body, *, origin="operator"):
        validate_body(body)
        if origin not in ("operator", "model", "import"):
            raise ValueError("Invalid context proposal origin")
        with self.connection() as con:
            record, state = self._load(con, profile_id, create=True)
            proposal = dict(profile_id=profile_id, version=len(state["versions"]) + 1,
                parent_generation=state["generation"], parent_digest=_active_digest(state), body=body)
            proposal["sha256"] = _digest(proposal)
            trust.append_entry(record, "note", payload=dict(event="context_proposed",
                profile_id=profile_id, proposal=proposal, origin=origin))
            state = self._save(con, record)
            return copy.deepcopy(state["versions"][proposal["version"]])

    def _decide(self, profile_id, version, expected_digest, actor, reason, *,
                approve, expected_generation=None, expected_parent_digest=None):
        evidence.text(actor, 120)
        evidence.text(reason, 500)
        with self.connection() as con:
            record, state = self._load(con, profile_id)
            proposal = state["versions"].get(version) if type(version) is int else None
            if not proposal or proposal["decision"] != "proposed" or proposal["sha256"] != expected_digest:
                raise Conflict("Exact undecided context proposal required")
            if approve and (type(expected_generation) is not int
                    or expected_generation != state["generation"]
                    or proposal["parent_generation"] != state["generation"]
                    or expected_parent_digest != _active_digest(state)
                    or proposal["parent_digest"] != _active_digest(state)):
                raise Conflict("Context parent changed; review a new proposal")
            payload = dict(event="context_approved" if approve else "context_rejected",
                profile_id=profile_id, version=version, sha256=expected_digest,
                reason=reason, identity_provenance=IDENTITY)
            if approve:
                payload.update(expected_generation=expected_generation,
                               expected_parent_digest=expected_parent_digest)
            trust.append_entry(record, "approval_granted" if approve else "note", actor=actor, payload=payload)
            state = self._save(con, record)
            return copy.deepcopy(state["versions"][version])

    def approve(self, profile_id, version, expected_digest, expected_generation,
                expected_parent_digest, *, actor, reason):
        return self._decide(profile_id, version, expected_digest, actor, reason,
            approve=True, expected_generation=expected_generation,
            expected_parent_digest=expected_parent_digest)

    def reject(self, profile_id, version, expected_digest, *, actor, reason):
        return self._decide(profile_id, version, expected_digest, actor, reason, approve=False)

    def withdraw(self, profile_id, expected_generation, expected_digest, *, actor, reason):
        evidence.text(actor, 120)
        evidence.text(reason, 500)
        with self.connection() as con:
            record, state = self._load(con, profile_id)
            if (state["active_version"] is None or type(expected_generation) is not int
                    or expected_generation != state["generation"] or expected_digest != _active_digest(state)):
                raise Conflict("Exact current context approval required")
            trust.append_entry(record, "note", actor=actor, payload=dict(event="context_withdrawn",
                profile_id=profile_id, expected_generation=expected_generation,
                expected_digest=expected_digest, reason=reason, identity_provenance=IDENTITY))
            state = self._save(con, record)
            return dict(profile_id=profile_id, generation=state["generation"], active_version=None)

    def active_snapshot(self, profile_id, *, now=None, required_entries=()):
        if not isinstance(required_entries, (list, tuple)) or len(required_entries) > 20:
            raise ValueError("Bounded required context identifiers expected")
        for item in required_entries:
            identifier(item)
        current = time.time() if now is None else now
        if type(current) not in (int, float) or not 0 <= current < 4102444800:
            raise ValueError("Invalid context observation time")
        with self.connection() as con:
            _, state = self._load(con, profile_id)
            if state["active_version"] is None:
                raise Conflict("No active locally approved context; no implicit fallback")
            proposal = copy.deepcopy(state["versions"][state["active_version"]])
        flags = []
        entries = {item["id"]: item for item in proposal["body"]["entries"]}
        for entry in entries.values():
            qualifications = list(entry["qualifications"])
            deadline = datetime.datetime.strptime(entry["review_before"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
            if current >= deadline:
                qualifications.append("stale")
            if entry["status"] != "fact":
                qualifications.append(entry["status"])
            if qualifications:
                flags.append(dict(id=entry["id"], qualifications=qualifications))
        flags.extend(dict(id=item, qualifications=["missing"]) for item in required_entries if item not in entries)
        return dict(profile_id=profile_id, version=proposal["version"], sha256=proposal["sha256"],
            generation=state["generation"], approval_event_hash=proposal["approval_event_hash"],
            body=proposal["body"], qualifications=flags, unresolved=bool(flags),
            identity_provenance=IDENTITY,
            limitations="Local review and source hashes do not prove factual truth or authorize actions.")

    def export_history(self, profile_id):
        with self.connection() as con:
            record, _ = self._load(con, profile_id)
        anchor = dict(record_id=record["record_id"], entry_count=len(record["entries"]),
                      head_hash=record["entries"][-1]["entry_hash"])
        return dict(schema=SCHEMA, record=record, checkpoint=anchor)


def verify_history(bundle, retained_checkpoint):
    """Reconstruct recorded local lifecycle; not authenticated approval or truth."""
    try:
        evidence.fields(bundle, "schema record checkpoint")
        evidence.fields(retained_checkpoint, "record_id entry_count head_hash")
        if bundle["schema"] != SCHEMA:
            return False
        record = bundle["record"]
        inspect_record(record)
        actual = dict(record_id=record["record_id"], entry_count=len(record["entries"]),
                      head_hash=record["entries"][-1]["entry_hash"])
        return (type(retained_checkpoint["entry_count"]) is int
                and actual == bundle["checkpoint"] == retained_checkpoint)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        return False
