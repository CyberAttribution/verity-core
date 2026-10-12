"""Unified Gemini transport (Sprint 1 Phase 2, final step).

Two call paths are preserved EXACTLY as each service had them — they are NOT
merged into one behavior. Both return the raw Gemini result JSON; each caller
keeps its own response parsing.

  * call_gemini            — Verity: iterate PREFERRED_GEMINI_MODELS, skip 404
                             and continue, raise the last error if all fail.
  * call_gemini_structured — PhishFinder: resolve gemini-1.5 pro/flash (or honor
                             an explicit model_name), POST with the 2-attempt
                             503 retry, raise on non-OK, return response.json().
"""
import os
import re
import time

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


# --- Verity model preference list (verbatim construction) ------------------
PREFERRED_GEMINI_MODELS = []

_user_primary_model = os.environ.get("GEMINI_MODEL_PRIMARY")
if _user_primary_model:
    PREFERRED_GEMINI_MODELS.append(_user_primary_model.strip())

PREFERRED_GEMINI_MODELS.extend([
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-pro",
])

# Deduplicate while preserving order and stripping empties
_seen_models = set()
PREFERRED_GEMINI_MODELS = [
    m for m in PREFERRED_GEMINI_MODELS
    if m and not (m in _seen_models or _seen_models.add(m))
]


_KEY_RE = re.compile(r"([?&]key=)[^&\s\"']+")
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_GTOKEN_RE = re.compile(r"ya29\.[A-Za-z0-9._\-]+")


def sanitize_provider_text(text, cap=400):
    """Strip any secret (AI Studio key= value, OAuth bearer/access token)
    and cap length, so a provider error can be surfaced to
    operators/logs/exports without leaking a credential. Never raises."""
    try:
        s = _KEY_RE.sub(r"\1<redacted>", str(text or ""))
        s = _BEARER_RE.sub(r"\1<redacted>", s)
        s = _GTOKEN_RE.sub("<redacted>", s)
        if GEMINI_API_KEY:
            s = s.replace(GEMINI_API_KEY, "<redacted>")
        return s[:cap]
    except Exception:
        return ""


class ProviderError(Exception):
    """Carries per-model attempt records with sanitized status/body so
    callers can surface *why* a call failed (e.g. 403) without ever
    exposing a credential. `attempts`: [{model, status, error}]."""

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []

    @property
    def attempted_models(self):
        return [a.get("model") for a in self.attempts if a.get("model")]

    @property
    def status(self):
        for a in reversed(self.attempts):
            if a.get("status"):
                return a["status"]
        return None


# Backward-compatible alias (v0.3.x callers/tests).
GeminiError = ProviderError


# ============================ Vertex AI ==============================
# v0.4.0: governed cloud inference moves to Vertex AI (billed to the
# project's credits, latest Gemini models). Authentication is
# Application Default Credentials — NO api key in the URL, logs, or
# exception strings. Same generateContent body contract as before, so
# the media pipeline is unchanged above the router boundary.

VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# ---- Vertex request-body normalization (v0.5.0) ----
# Vertex generateContent accepts ONLY role "user" or "model" on
# contents[]. AI Studio silently defaulted a missing role to "user";
# Vertex returns HTTP 400 "Please use a valid role: user, model". This
# single layer maps the application's internal message shapes into a
# valid Vertex request: evidence/operator -> user, prior provider output
# -> model, system instructions -> systemInstruction (never a content
# role). No other internal label (assistant/system/developer/tool/…) may
# be serialized as a Vertex content role.
_VERTEX_ROLE_MAP = {
    "user": "user", "operator": "user", "evidence": "user",
    "human": "user", "input": "user",
    "model": "model", "assistant": "model", "ai": "model",
    "output": "model",
}
_VERTEX_SYSTEM_ROLES = {"system", "developer", "instruction"}


