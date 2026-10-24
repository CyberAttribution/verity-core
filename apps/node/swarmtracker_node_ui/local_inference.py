"""Bounded, local OpenClaw inference from an immutable effective configuration.

This gates requests made through Verity. It does not constrain independently
accessible OpenClaw credentials, OS permissions, or direct runtime calls.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

import requests

from verity_core.json_safety import bounded_json_loads
from verity_core.trust import context_analysis, inference_receipt

MAX_RUNTIME_RESPONSE_BYTES = 128 * 1024

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
            db.execute("CREATE TABLE IF NOT EXISTS context_exports (run_id TEXT PRIMARY KEY, bundle TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS request_exports (run_id TEXT PRIMARY KEY, bundle TEXT NOT NULL)")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def latest_for_agent(self, agent_id, *, state=None):
        label(agent_id)
        if state not in {None,"completed","denied","failed","reserved"}:
            raise ValueError("Invalid request state")
        with self._db() as db:
            if state is None:
                row=db.execute("SELECT record FROM runs WHERE agent_id=? ORDER BY at DESC,rowid DESC LIMIT 1",(agent_id,)).fetchone()
            else:
                row=db.execute("SELECT record FROM runs WHERE agent_id=? AND state=? ORDER BY at DESC,rowid DESC LIMIT 1",(agent_id,state)).fetchone()
        return json.loads(row["record"]) if row else None

    def recent(self):
        with self._db() as db:
            return [json.loads(r["record"]) for r in db.execute(
                "SELECT record FROM runs ORDER BY at DESC LIMIT 30")]

    def get(self, run_id):
        with self._db() as db:
            row=db.execute("SELECT record FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:raise KeyError(run_id)
        return json.loads(row[0])

    def request_export(self, run_id):
        with self._db() as db:
            row=db.execute("SELECT bundle FROM request_exports WHERE run_id=?", (run_id,)).fetchone()
        if row is None:raise KeyError(run_id)
        bundle=json.loads(row[0])
        if not inference_receipt.verify(bundle,bundle["checkpoint"]):
            raise ValueError("Stored request receipt is inconsistent")
        return bundle

    def run(self, *, agent_id, task_id, resource_id, purpose, prompt,
            requested_model="", requested_connection_id="", session_id="", dispatch=None,
            context_profile_id="", context_store=None, expected_configuration_sha256="", request_id=None):
        if request_id is not None:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError("Invalid request identity")
        label(agent_id); label(task_id); label(resource_id)
        if session_id:
            label(session_id)
        if purpose not in {"generate_text", "draft_text"}:
            raise ValueError("Unsupported purpose")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Prompt must contain 1 to 4000 characters")
        if not isinstance(requested_model, str) or len(requested_model) > 160:
            raise ValueError("Invalid requested model")
        if expected_configuration_sha256 and (not isinstance(expected_configuration_sha256,str) or not re.fullmatch("[0-9a-f]{64}",expected_configuration_sha256)):
            raise ValueError("Invalid expected configuration fingerprint")
        if requested_connection_id:
            label(requested_connection_id)
        if context_profile_id:
            from verity_core.organization_context import ContextStore, identifier
            identifier(context_profile_id)
            if not isinstance(context_store, ContextStore):
                raise ValueError("Authoritative local context store required")
        guard = context_store.capture_guard(context_profile_id) if context_profile_id else nullcontext(None)
        specimen = prompt
        with guard as capture:
            if capture is not None:
                prompt = context_analysis.compose_prompt(capture, specimen)
            cfg = self.configuration.effective(agent_id, resource_id)
            selection_denial=None
            if requested_connection_id and requested_connection_id!=cfg["connection_id"]:
                try:cfg=self.configuration.effective(agent_id,resource_id,requested_connection_id)
                except ValueError:selection_denial="Requested additional connection is not permitted or supported"
            if not requested_connection_id and expected_configuration_sha256:
                cfg=self.configuration.resolve_pre_dispatch(agent_id,resource_id,purpose)
            connection = self.configuration.latest("connection", cfg["connection_id"])
            observation = self.configuration.observations().get(("connection",cfg["connection_id"]))
            reason = None
            if cfg.get("routing_enabled",True) is not True:
                reason="Agent routed requests were paused by the operator"
            elif selection_denial:
                reason=selection_denial
            elif cfg.get("adapter_kind") == "custom_unverified":
                reason = "Custom agent is unverified; no dispatch adapter is enabled"
            elif expected_configuration_sha256 and hashlib.sha256(canonical(cfg).encode()).hexdigest() != expected_configuration_sha256:
                reason = "Configuration changed since request review; reload and review current settings"
            elif cfg.get("require_fresh_connection_observation", False) and not observation:
                reason = "Fresh local connection observation required; check health before dispatch"
            elif observation and observation["state"] != "reachable":
                reason = "Local connection observation is unavailable or stale; recheck before dispatch"
            elif self.configuration.connection_disabled(cfg["connection_id"]):
                reason = "Primary model connection was disabled by the operator"
            elif purpose not in cfg["allowed_tools"]:
                reason = "Purpose denied by agent configuration"
            elif requested_model and requested_model != cfg["model"]:
                reason = "Requested model differs from selected connection; fallback is not enabled"
            elif connection["body"]["scope"] != "local":
                reason = "External inference adapter is not enabled"
            elif connection["body"]["provider"] not in {"openclaw", "ollama_local"}:
                reason = "Only the local OpenClaw adapter is supported"
            elif not cfg["endpoint"].startswith(("http://127.0.0.1:", "http://[::1]:", "http://localhost:")):
                reason = "OpenClaw endpoint must be loopback"
            rid = request_id or str(uuid.uuid4())
            record = dict(id=rid, task_id=task_id, at=time.time(), agent_id=agent_id,
                          purpose=purpose, resource_id=resource_id, session_id=session_id or None, decision="denied" if reason else "allowed",
                          state="denied" if reason else "reserved", reason=reason,
                          prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                          model=cfg["model"], model_revision=cfg["model_revision"],
                          configuration=cfg, result=None,
                          requested_configuration_sha256=expected_configuration_sha256 or None)
            if requested_connection_id:
                record["requested_connection_id"]=requested_connection_id
            if capture is not None:
                record["context_analysis"] = dict(schema="context_analysis_input/v0.1",
                    capture=capture, specimen=specimen,
                    runtime_prompt_sha256=hashlib.sha256((cfg["instructions"]+"\n\n"+prompt).encode()).hexdigest())
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                limit = cfg.get("daily_request_limit",0)
                if not reason and limit:
                    day_start = int(record["at"] // 86400) * 86400  # UTC calendar day
                    count = db.execute("SELECT COUNT(*) FROM runs WHERE agent_id=? AND decision='allowed' AND at>=? AND at<?",
                                       (agent_id,day_start,day_start+86400)).fetchone()[0]
                    if count >= limit:
                        reason = "Daily agent request cap reached before dispatch"
                        record.update(decision="denied",state="denied",reason=reason)
                session_limit = cfg.get("session_request_limit",0)
                if not reason and session_limit:
                    if not session_id:
                        reason = "Session ID required by agent request cap"
                    else:
                        # Count old and new records through JSON to preserve the
                        # existing on-disk schema. The transaction serializes reservations.
                        rows = db.execute("SELECT record FROM runs WHERE agent_id=? AND decision='allowed'",(agent_id,))
                        count = sum(json.loads(row[0]).get("session_id")==session_id for row in rows)
                        if count >= session_limit:
                            reason = "Session agent request cap reached before dispatch"
                    if reason:
                        record.update(decision="denied",state="denied",reason=reason)
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                           (rid, task_id, record["at"], agent_id, purpose, record["decision"],
                            record["state"], canonical(record)))
                if reason:
                    db.execute("INSERT INTO request_exports VALUES (?,?)", (rid,canonical(inference_receipt.export(record))))
                if capture is not None and reason:
                    db.execute("INSERT INTO context_exports VALUES (?,?)", (rid, canonical(context_analysis.export(record))))
            if reason:
                return record
        # The reservation is durable before dispatch. A crash leaves it visibly
        # reserved; the same task ID cannot be dispatched again silently.
        try:
            current_observation = self.configuration.observations().get(("connection",cfg["connection_id"]))
            if cfg.get("require_fresh_connection_observation", False) and not current_observation:
                raise ValueError("Fresh local connection observation required before dispatch")
            if current_observation and current_observation["state"] != "reachable":
                raise ValueError("Local connection observation changed before dispatch")
            if self.configuration.connection_disabled(cfg["connection_id"]):
                raise ValueError("Primary model connection disabled before dispatch")
            selected=cfg.get("selected_connection_id", "")
            current_cfg=self.configuration.for_snapshot(agent_id,resource_id,cfg)
            if current_cfg != cfg:
                raise ValueError("Effective configuration changed before dispatch")
            # Hold the configuration write reservation across the last checks
            # and bounded runtime call. Independent config instances/processes
            # cannot commit removal or policy changes in that interval.
            with self.configuration.dispatch_guard(agent_id, resource_id, cfg):
                result = (dispatch or self._dispatch)(cfg, prompt, purpose)
            if not isinstance(result, dict) or result.get("model") != cfg["model"]:
                raise ValueError("Runtime model identity missing or mismatched")
            if result.get("decision") not in ("allowed", "allow") or result.get("governance_failures"):
                raise ValueError("Runtime did not report an allowed decision")
            label(result.get("event_id") or result.get("id"))
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
            db.execute("INSERT INTO request_exports VALUES (?,?)", (rid,canonical(inference_receipt.export(record))))
            if capture is not None:
                db.execute("INSERT INTO context_exports VALUES (?,?)", (rid, canonical(context_analysis.export(record))))
        return record

    def context_export(self, run_id):
        with self._db() as db:
            row = db.execute("SELECT bundle FROM context_exports WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        bundle = json.loads(row[0])
        if not context_analysis.verify(bundle, bundle["checkpoint"]):
            raise ValueError("Stored context analysis integrity failed")
        return bundle

    @staticmethod
    def _dispatch(cfg, prompt, purpose):
        if 'transport_summary' in cfg:
            from .managed_ssh_tunnel import require_connection
            require_connection(cfg['connection_id'],cfg['connection_hash'])
        if cfg['provider']=='ollama_local':
            return LocalInference._ollama_dispatch(cfg,prompt)
        url = cfg["endpoint"].rstrip("/") + "/v0/generate"
        config.assert_local(url)
        payload = dict(prompt=cfg["instructions"] + "\n\n" + prompt,
                       model=cfg["model"], options={"num_ctx": 4096, "num_predict": 512},
                       instruction_source="operator", source_document_role="specimen",
                       evidence_boundary_enforced=True, execution_location="local",
                       meta={"verity_task_purpose": purpose})
        with requests.Session() as session:
            session.trust_env = False
            with session.post(url, json=payload, timeout=90, allow_redirects=False,
                              stream=True) as response:
                if response.is_redirect:
                    raise ValueError("Runtime redirect refused")
                response.raise_for_status()
                # Count actual decoded bytes, not a possibly absent or false
                # Content-Length. Close the response on overflow or parse failure.
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    if len(raw) + len(chunk) > MAX_RUNTIME_RESPONSE_BYTES:
                        raise ValueError("Runtime response envelope exceeds size limit")
                    raw.extend(chunk)
                data = bounded_json_loads(raw, max_depth=32)
        return data

    @staticmethod
    def _ollama_dispatch(cfg,prompt):
        """Direct local text, with a Verity client observation, not runtime policy."""
        from .ollama_client import _refuse_cloud
        _refuse_cloud(cfg['model'])
        endpoint=cfg['endpoint'].rstrip('/');config.assert_local(endpoint)
        def call(method,path,payload=None,timeout=5):
            from .bounded_loopback import request_json
            return request_json(endpoint,method,path,payload,timeout=timeout,max_bytes=MAX_RUNTIME_RESPONSE_BYTES)
        metadata=call('POST','/api/show',{'model':cfg['model']})
        if (metadata.get('remote_model') or metadata.get('remote_host') or not metadata.get('model_info')
                or metadata.get('details',{}).get('format')!='gguf'):
            raise ValueError('Locally stored GGUF model required')
        tags=call('GET','/api/tags');models=[m for m in tags.get('models',[]) if m.get('name')==cfg['model']]
        if len(models)!=1:raise ValueError('Exact local model inventory missing or ambiguous')
        revision=cfg.get('model_revision','unknown')
        observed_digest=models[0].get('digest','')
        if not isinstance(observed_digest,str):raise ValueError('Model digest missing')
        if revision not in ('','unknown') and observed_digest.removeprefix('sha256:')!=revision.removeprefix('sha256:'):
            raise ValueError('Local model digest differs from configured revision')
        data=call('POST','/api/generate',dict(model=cfg['model'],prompt=cfg['instructions']+'\n\n'+prompt,
                    stream=False,options={'num_ctx':4096,'num_predict':512},keep_alive=cfg.get('runtime_options',{}).get('keep_alive_seconds',300)),timeout=90)
        # This allowed value reports the already-applied Verity dispatch gate.
        # Ollama has no governance decision or native audit-event identity.
        return dict(data,decision='allowed',event_id='verity-client-observation-'+str(uuid.uuid4()),
                    provider='ollama_local; Verity client gate, no runtime governance')
