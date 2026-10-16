"""Direct-local Ollama client (additive). Loopback-only, no proxy, no redirects.

Direct-local mode has no governance runtime in the loop: the model output is
untrusted text, no runtime audit event is emitted by this module, and no claim
is made that the model enforces Keel policy. Existing node policy governs
operations; this client only transports a prompt and normalizes a reply.
"""
import requests
import re

from . import config, keel_policy


def _request_json(method, url, **kwargs):
    """Exact loopback only, without proxy inheritance or redirect following."""
    config.assert_local(url)
    kwargs["allow_redirects"] = False
    with requests.Session() as session:
        session.trust_env = False
        with session.request(method, url, **kwargs) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Local runtime redirect refused")
            response.raise_for_status()
            return response.json()


def health(timeout: float = None) -> dict:
    """GET /api/version with explicit local runtime identity. {} on failure."""
    try:
        data = _request_json("GET", config.OLLAMA_URL.rstrip("/")+"/api/version",
                             timeout=timeout or config.HEALTH_TIMEOUT)
        if not isinstance(data,dict) or not isinstance(data.get("version"),str):return {}
        return {"ok":True,"status":"ok","provider":"ollama","node":config.NODE_NAME,
                "default_model":config.DEFAULT_MODEL,"version":data["version"],
                "framework":"Ollama native API (direct local)"}
    except Exception:return {}


def _refuse_cloud(model: str) -> None:
    if not isinstance(model,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,239}",model) or model.lower().endswith((":cloud","-cloud")) or model.lower().startswith("cloud/"):
        raise ValueError("Cloud models are refused in direct-local mode")


def generate(prompt: str, *, model: str = None, policy: str = None, operator: str = None) -> dict:
    """POST /api/generate (stream=false, no tools) and normalize the reply.

    Returns ok, response, provider, model, runtime_kind, untrusted, raw, error.
    Never raises; transport/validation failures come back as ok=False.
    Model output is untrusted; no audit event is fabricated here.
    """
    chosen = model or config.DEFAULT_MODEL
    try:
        _refuse_cloud(chosen)
    except ValueError as e:
        return {"ok": False, "error": str(e), "provider": "ollama",
                "model": chosen, "runtime_kind": "ollama", "untrusted": True,
                "response": "", "raw": None}
    # Require metadata for locally stored weights before sending evidence.
    # This trusts the independently operated loopback runtime, not attestation.
    try:
        meta = _request_json("POST", config.OLLAMA_URL.rstrip("/")+"/api/show",
                             json={"model":chosen}, timeout=config.HEALTH_TIMEOUT)
        if (not isinstance(meta,dict) or meta.get("remote_model") or meta.get("remote_host")
                or not isinstance(meta.get("model_info"),dict) or not meta["model_info"]
                or meta.get("details",{}).get("format") != "gguf"):
            return _fail(chosen,"Locally stored GGUF model metadata is required.")
    except Exception:
        return _fail(chosen,"Local model is unavailable; download it in Ollama and retry.")
    payload = {
        "model": chosen,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": 1024,   # bounded generation
            "num_ctx": 4096,       # bounded context (16GB host)
        },
        # No "tools" key: direct-local mode has no tool execution.
    }
    url = config.OLLAMA_URL.rstrip("/") + "/api/generate"
    try:
        data = _request_json("POST", url, json=payload,
                             timeout=config.GENERATE_TIMEOUT)
    except requests.exceptions.ConnectionError:
        return _fail(chosen, "Ollama unreachable at the configured loopback endpoint. Is Ollama running?")
    except requests.exceptions.Timeout:
        return _fail(chosen, "Local model timed out. It may still be loading; try again.")
    except Exception:
        return _fail(chosen, "Local runtime request failed.")

    # Malformed-response refusal: strict shape checks, no guessing.
    if not isinstance(data, dict) or not isinstance(data.get("response"), str):
        return _fail(chosen, "Malformed Ollama response refused.")
    if data.get("done") is not True:
        return _fail(chosen, "Ollama reported done=false; incomplete response refused.")

    if data.get("model") != chosen:
        return _fail(chosen,"Runtime returned a different model than requested.")
    if not data["response"].strip() or data.get("done_reason") == "length":
        return _fail(chosen,"Empty or truncated Ollama response refused.")
    return {
        "ok": True,
        "response": data["response"],
        "provider": "ollama",
        "model": data.get("model") or chosen,
        "runtime_kind": "ollama",
        "framework": "Ollama native API (direct local)",
        "node": config.NODE_NAME,
        "policy": keel_policy.enforced_policy(policy),
        "openclaw_event_id": None,
        "untrusted": True,          # model output is untrusted specimen text
        "raw": data,
        "error": None,
    }


def _fail(model: str, message: str) -> dict:
    return {"ok": False, "response": "", "provider": "ollama", "model": model,
            "runtime_kind": "ollama", "untrusted": True, "raw": None,
            "error": message}

