"""Fixed-target governed report action.

The OpenClaw v0 inference endpoint has no file or shell tools. A separate,
operator-owned Verity process receives its text proposal. The action is bound to
one fixed output name; reservation is durable before the external file effect.
A crash after reservation leaves the action in doubt and blocks retries.
"""
import hashlib
import os
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from verity_core.json_safety import bounded_json_loads
from verity_core.trust import record as trust

SCHEMA = "governed_report_write/v1"
TARGET = "summary.md"
POLICY = {"id": "isolated-report-summary-write", "version": "1",
          "mode": "require_approval", "target": TARGET}
MAX_OUTPUT = 12000


class Conflict(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _label(value, name, limit=240):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Invalid " + name)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Control character in " + name)
    return value


def validate(manifest):
    keys = {"schema", "agent_id", "task_id", "model", "provider", "source_name",
            "source_sha256", "runtime_event_id", "proposal", "action", "policy"}
    if not isinstance(manifest, dict) or not keys <= set(manifest) or set(manifest) - keys - {"activity_run_id", "response_sha256", "effective_configuration"} or manifest["schema"] != SCHEMA:
        raise ValueError("Invalid report proposal")
    for k in ("agent_id", "task_id", "model", "provider", "source_name", "runtime_event_id"):
        _label(manifest[k], k)
    if not isinstance(manifest["source_sha256"], str) or len(manifest["source_sha256"]) != 64:
        raise ValueError("Invalid source digest")
    int(manifest["source_sha256"], 16)
    if "activity_run_id" in manifest:
        _label(manifest["activity_run_id"], "activity run ID", 120)
    if "response_sha256" in manifest:
        value = manifest["response_sha256"]
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("Invalid runtime response digest")
        int(value, 16)
    if "effective_configuration" in manifest:
        cfg=manifest["effective_configuration"]
        required={"agent_id","agent_version","agent_hash","connection_id","connection_version","connection_hash",
                  "model","model_revision","provider","endpoint","instructions_sha256","allowed_tools",
                  "external_sharing","fallback_id","fallback_version","resource_id","resource_version",
                  "resource_hash","provenance","machine_id","machine_version","machine_hash","executor_id"}
        expanded={"instructions","resources","fallback_model","fallback_revision"}
        if not isinstance(cfg,dict) or set(cfg) not in (required,required|expanded) or cfg["agent_id"]!=manifest["agent_id"] or cfg["model"]!=manifest["model"] or cfg["provider"]!=manifest["provider"] or cfg["resource_id"]!=TARGET or cfg["executor_id"]!="verity-report-broker":
            raise ValueError("Action configuration mismatch")
        if any(type(cfg[k]) is not int or cfg[k]<1 for k in ("agent_version","connection_version","resource_version")):
            raise ValueError("Invalid configuration version")
        for k in ("agent_hash","connection_hash","resource_hash","instructions_sha256"):
            if not isinstance(cfg[k],str) or len(cfg[k])!=64:raise ValueError("Invalid configuration digest")
            int(cfg[k],16)
        if expanded <= set(cfg):
            if not isinstance(cfg["instructions"],str) or digest(cfg["instructions"].encode())!=cfg["instructions_sha256"] or not isinstance(cfg["resources"],list) or TARGET not in cfg["resources"]:
                raise ValueError("Invalid effective instructions or resources")
        if cfg["machine_id"] is not None:
            if not isinstance(cfg["machine_id"],str) or type(cfg["machine_version"]) is not int or not isinstance(cfg["machine_hash"],str) or len(cfg["machine_hash"])!=64:
                raise ValueError("Invalid machine linkage")
        if not isinstance(cfg["allowed_tools"],list) or any(x not in ("generate_text","draft_text") for x in cfg["allowed_tools"]):
            raise ValueError("Unsupported tool assertion")
    proposal = manifest["proposal"]
    if not isinstance(proposal, str) or not proposal.strip() or len(proposal.encode()) > MAX_OUTPUT:
        raise ValueError("Invalid proposed output")
    if "\x00" in proposal:
        raise ValueError("NUL in output")
    action = {"kind": "write_sanitized_summary", "target": TARGET,
              "sha256": digest(proposal.encode())}
    if manifest["action"] != action or manifest["policy"] != POLICY:
        raise ValueError("Unsupported action or policy")
    return manifest


def binding(manifest, record_id):
    return digest(trust.canonical_json({"record_id": record_id, "manifest": manifest}).encode())


def new(manifest):
    validate(manifest)
    record = trust.new_record({"kind": "governed_report_write", "identifier": manifest["task_id"]})
    trust.append_entry(record, "note", actor=manifest["agent_id"],
                       payload={"event": "proposed", "manifest": manifest})
    return record


