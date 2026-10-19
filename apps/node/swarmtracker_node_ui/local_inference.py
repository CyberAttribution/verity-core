"""Bounded, local OpenClaw inference from an immutable effective configuration.

This gates requests made through Verity. It does not constrain independently
accessible OpenClaw credentials, OS permissions, or direct runtime calls.
"""
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

import requests

from . import config
from .customer_config import CustomerConfig, canonical, label


class LocalInference:
    def __init__(self, path, configuration: CustomerConfig):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.configuration = configuration
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, at REAL NOT NULL,
                agent_id TEXT NOT NULL, purpose TEXT NOT NULL, decision TEXT NOT NULL,
                state TEXT NOT NULL, record TEXT NOT NULL)""")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def recent(self):
        with self._db() as db:
            return [json.loads(r["record"]) for r in db.execute(
                "SELECT record FROM runs ORDER BY at DESC LIMIT 30")]

    def run(self, *, agent_id, task_id, resource_id, purpose, prompt,
            requested_model="", dispatch=None):
        label(agent_id); label(task_id); label(resource_id)
        if purpose not in {"generate_text", "draft_text"}:
            raise ValueError("Unsupported purpose")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Prompt must contain 1 to 4000 characters")
        if not isinstance(requested_model, str) or len(requested_model) > 160:
            raise ValueError("Invalid requested model")
        cfg = self.configuration.effective(agent_id, resource_id)
        connection = self.configuration.latest("connection", cfg["connection_id"])
        reason = None
        if purpose not in cfg["allowed_tools"]:
            reason = "Purpose denied by agent configuration"
        elif requested_model and requested_model != cfg["model"]:
            reason = "Requested model differs from primary model; fallback is not enabled"
        elif connection["body"]["scope"] != "local":
            reason = "External inference adapter is not enabled"
        elif connection["body"]["provider"] not in {"openclaw", "ollama_local"}:
            reason = "Only the local OpenClaw adapter is supported"
        elif not cfg["endpoint"].startswith(("http://127.0.0.1:", "http://[::1]:", "http://localhost:")):
            reason = "OpenClaw endpoint must be loopback"
        rid = str(uuid.uuid4())
        record = dict(id=rid, task_id=task_id, at=time.time(), agent_id=agent_id,
                      purpose=purpose, resource_id=resource_id, decision="denied" if reason else "allowed",
                      state="denied" if reason else "reserved", reason=reason,
                      prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                      model=cfg["model"], model_revision=cfg["model_revision"],
                      configuration=cfg, result=None)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                       (rid, task_id, record["at"], agent_id, purpose, record["decision"],
                        record["state"], canonical(record)))
        if reason:
            return record
        # The reservation is durable before dispatch. A crash leaves it visibly
        # reserved; the same task ID cannot be dispatched again silently.
        try:
            if self.configuration.effective(agent_id, resource_id) != cfg:
                raise ValueError("Effective configuration changed before dispatch")
            result = (dispatch or self._dispatch)(cfg, prompt, purpose)
            if not isinstance(result, dict) or result.get("model") != cfg["model"]:
                raise ValueError("Runtime model identity missing or mismatched")
            if result.get("decision") not in ("allowed", "allow") or result.get("governance_failures"):
                raise ValueError("Runtime did not report an allowed decision")
            if not isinstance(result.get("event_id") or result.get("id"), str):
                raise ValueError("Runtime event identity missing")
            body = result.get("response")
            if not isinstance(body, str) or not body.strip() or len(body) > 20000:
                raise ValueError("Runtime result empty, incomplete, or oversized")
            if result.get("done") is not True or result.get("done_reason") not in (None, "stop"):
                raise ValueError("Runtime result incomplete")
            record["state"] = "completed"
            record["result"] = dict(response=body, response_sha256=hashlib.sha256(body.encode()).hexdigest(),
                                    runtime_event_id=result.get("event_id") or result.get("id"),
                                    runtime_decision=result.get("decision"),
                                    provider=result.get("provider"),
                                    finish_reason=result.get("done_reason"))
        except Exception as exc:
            record["state"] = "failed"
            record["reason"] = "Runtime failed or returned unverifiable output (" + type(exc).__name__ + ")"
        with self._db() as db:
            db.execute("UPDATE runs SET state=?,record=? WHERE id=?",
                       (record["state"], canonical(record), rid))
        return record

    @staticmethod
    def _dispatch(cfg, prompt, purpose):
        url = cfg["endpoint"].rstrip("/") + "/v0/generate"
        config.assert_local(url)
        payload = dict(prompt=cfg["instructions"] + "\n\n" + prompt,
                       model=cfg["model"], options={"num_ctx": 4096, "num_predict": 512},
                       instruction_source="operator", source_document_role="specimen",
                       evidence_boundary_enforced=True, execution_location="local",
                       meta={"verity_task_purpose": purpose})
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(url, json=payload, timeout=90, allow_redirects=False)
            if response.is_redirect:
                raise ValueError("Runtime redirect refused")
            response.raise_for_status()
            data = response.json()
        return data
