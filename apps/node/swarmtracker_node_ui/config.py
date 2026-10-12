"""
SwarmTracker Node UI v0.1 — configuration.

Everything is env-overridable so Hardware / Model Runtime read as
configurable status fields, not hardcoded facts about the M1.
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
# Assumes the package lives inside the OpenClaw project root (~/openclaw-v0).
PROJECT_ROOT = PKG_DIR.parent

OPENCLAW_URL = os.environ.get("NODE_UI_OPENCLAW_URL", "http://127.0.0.1:8090")
OLLAMA_URL = os.environ.get("NODE_UI_OLLAMA_URL", "http://127.0.0.1:11434")
LEDGER_PATH = Path(os.environ.get(
    "NODE_UI_LEDGER", str(PROJECT_ROOT / "logs" / "openclaw_events.jsonl")))

DEFAULT_MODEL = os.environ.get("NODE_UI_MODEL", "qwen2.5:7b-instruct")
OPERATOR = os.environ.get("NODE_UI_OPERATOR", "Kevin")
KEEL_POLICY = os.environ.get("NODE_UI_POLICY", "read_only_analysis")

NODE_NAME = os.environ.get("NODE_UI_NODE", "macmini-m1")
HARDWARE = os.environ.get("NODE_UI_HARDWARE", "Apple M1 Mac mini (prototype)")
HARDWARE_NEXT = os.environ.get(
    "NODE_UI_HARDWARE_NEXT", "Tiiny local AI device (target, Aug 2026)")
RUNTIME_LABEL = os.environ.get("NODE_UI_RUNTIME", "Ollama (local)")

# v0.1.x has no cloud escalation path implemented, so the mode is Local Only.
MODE = "Local Only"
CLOUD_ESCALATION = "Disabled"

# Keel policy display facts for the trace panel (v0.1.1). Display-only:
# the runtime itself has no tool execution; these state what the policy
# permits and denies for this workflow.
DATA_BOUNDARY = "Local node only"
TOOLS_ALLOWED = ["generate_text"]
TOOLS_DENIED = ["shell_exec", "file_write", "email_send", "browser_login"]
EVIDENCE_ROLE = "untrusted specimen"

GENERATE_TIMEOUT = float(os.environ.get("NODE_UI_GENERATE_TIMEOUT", "180"))
HEALTH_TIMEOUT = float(os.environ.get("NODE_UI_HEALTH_TIMEOUT", "2"))

# Governed cloud provider (v0.4.0). Vertex AI via Application Default
# Credentials — no API key. All values are env-overridable.
CLOUD_PROVIDER = os.environ.get("NODE_CLOUD_PROVIDER", "vertex")
VERTEX_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION",
                                 os.environ.get("VERTEX_LOCATION",
                                                "global"))


def assert_local(url: str) -> str:
    """Refuse any non-loopback base URL. This UI never calls out."""
    if not (url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
        raise ValueError(f"Non-local URL refused by SwarmTracker Node UI: {url}")
    return url


assert_local(OPENCLAW_URL)
assert_local(OLLAMA_URL)