def inspect(record):
    if not trust.chain_valid(record) or not record["entries"]:
        raise ValueError("Report action integrity failure")
    first = record["entries"][0]
    if first["entry_type"] != "note" or first["payload"].get("event") != "proposed":
        raise ValueError("Missing proposal")
    m = validate(first["payload"]["manifest"])
    if record["subject"] != {"kind": "governed_report_write", "identifier": m["task_id"]}:
        raise ValueError("Proposal identity mismatch")
    state, decision, attempts, reserved = "proposed", "pending", 0, None
    approval_expires_at = None
    for entry in record["entries"][1:]:
        p = entry["payload"]
        event = p.get("event")
        if event == "decision":
            if decision != "pending" or reserved is not None or attempts:
                raise ValueError("Invalid decision order")
            choice = p.get("choice")
            if choice not in ("approved", "rejected") or p.get("binding") != binding(m, record["record_id"]):
                raise ValueError("Decision binding mismatch")
            _label(p.get("reason"), "reason", 1000)
            if p.get("identity_provenance") != "local operator intent; unauthenticated":
                raise ValueError("Invalid actor provenance")
            if (entry["entry_type"] == "approval_granted") != (choice == "approved"):
                raise ValueError("Decision entry mismatch")
            expiry=p.get("expires_at")
            if expiry is not None and (choice!="approved" or type(expiry) not in (int,float) or expiry<=0):
                raise ValueError("Invalid approval expiry")
            approval_expires_at=expiry
            decision = choice
            state = choice
        elif event == "revocation":
            if decision != "approved" or reserved is not None or attempts or entry["entry_type"] != "note":
                raise ValueError("Invalid approval revocation order")
            if p.get("binding") != binding(m, record["record_id"]) or p.get("identity_provenance") != "local operator intent; unauthenticated":
                raise ValueError("Revocation binding mismatch")
            _label(p.get("reason"), "reason", 1000)
            if set(p) != {"event", "binding", "reason", "identity_provenance"}:
                raise ValueError("Unexpected revocation fields")
            decision = "revoked"
            state = "revoked"
        elif event == "attempt":
            if entry["entry_type"] != "action_attempted" or reserved is not None or attempts:
                raise ValueError("Invalid attempt")
            allowed = decision == "approved"
            if p != {"event": "attempt", "allowed": allowed,
                      "binding": binding(m, record["record_id"])}:
                raise ValueError("Attempt binding mismatch")
            attempts += 1
            reserved = entry["entry_hash"]
            state = "in_doubt" if allowed else "denied"
        elif event == "result":
            if entry["entry_type"] != "action_result" or reserved is None or p.get("attempt_hash") != reserved:
                raise ValueError("Result without reservation")
            if p.get("outcome") == "written":
                if decision != "approved" or p != {"event": "result", "attempt_hash": reserved,
                    "outcome": "written", "sha256": m["action"]["sha256"],
                    "external_action_executed": True}:
                    raise ValueError("Invalid written result")
                state = "written"
            elif p == {"event": "result", "attempt_hash": reserved,
                        "outcome": "denied", "external_action_executed": False}:
                if decision == "approved":
                    raise ValueError("Approved action incorrectly denied")
                state = "denied"
            else:
                raise ValueError("Invalid result")
            reserved = None
        else:
            raise ValueError("Unknown report action event")
    if attempts and state == "denied" and reserved is not None:
        raise ValueError("Denied action missing result")
    if state=="approved" and approval_expires_at is not None and time.time()>approval_expires_at:
        state="expired"
    return {"id": record["record_id"], "manifest": m, "decision": decision,
            "state": state, "attempts": attempts, "approval_expires_at": approval_expires_at,
            "can_execute": state == "approved" and not attempts,
            "record": record, "head_hash": record["entries"][-1]["entry_hash"],
            "entry_count": len(record["entries"])}


def checkpoint(record):
    view = inspect(record)
    return {"schema": SCHEMA, "record_id": view["id"],
            "head_hash": view["head_hash"], "entry_count": view["entry_count"],
            "record_sha256": digest(trust.canonical_json(record).encode())}


def export(record):
    return {"schema": SCHEMA, "record": record, "checkpoint": checkpoint(record),
            "limits": "Fixed output only; operator identity is not authenticated. A reserved action without a result is in doubt and cannot be retried."}


def verify(bundle, anchor=None):
    try:
        cp = checkpoint(bundle["record"])
        return bundle["schema"] == SCHEMA and bundle["checkpoint"] == cp and (anchor is None or anchor == cp)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