def normalize_vertex_body(prompt=None, parts=None, system=None,
                          history=None):
    """Build a valid Vertex generateContent body.

    - `system`: text -> systemInstruction (NOT a content role).
    - `history`: optional [{"role","text"|"parts"}] prior turns; roles
      are mapped (assistant->model, etc.); system-ish roles are folded
      into systemInstruction. Unknown roles raise ValueError rather than
      emit an invalid request.
    - `prompt`/`parts`: the current operator/evidence turn -> role user,
      with multimodal `parts` attached to THIS user message.
    Returns a dict with `contents` (each carrying a valid role) and, when
    present, `systemInstruction`.
    """
    system_texts = []
    if system:
        system_texts.append(str(system))
    contents = []
    for msg in (history or []):
        role_in = str(msg.get("role", "user")).lower().strip()
        text = msg.get("text")
        mparts = msg.get("parts")
        if role_in in _VERTEX_SYSTEM_ROLES:
            if text:
                system_texts.append(str(text))
            continue
        if role_in not in _VERTEX_ROLE_MAP:
            raise ValueError(
                f"unsupported message role for Vertex: {role_in!r}")
        entry_parts = list(mparts) if mparts else (
            [{"text": str(text)}] if text is not None else [])
        contents.append({"role": _VERTEX_ROLE_MAP[role_in],
                         "parts": entry_parts})
    # current turn (operator/evidence) — always role user, parts here
    cur_parts = []
    if prompt is not None:
        cur_parts.append({"text": str(prompt)})
    for p in parts or []:
        cur_parts.append(p)
    if cur_parts:
        contents.append({"role": "user", "parts": cur_parts})
    body = {"contents": contents}
    if system_texts:
        body["systemInstruction"] = {
            "parts": [{"text": "\n\n".join(system_texts)}]}
    return body


def adc_available():
    try:
        import google.auth  # noqa: F401
        return True
    except Exception:
        return False


def _adc_bearer_token():
    """Return (token, adc_project). Raises ProviderError (sanitized) on
    any ADC problem. The token is used only in the Authorization header,
    never logged."""
    try:
        from google.auth import default as _default
        from google.auth.transport.requests import Request as _Req
    except Exception:
        raise ProviderError(
            "google-auth is not installed on this node "
            "(pip install google-auth); Vertex escalation unavailable.",
            attempts=[{"model": None, "status": None,
                       "error": "google-auth missing"}])
    try:
        creds, adc_project = _default(scopes=VERTEX_SCOPES)
        creds.refresh(_Req())
        return creds.token, adc_project
    except Exception as e:
        raise ProviderError(
            sanitize_provider_text(
                "Application Default Credentials unavailable "
                f"({e.__class__.__name__}). Run "
                "`gcloud auth application-default login`."),
            attempts=[{"model": None, "status": None,
                       "error": "ADC not configured"}])


def call_vertex(body, models, project, location, timeout=120):
    """POST generateContent to Vertex AI, trying `models` in order.
    Returns the response JSON augmented with `_model_used` and
    `_latency_ms`. Raises ProviderError (sanitized attempts) on total
    failure. No credential ever appears in the URL or in any surfaced
    string."""
    if not project:
        raise ProviderError(
            "GOOGLE_CLOUD_PROJECT is not set; Vertex escalation "
            "unavailable.",
            attempts=[{"model": None, "status": None,
                       "error": "no project configured"}])
    token, adc_project = _adc_bearer_token()
    project = project or adc_project
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    # The `global` endpoint has NO region prefix on the host and uses
    # `locations/global` in the path. Newer Gemini models (3.x) are
    # frequently served only from `global`, not from regional hosts —
    # which returns 404 "model not found" even when auth/project are
    # correct. Regional hosts keep the `{region}-` prefix.
    host = ("https://aiplatform.googleapis.com" if location == "global"
            else f"https://{location}-aiplatform.googleapis.com")
    attempts = []
    for model in models:
        url = (f"{host}/v1/projects/{project}/locations/{location}/"
               f"publishers/google/models/{model}:generateContent")
        t0 = time.time()
        try:
            resp = requests.post(url, headers=headers, json=body,
                                 timeout=timeout)
            latency = int((time.time() - t0) * 1000)
            if resp.status_code == 404:
                attempts.append({"model": model, "status": 404,
                                 "latency_ms": latency,
                                 "error": "model not found (404)"})
                continue
            if resp.status_code >= 400:
                attempts.append({"model": model,
                                 "status": resp.status_code,
                                 "latency_ms": latency,
                                 "error": f"HTTP {resp.status_code}: "
                                 + sanitize_provider_text(
                                     getattr(resp, "text", ""))})
                continue
            data = resp.json()
            data["_model_used"] = model
            data["_latency_ms"] = latency
            data["_project"] = project
            data["_location"] = location
            data["_attempts"] = attempts + [{"model": model,
                                             "status": 200,
                                             "latency_ms": latency}]
            return data
        except Exception as e:
            attempts.append({"model": model, "status": None,
                             "latency_ms": int((time.time() - t0) * 1000),
                             "error": sanitize_provider_text(
                                 f"{e.__class__.__name__}: {e}")})
            continue
    last = attempts[-1] if attempts else {"error": "no model configured"}
    raise ProviderError(
        f"all Vertex attempts failed ({last.get('error')})",
        attempts=attempts)


