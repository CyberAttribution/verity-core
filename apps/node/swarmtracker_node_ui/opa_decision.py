"""Isolated experimental OPA decision adapter; never substitutes Keel approval.

The caller must enforce the returned decision at its own execution boundary.
"""
import hashlib
import json
import urllib.parse
import urllib.request


def decide(input_document, policy_bytes, evaluator=None):
    if not isinstance(input_document,dict) or not input_document.get("subject") or not input_document.get("action"):
        return {"allow":False,"reason":"missing-input","policy_sha256":hashlib.sha256(policy_bytes).hexdigest()}
    digest=hashlib.sha256(policy_bytes).hexdigest()
    try:
        response=(evaluator or local_opa)(input_document)
        if not isinstance(response,dict) or type(response.get("result")) is not bool:
            return {"allow":False,"reason":"undefined-decision","policy_sha256":digest}
        return {"allow":response["result"],"reason":"opa-decision","policy_sha256":digest}
    except Exception:
        return {"allow":False,"reason":"evaluator-unavailable","policy_sha256":digest}


def local_opa(input_document, endpoint="http://127.0.0.1:8181/v1/data/verity/allow"):
    url=urllib.parse.urlparse(endpoint)
    if (url.scheme,url.hostname,url.port,url.path) != ("http","127.0.0.1",8181,"/v1/data/verity/allow"):
        raise ValueError("OPA endpoint must be local and fixed")
    req=urllib.request.Request(endpoint,data=json.dumps({"input":input_document}).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=2) as response:
        return json.load(response)


def execute_if_allowed(document, policy_bytes, action, evaluator=None, keel_approved=False):
    decision=decide(document,policy_bytes,evaluator)
    if not keel_approved or not decision["allow"]:
        raise PermissionError(decision["reason"] if keel_approved else "Keel approval required")
    return action(),decision
