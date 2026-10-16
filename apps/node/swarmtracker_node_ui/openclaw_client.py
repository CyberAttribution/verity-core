"""
Thin local client for the OpenClaw runtime (v0.3, /v0/generate).

Additive only — mirrors governance/verity_bridge/openclaw_client.py but is
self-contained so nothing existing is touched. Loopback-only by design.
"""
import requests

from . import config, keel_policy


def _request_json(method, url, **kwargs):
    """Loopback only, without proxy inheritance or redirect following."""
    config.assert_local(url)
    kwargs["allow_redirects"] = False
    with requests.Session() as session:
        session.trust_env = False
        with session.request(method, url, **kwargs) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Local runtime redirect refused")
            response.raise_for_status()
            return response.json()


def healthz(timeout: float = None) -> dict:
    """GET /healthz. Returns {} on any failure (caller treats as offline)."""
    if config.RUNTIME_KIND == "ollama":
        from . import ollama_client
        return ollama_client.health(timeout)
    try:
        return _request_json("GET", config.OPENCLAW_URL.rstrip("/") + "/healthz",
                             timeout=timeout or config.HEALTH_TIMEOUT)
    except Exception:
        return {}


def ollama_models(timeout: float = None) -> list:
    """GET Ollama /api/tags. Returns [] on any failure."""
    try:
        data = _request_json("GET", config.OLLAMA_URL.rstrip("/") + "/api/tags",
                             timeout=timeout or config.HEALTH_TIMEOUT)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def generate(prompt: str, *, model: str = None, policy: str = None,
             operator: str = None) -> dict:
    """POST one inference to OpenClaw /v0/generate and normalize the reply.

    Returns:
      ok, response (text), openclaw_event_id, governance_alarm,
      governance_failures, provider, node, policy, model, decision,
      raw (full JSON), error (message when ok is False).
    Never raises: transport errors come back as ok=False.
    """
    if config.RUNTIME_KIND == "ollama":
        from . import ollama_client
        return ollama_client.generate(prompt, model=model, policy=policy, operator=operator)
    if config.RUNTIME_KIND != "openclaw":
        return {"ok":False,"error":"Unsupported runtime kind"}
    url = config.OPENCLAW_URL.rstrip("/") + "/v0/generate"
    # v0.1.3 Keel policy lock: the policy sent to the runtime is always
    # resolved through the local policy file — never raw caller input.
    # Unknown requested policies collapse to the locked default (the analyze
    # flow hard-rejects them earlier; this is defense in depth).
    locked_policy = keel_policy.enforced_policy(policy)
    pol = keel_policy.policy_summary()
    payload = {
        "prompt": prompt,
        "options": {"num_ctx":4096,"num_predict":1024},
        "model": model or config.DEFAULT_MODEL,
        "policy": locked_policy,
        "operator": operator or config.OPERATOR,
        # v0.3 evidence-boundary fields: user-submitted evidence is a
        # specimen, never instructions to obey.
        "instruction_source": "operator",
        "source_document_role": "specimen",
        "evidence_boundary_enforced": True,
        "execution_location": "local",
        # Policy-lock facts ride under meta — accepted by v0.2/v0.3
        # contracts and safely ignored by older handlers.
        "meta": {
            "tools_allowed": pol["tools_allowed"],
            "tools_denied": pol["tools_denied"],
            "network_allowed": pol["network_allowed"],
            "cloud_escalation_allowed": pol["cloud_escalation_allowed"],
            "human_approval_required": False,  # read-only analysis
            "keel_policy_source": pol["source"],
            "keel_policy_version": pol["version"],
            "keel_enforcement_layer": "node_ui",
        },
    }
    try:
        data = _request_json("POST", url, json=payload,
                             timeout=config.GENERATE_TIMEOUT)
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "Local runtime unreachable at the configured loopback endpoint. "
                                      "Is OpenClaw running?"}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Local model timed out. The runtime may still be "
                                      "loading the model; try again."}
    except Exception as e:
        return {"ok": False, "error": f"Local runtime request failed ({e.__class__.__name__})."}

    if not isinstance(data,dict):
        return {"ok":False,"error":"Local runtime returned an invalid response. Test the connection and retry."}

    # Defensive field lookup — response text and event id keys may vary.
    text = None
    for k in ("response", "text", "output", "completion"):
        if isinstance(data.get(k), str):
            text = data[k]
            break
    if not text or not text.strip() or data.get("done") is False or data.get("done_reason") == "length":
        return {"ok":False,"error":"Local runtime returned an empty or incomplete response. Retry with a smaller specimen."}
    raw_id = data.get("id") or data.get("event_id") or data.get("openclaw_event_id")
    event_id = raw_id if isinstance(raw_id,str) and raw_id.strip() and len(raw_id)<160 else None

    return {
        "ok": True,
        "response": text or "",
        "openclaw_event_id": event_id,
        "governance_alarm": data.get("governance_alarm"),
        "governance_failures": data.get("governance_failures"),
        "provider": data.get("provider"),
        "node": data.get("node"),
        "policy": data.get("policy") or payload["policy"],
        "model": data.get("model") or payload["model"],
        "decision": data.get("decision"),
        "raw": data,
        "error": None,
    }