def list_vertex_models(project, location, timeout=30):
    """Diagnostic: return the exact publisher model IDs available to THIS
    project/location (ground truth, so nobody has to guess an id). Returns
    {ok, location, models:[{id, launch_stage, methods}], error?}. Never
    raises; sanitized errors."""
    if not project:
        return {"ok": False, "error": "GOOGLE_CLOUD_PROJECT not set",
                "models": []}
    try:
        token, adc_project = _adc_bearer_token()
    except ProviderError as e:
        return {"ok": False, "error": sanitize_provider_text(str(e)),
                "models": []}
    project = project or adc_project
    host = ("https://aiplatform.googleapis.com" if location == "global"
            else f"https://{location}-aiplatform.googleapis.com")
    url = (f"{host}/v1/publishers/google/models"
           f"?view=PUBLISHER_MODEL_VIEW_BASIC")
    try:
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {token}"},
            timeout=timeout)
        if resp.status_code >= 400:
            return {"ok": False, "location": location,
                    "error": f"HTTP {resp.status_code}: "
                    + sanitize_provider_text(getattr(resp, "text", "")),
                    "models": []}
        data = resp.json()
        out = []
        for m in data.get("publisherModels", []) or []:
            name = m.get("name", "")
            mid = name.split("/models/")[-1] if "/models/" in name \
                else name
            out.append({"id": mid,
                        "launch_stage": m.get("launchStage"),
                        "version": m.get("versionId")})
        return {"ok": True, "location": location, "models": out}
    except Exception as e:
        return {"ok": False, "location": location,
                "error": sanitize_provider_text(
                    f"{e.__class__.__name__}: {e}"), "models": []}


def call_gemini(body, headers=None, timeout=60):
    """
    Helper to call Gemini with a preferred model list and graceful fallback.
    Tries each model in PREFERRED_GEMINI_MODELS until one succeeds or all fail.
    On total failure raises GeminiError carrying sanitized per-model
    attempts (status + body), never a raw exception whose string embeds
    the key-bearing URL.
    """
    if not GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not set.",
                          attempts=[{"model": None, "status": None,
                                     "error": "no api key configured"}])

    if headers is None:
        headers = {"Content-Type": "application/json"}

    attempts = []

    for model in PREFERRED_GEMINI_MODELS:
        api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        try:
            print(f"GEMINI CALL: Attempting model '{model}'")
            response = requests.post(api_url, headers=headers, json=body, timeout=timeout)

            if response.status_code == 404:
                print(f"GEMINI CALL: Model '{model}' returned 404 (not found), trying next if available.")
                attempts.append({"model": model, "status": 404,
                                 "error": "model not found (404)"})
                continue

            if response.status_code >= 400:
                # Capture the body (sanitized) — this is where a 403
                # explains itself (API not enabled / key restricted /
                # billing). raise_for_status() would discard it.
                body_txt = sanitize_provider_text(
                    getattr(response, "text", ""))
                attempts.append({"model": model,
                                 "status": response.status_code,
                                 "error": f"HTTP {response.status_code}: "
                                          f"{body_txt}"})
                continue

            return response.json()
        except Exception as e:
            attempts.append({"model": model, "status": None,
                             "error": sanitize_provider_text(
                                 f"{e.__class__.__name__}: {e}")})
            continue

    last = attempts[-1] if attempts else {"error": "no model configured"}
    raise GeminiError(
        f"all Gemini attempts failed ({last.get('error')})",
        attempts=attempts)


def call_gemini_structured(body, model_type='flash', model_name=None, timeout=60):
    """
    PhishFinder structured-output transport — moved verbatim. The caller builds
    `body` (including its response_schema) and parses the result; this owns only
    model resolution, the 2-attempt 503 retry, and the non-OK guard.
    """
    if model_name is None:
        model_name = "gemini-1.5-pro-latest" if model_type == 'pro' else "gemini-1.5-flash-latest"

    headers = {"Content-Type": "application/json"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"

    response = None
    for attempt in range(2):  # Retry logic
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
            if response.status_code != 503:
                response.raise_for_status()
                break
            print(f"⚠️ Received 503 from Gemini, retrying... (Attempt {attempt + 1})")
            time.sleep(1)
        except requests.exceptions.RequestException as e:
            if attempt == 1:
                raise e

    if not response or not response.ok:
        raise Exception(f"Failed to get a successful response from Gemini. Status: {response.status_code if response else 'N/A'}")

    return response.json()
