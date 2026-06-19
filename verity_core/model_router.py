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


def call_gemini(body, headers=None, timeout=60):
    """
    Helper to call Gemini with a preferred model list and graceful fallback.
    Tries each model in PREFERRED_GEMINI_MODELS until one succeeds or all fail.
    (Verity behavior — moved verbatim.)
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")

    if headers is None:
        headers = {"Content-Type": "application/json"}

    last_error = None

    for model in PREFERRED_GEMINI_MODELS:
        api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        try:
            print(f"GEMINI CALL: Attempting model '{model}'")
            response = requests.post(api_url, headers=headers, json=body, timeout=timeout)

            # Explicitly handle 404 model not found for smooth fallback
            if response.status_code == 404:
                print(f"GEMINI CALL: Model '{model}' returned 404 (not found), trying next if available.")
                last_error = ValueError(f"Model '{model}' not found (404).")
                continue

            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"GEMINI CALL ERROR with model '{model}': {e}")
            last_error = e
            continue

    # If we exhausted the list and still failed:
    if last_error:
        raise last_error
    raise RuntimeError("No usable Gemini model configured or all calls failed.")


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