class Store:
    def __init__(self, db_path, output_dir, config_store=None):
        self.db_path = Path(db_path)
        self.output_dir = Path(output_dir)
        self.config_store = config_store
        if not self.output_dir.is_absolute() or self.output_dir.is_symlink():
            raise ValueError("Absolute non-symlink output directory required")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS report_actions (id TEXT PRIMARY KEY, record TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        try:
            con.execute("PRAGMA synchronous=FULL")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _get(self, con, rid):
        row = con.execute("SELECT record FROM report_actions WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        record = bounded_json_loads(row[0])
        view = inspect(record)
        if view["id"] != rid:
            raise ValueError("Record ID mismatch")
        return record, view

    def _save(self, con, record):
        inspect(record)
        con.execute("UPDATE report_actions SET record=? WHERE id=?",
                    (trust.canonical_json(record), record["record_id"]))

    def create(self, manifest):
        if self.config_store is not None and "effective_configuration" in manifest:
            cfg=manifest["effective_configuration"]
            if cfg != self.config_store.effective(manifest["agent_id"],TARGET):
                raise ValueError("Effective configuration changed before proposal was saved")
        record = new(manifest)
        with self.connection() as con:
            con.execute("INSERT INTO report_actions VALUES (?,?)",
                        (record["record_id"], trust.canonical_json(record)))
        return inspect(record)

    def get(self, rid):
        with self.connection() as con:
            return self._get(con, rid)[1]

    def recent(self, limit=50):
        with self.connection() as con:
            ids = con.execute("SELECT id FROM report_actions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [self._get(con, rid)[1] for (rid,) in ids]

    def decide(self, rid, choice, reason, actor, head):
        if choice not in ("approved", "rejected"):
            raise ValueError("Invalid choice")
        _label(reason, "reason", 1000)
        _label(actor, "actor")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            record, view = self._get(con, rid)
            if view["head_hash"] != head or view["decision"] != "pending":
                raise Conflict("Decision changed; reload")
            trust.append_entry(record, "approval_granted" if choice == "approved" else "note",
                               actor=actor, payload={"event": "decision", "choice": choice,
                               "reason": reason, "binding": binding(view["manifest"], rid),
                               **({"expires_at": time.time()+600} if choice=="approved" else {}),
                               "identity_provenance": "local operator intent; unauthenticated"})
            self._save(con, record)
        return inspect(record)

    def revoke(self, rid, reason, actor, head):
        _label(reason, "reason", 1000)
        _label(actor, "actor")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            record, view = self._get(con, rid)
            if view["head_hash"] != head or view["decision"] != "approved" or view["attempts"]:
                raise Conflict("Approval changed or execution began; reload")
            trust.append_entry(record, "note", actor=actor,
                               payload={"event": "revocation", "reason": reason,
                                        "binding": binding(view["manifest"], rid),
                                        "identity_provenance": "local operator intent; unauthenticated"})
            self._save(con, record)
        return inspect(record)

    def _write(self, content):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.output_dir, flags)
        name = ".verity-" + secrets.token_hex(16)
        try:
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
            try:
                with os.fdopen(out, "wb") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            except BaseException:
                os.unlink(name, dir_fd=fd)
                raise
            os.replace(name, TARGET, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)

    def execute(self, rid, actor, head):
        _label(actor, "actor")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            record, view = self._get(con, rid)
            if view["head_hash"] != head or view["attempts"] or view["state"]=="expired":
                raise Conflict("Action changed or already attempted; reload")
            if self.config_store is not None and "effective_configuration" in view["manifest"]:
                try:
                    current=self.config_store.effective(view["manifest"]["agent_id"],TARGET)
                except ValueError:
                    raise Conflict("Effective configuration unavailable; renewed proposal required") from None
                if current!=view["manifest"]["effective_configuration"]:
                    raise Conflict("Effective configuration changed; renewed proposal and approval required")
            allowed = view["decision"] == "approved"
            attempt = trust.append_entry(record, "action_attempted", actor=actor,
                                         payload={"event": "attempt", "allowed": allowed,
                                         "binding": binding(view["manifest"], rid)})
            if not allowed:
                trust.append_entry(record, "action_result", payload={"event": "result",
                                   "attempt_hash": attempt["entry_hash"], "outcome": "denied",
                                   "external_action_executed": False})
            self._save(con, record)
        if not allowed:
            return inspect(record)
        # The reservation is committed before this effect. Any crash or storage
        # failure leaves an in-doubt record and never silently repeats the write.
        self._write(view["manifest"]["proposal"].encode())
        observed = self.observed_file()
        if observed["sha256"] != view["manifest"]["action"]["sha256"]:
            raise ValueError("Reserved write did not produce the approved file digest")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            record, current = self._get(con, rid)
            if current["state"] != "in_doubt" or current["head_hash"] != attempt["entry_hash"]:
                raise Conflict("Reserved action changed; outcome needs reconciliation")
            trust.append_entry(record, "action_result", payload={"event": "result",
                               "attempt_hash": attempt["entry_hash"], "outcome": "written",
                               "sha256": view["manifest"]["action"]["sha256"],
                               "external_action_executed": True})
            self._save(con, record)
        return inspect(record)

    def observed_file(self):
        try:
            fd = os.open(self.output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                file_fd = os.open(TARGET, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    info = os.fstat(file_fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTPUT:
                        raise ValueError("Output is not a bounded regular file")
                    with os.fdopen(file_fd, "rb", closefd=False) as f:
                        data = f.read(MAX_OUTPUT + 1)
                    return {"present": True, "sha256": digest(data), "bytes": len(data)}
                finally:
                    os.close(file_fd)
            finally:
                os.close(fd)
        except FileNotFoundError:
            return {"present": False, "sha256": None, "bytes": 0}
