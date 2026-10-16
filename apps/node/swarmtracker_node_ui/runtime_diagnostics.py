"""Metadata-only first-launch checks. Never load a model or send evidence."""
from . import config, openclaw_client as oc

def check():
    kind=config.RUNTIME_KIND
    result={"runtime_kind":kind,"model":config.DEFAULT_MODEL,"runtime_reachable":False,
            "model_list_reachable":False,"model_listed":False,
            "scope":"Metadata only. No model was loaded and no analysis was run."}
    if kind not in ("openclaw","ollama"):
        return dict(result,state="invalid_configuration",message="Choose a supported analysis runtime in setup.",next_step="Save OpenClaw v0 or Ollama as the runtime.")
    base=config.OLLAMA_URL if kind=="ollama" else config.OPENCLAW_URL
    endpoint="/api/version" if kind=="ollama" else "/healthz"
    try:
        data=oc._request_json("GET",base.rstrip("/")+endpoint,timeout=1.5)
        valid=isinstance(data,dict) and (isinstance(data.get("version"),str) if kind=="ollama" else data.get("status")=="ok")
        if not valid:raise ValueError("Unexpected runtime metadata")
        result["runtime_reachable"]=True
    except Exception:
        return dict(result,state="runtime_unreachable",message="The selected runtime did not answer its health check.",
                    next_step="Start "+("Ollama" if kind=="ollama" else "the existing OpenClaw v0 service")+" and check its saved loopback address. Activation is separate from runtime availability.")
    try:
        data=oc._request_json("GET",config.OLLAMA_URL.rstrip("/")+"/api/tags",timeout=1.5)
        if not isinstance(data,dict) or not isinstance(data.get("models"),list):raise ValueError("Unexpected model list")
        names=[v["name"] for v in data["models"] if isinstance(v,dict) and isinstance(v.get("name"),str) and len(v["name"])<=240]
        result["available_models"]=names[:12]
        result["model_list_reachable"]=True
        result["model_listed"]=config.DEFAULT_MODEL in names
    except Exception:
        return dict(result,state="model_list_unavailable",message="The runtime answered, but the Ollama model inventory could not be checked.",
                    next_step="Check the saved Ollama address and start its local service, then test again.")
    if not result["model_listed"]:
        return dict(result,state="model_missing",message="The runtime answered, but the selected model is not listed as installed.",
                    next_step="Download the intended local model in Ollama, or copy its exact installed name into setup, save, and test again.")
    return dict(result,state="metadata_ready",message="The runtime is reachable and the selected model is listed as installed.",
                next_step="Open Analyze and submit a small representative specimen. A model listing is not proof of a successful inference.")
