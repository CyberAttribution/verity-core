"""
media_ingest.tools — safe external-tool invocation + hashing.

Security posture (approved plan §9): every external tool runs via
subprocess with a LIST argv (shell=False — command injection is
structurally impossible), a wall-clock timeout, bounded captured output,
and `--` end-of-options separators supplied by callers where the tool
supports them. Paths are always caller-constructed (server-generated
filenames), never user strings.
"""
import hashlib
import json
import shutil
import subprocess
import time

MAX_OUTPUT_BYTES = 4_000_000     # bound captured stdout/stderr
_VERSION_CACHE = {}

_VERSION_ARGS = {
    "ffprobe": ["-version"],
    "ffmpeg": ["-version"],
    "exiftool": ["-ver"],
}


def available(name: str) -> bool:
    return shutil.which(name) is not None


def run_tool(argv, timeout_s=60, max_output_bytes=MAX_OUTPUT_BYTES):
    """Run argv (a list; never a string). Returns
    {ok, returncode, stdout, stderr, duration_s} or {ok: False, error}.
    Never raises on tool failure/timeout/absence."""
    if not isinstance(argv, (list, tuple)) or not argv:
        return {"ok": False, "error": "argv must be a non-empty list"}
    argv = [str(a) for a in argv]
    if not available(argv[0]):
        return {"ok": False, "error": f"{argv[0]} not available",
                "tool_missing": True}
    started = time.time()
    try:
        proc = subprocess.run(
            argv, shell=False, capture_output=True,
            timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{argv[0]} timed out after "
                                      f"{timeout_s}s",
                "duration_s": round(time.time() - started, 2)}
    except OSError as e:
        return {"ok": False, "error": f"{argv[0]} failed to start: "
                                      f"{e.__class__.__name__}"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[:max_output_bytes].decode("utf-8", "replace"),
        "stderr": proc.stderr[:8192].decode("utf-8", "replace"),
        "duration_s": round(time.time() - started, 2),
    }


def tool_version(name: str):
    """First line of the tool's version output, or None. Cached."""
    if name in _VERSION_CACHE:
        return _VERSION_CACHE[name]
    args = _VERSION_ARGS.get(name)
    version = None
    if args:
        out = run_tool([name] + args, timeout_s=10)
        if out.get("ok"):
            first = (out.get("stdout") or "").strip().splitlines()
            version = first[0][:120] if first else None
    _VERSION_CACHE[name] = version
    return version


def run_json_tool(argv, timeout_s=60):
    """run_tool + parse stdout as JSON. {ok, data} or {ok: False, error}."""
    out = run_tool(argv, timeout_s=timeout_s)
    if not out.get("ok"):
        return out
    try:
        out["data"] = json.loads(out.pop("stdout") or "null")
    except ValueError:
        return {"ok": False, "error": f"{argv[0]} produced non-JSON output"}
    return out


def sha256_file(path, chunk=1024 * 256):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
