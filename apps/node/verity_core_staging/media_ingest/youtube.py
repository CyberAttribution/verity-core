"""
media_ingest.youtube — the first remote-media adapter (S5, approved).

Security posture (approved plan §9):
  * https only; host allow-list (youtube.com/www/m + youtu.be); no IP
    hosts, no userinfo, no nonstandard ports;
  * the USER STRING IS NEVER PASSED TO THE RETRIEVAL TOOL — an 11-char
    video id is extracted and a canonical URL reconstructed from it;
  * playlists, channels, live URLs, and anything unrecognized rejected;
  * metadata probe first (no media), limits enforced before download;
  * no cookies, no authenticated access, bounded size/resolution/time;
  * output template locked inside the caller-supplied directory.

Evidentiary chain (approved S5 requirement — never collapsed): callers
receive the original URL, canonical URL, and video id separately; probe
metadata is retrieval-generated evidence; probe and retrieval are
separate events; the retrieved file is a distinct artifact that is a
platform re-encoded copy, never implied to be the uploader's original.
"""
import json
import os
import re
from urllib.parse import parse_qs, urlparse

from . import tools

# ---- retrieval-reliability configuration (2026.x yt-dlp guidance) ----
# Player-client selection and extractor args are OPERATOR CONFIGURATION,
# never hard-coded tokens. A single deterministic fallback attempt with
# an alternate client runs only on bot_challenge — never aggressive
# retries. PO-token support, when needed, is supplied externally via
# VERITY_YTDLP_EXTRACTOR_ARGS (e.g. a maintained provider's base URL);
# nothing secret ever enters this code or its logs.
#   VERITY_YTDLP_PLAYER_CLIENTS   optional, e.g. "tv,web" (primary)
#   VERITY_YTDLP_FALLBACK_CLIENTS optional, default "tv" (bot_challenge
#                                 fallback; empty string disables)
#   VERITY_YTDLP_EXTRACTOR_ARGS   optional, ';'-separated raw
#                                 --extractor-args values

_URL_RE = re.compile(r"https?://\S+")
_LONGTOKEN_RE = re.compile(r"\S{80,}")


def sanitize_diag(text, cap=300):
    """Strip URLs (which may carry signed params) and long token-like
    strings from diagnostic text; bound length."""
    t = _URL_RE.sub("[url]", str(text or ""))
    t = _LONGTOKEN_RE.sub("[redacted]", t)
    return t[:cap]


def _client_args(clients):
    return ["--extractor-args", f"youtube:player_client={clients}"] \
        if clients else []


def _cookie_args(cookie_file):
    """--cookies FILE when an authenticated pass is requested. The file
    is caller-managed (written from a secret at startup); this module
    only references its path, never its contents."""
    return ["--cookies", cookie_file] if cookie_file else []


def _configured_extractor_args():
    out = []
    raw = os.environ.get("VERITY_YTDLP_EXTRACTOR_ARGS", "")
    for arg in raw.split(";"):
        arg = arg.strip()
        if arg:
            out += ["--extractor-args", arg]
    return out


def _client_plan():
    """[(label, client-string-or-empty), ...] — primary then a small
    deterministic sequence of anonymous fallback clients used ONLY on
    bot_challenge. Default sequence: yt-dlp defaults -> tv -> mweb.
    Bounded (max 3 attempts), never unbounded retries."""
    primary = os.environ.get("VERITY_YTDLP_PLAYER_CLIENTS", "").strip()
    fallbacks = os.environ.get("VERITY_YTDLP_FALLBACK_CLIENTS",
                               "tv,mweb")
    plan = [(primary or "default", primary)]
    seen = {primary}
    for fb in (fallbacks or "").split(","):
        fb = fb.strip()
        if fb and fb not in seen:
            plan.append((fb, fb))
            seen.add(fb)
    return plan[:3]

_ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                  "youtu.be"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_CATEGORY_PATTERNS = (
    ("bot_challenge", ("not a bot", "confirm your identity",
                       "unusual traffic", "captcha")),
    ("geo_restricted", ("not available in your country",
                        "video is not available in your location",
                        "blocked in your country")),
    ("age_restricted", ("age-restricted", "age restricted",
                        "confirm your age", "inappropriate for some")),
    ("copyright_restricted", ("copyright", "blocked it on copyright",
                              "contains content from")),
    ("unavailable", ("private video", "video unavailable",
                     "has been removed", "no longer available",
                     "account associated with this video has been",
                     "does not exist")),
    ("network_failure", ("unable to download webpage",
                         "name or service not known",
                         "temporary failure in name resolution",
                         "connection refused", "network is unreachable")),
)


class YouTubeAdapter:
    provider = "youtube"
    tool_name = "yt-dlp"

    # ---- deterministic classification (no network) ----
    def classify(self, url):
        out = {"supported": False, "provider": self.provider,
               "provider_match": False, "media_id": "",
               "canonical_url": "", "reject_reason": ""}
        try:
            p = urlparse(str(url or "").strip())
        except ValueError:
            out["reject_reason"] = "URL could not be parsed."
            return out
        host = (p.hostname or "").lower()
        base_host = host[4:] if host.startswith("www.") else host
        if base_host not in ("youtube.com", "m.youtube.com", "youtu.be") \
                and host not in _ALLOWED_HOSTS:
            out["reject_reason"] = "Not a supported YouTube URL."
            return out
        out["provider_match"] = True
        if p.scheme != "https":
            out["reject_reason"] = "Only https YouTube URLs are accepted."
            return out
        if p.username or p.password or p.port not in (None, 443):
            out["reject_reason"] = "URL form not accepted."
            return out
        qs = parse_qs(p.query or "")
        path = p.path or "/"
        if path.startswith("/playlist") or ("list" in qs and "v" not in qs):
            out["reject_reason"] = ("Playlists are not supported; submit "
                                    "a single video URL.")
            return out
        if path.startswith(("/channel/", "/user/", "/c/", "/@")):
            out["reject_reason"] = ("Channel pages are not supported; "
                                    "submit a single video URL.")
            return out
        if path.startswith("/live"):
            out["reject_reason"] = "Live streams are not supported."
            return out
        vid = ""
        if host == "youtu.be":
            vid = path.lstrip("/").split("/")[0]
        elif path == "/watch":
            vid = (qs.get("v") or [""])[0]
        elif path.startswith("/shorts/"):
            vid = path.split("/shorts/", 1)[1].split("/")[0]
        elif path.startswith("/embed/"):
            vid = path.split("/embed/", 1)[1].split("/")[0]
        if not _ID_RE.match(vid or ""):
            out["reject_reason"] = ("Could not extract a valid video id "
                                    "from the URL.")
            return out
        out.update(supported=True, media_id=vid,
                   canonical_url=f"https://www.youtube.com/watch?v={vid}")
        return out

    # ---- failure categorization ----
    @staticmethod
    def categorize_failure(text, timed_out=False, tool_missing=False):
        if tool_missing:
            return "tool_missing"
        if timed_out:
            return "timeout"
        low = (text or "").lower()
        for category, needles in _CATEGORY_PATTERNS:
            if any(n in low for n in needles):
                return category
        return "retrieval_error"

    # ---- metadata probe (no media) ----
    @staticmethod
    def _probe_once_argv(canonical_url, clients, cookie_file=None):
        return (["yt-dlp", "--no-playlist", "--skip-download",
                 "--dump-single-json", "--no-warnings",
                 "--socket-timeout", "15", "--retries", "1",
                 "--no-cache-dir"]
                + _cookie_args(cookie_file)
                + _client_args(clients) + _configured_extractor_args()
                + ["--", canonical_url])

    def _probe_plan(self, canonical_url, timeout_s, cookie_file=None,
                    authenticated=False):
        """Run the bounded client plan once (anonymous or authenticated).
        Returns (out, attempts). --cookies never exposes contents; the
        diag argv shows the flag + path only."""
        attempts = []
        out = None
        for label, clients in _client_plan():
            argv = self._probe_once_argv(canonical_url, clients,
                                         cookie_file)
            out = tools.run_tool(argv, timeout_s=timeout_s)
            entry = {"client": label, "authenticated": authenticated,
                     "argv": sanitize_diag(" ".join(argv), cap=400)}
            if out.get("ok"):
                entry["outcome"] = "ok"
                attempts.append(entry)
                break
            entry["outcome"] = self.categorize_failure(
                (out.get("stderr") or "") + (out.get("error") or ""),
                timed_out="timed out" in (out.get("error") or ""),
                tool_missing=bool(out.get("tool_missing")))
            attempts.append(entry)
            if entry["outcome"] != "bot_challenge":
                break              # fallback client only for bot checks
        return out, attempts

    def probe(self, canonical_url, timeout_s=60, cookie_file=None):
        tool = {"name": self.tool_name,
                "version": tools.tool_version("yt-dlp")}
        out, attempts = self._probe_plan(canonical_url, timeout_s)
        # authenticated escalation: only after an anonymous bot_challenge,
        # only if a cookie file is available (approved requirement 4/5).
        if (not out.get("ok") and attempts
                and attempts[-1]["outcome"] == "bot_challenge"
                and cookie_file):
            out2, att2 = self._probe_plan(canonical_url, timeout_s,
                                          cookie_file=cookie_file,
                                          authenticated=True)
            attempts += att2
            if out2.get("ok"):
                out = out2
        if not out.get("ok"):
            return {"ok": False, "tool": tool,
                    "category": attempts[-1]["outcome"],
                    "attempts": attempts,
                    "error": sanitize_diag(out.get("stderr")
                                           or out.get("error")
                                           or "probe failed")}
        try:
            data = json.loads(out.get("stdout") or "null") or {}
        except ValueError:
            return {"ok": False, "tool": tool,
                    "category": "retrieval_error", "attempts": attempts,
                    "error": "probe returned non-JSON metadata"}
        md = {
            "video_id": str(data.get("id") or "")[:20],
            "title": str(data.get("title") or "")[:300],
            "uploader": str(data.get("uploader") or "")[:200],
            "channel_id": str(data.get("channel_id") or "")[:60],
            "upload_date": str(data.get("upload_date") or "")[:16],
            "duration_s": data.get("duration"),
            "is_live": bool(data.get("is_live")),
            "was_live": bool(data.get("was_live")),
            "availability": str(data.get("availability") or "")[:40],
            "description_excerpt": str(data.get("description")
                                       or "")[:2000],
            "view_count": data.get("view_count"),
            "resolution_max": data.get("height"),
        }
        return {"ok": True, "tool": tool, "metadata": md,
                "attempts": attempts}

    # ---- bounded retrieval ----
    def _retrieve_plan(self, canonical_url, dest_dir, size_mb, fmt,
                       match_filter, timeout_s, cookie_file=None,
                       authenticated=False):
        """Run the bounded client plan once. Returns (ok_result_or_None,
        attempts, last_category, last_out)."""
        attempts = []
        out = None
        category = "retrieval_error"
        for label, clients in _client_plan():
            argv = (["yt-dlp", "--no-playlist", "--no-warnings",
                     "--socket-timeout", "15", "--retries", "2",
                     "--no-cache-dir", "--max-filesize", f"{size_mb}M",
                     "--match-filters", match_filter,
                     "-f", fmt, "--no-part",
                     "-o", os.path.join(dest_dir, "source.%(ext)s")]
                    + _cookie_args(cookie_file)
                    + _client_args(clients)
                    + _configured_extractor_args()
                    + ["--", canonical_url])
            argv_diag = sanitize_diag(" ".join(argv), cap=400)
            out = tools.run_tool(argv, timeout_s=timeout_s)
            files = [n for n in sorted(os.listdir(dest_dir))
                     if n.startswith("source.")]
            if out.get("ok") and files:
                attempts.append({"client": label, "outcome": "ok",
                                 "authenticated": authenticated,
                                 "argv": argv_diag})
                return files[0], attempts, "ok", out
            for n in files:                # cleanup partial before retry
                try:
                    os.unlink(os.path.join(dest_dir, n))
                except OSError:
                    pass
            stderr = (out.get("stderr") or "") + (out.get("error") or "")
            category = self.categorize_failure(
                stderr,
                timed_out="timed out" in (out.get("error") or ""),
                tool_missing=bool(out.get("tool_missing")))
            if "max-filesize" in stderr.lower() or \
                    "file is larger than max" in stderr.lower():
                category = "oversize"
            low = stderr.lower()
            if ("does not pass filter" in low or
                    "skipping" in low and "match-filter" in low):
                category = "over_duration" if "duration" in low \
                    else "live_stream"
            attempts.append({"client": label, "outcome": category,
                             "authenticated": authenticated,
                             "argv": argv_diag})
            if category != "bot_challenge":
                break              # fallback client only for bot checks
        return None, attempts, category, out

    def retrieve(self, canonical_url, dest_dir, limits=None,
                 timeout_s=300, cookie_file=None):
        """limits: {max_filesize_mb, max_resolution, max_duration_s}.
        Writes source.<ext> inside dest_dir only. Anonymous first; on a
        bot_challenge, retries the same bounded plan with cookies (+ the
        existing PO-token provider) when a cookie file is available."""
        limits = limits or {}
        tool = {"name": self.tool_name,
                "version": tools.tool_version("yt-dlp")}
        os.makedirs(dest_dir, exist_ok=True)
        height = int(limits.get("max_resolution") or 720)
        size_mb = int(limits.get("max_filesize_mb") or 100)
        fmt = (f"bv*[height<={height}]+ba/b[height<={height}]/b")
        # Tool-side governance filters (approved D: duration/live limits
        # stay enforced even when the probe was bypassed — never weakened).
        filters = ["!is_live"]
        max_dur = limits.get("max_duration_s")
        if max_dur:
            filters.append(f"duration<={int(max_dur)}")
        match_filter = " & ".join(filters)

        fname, attempts, category, out = self._retrieve_plan(
            canonical_url, dest_dir, size_mb, fmt, match_filter,
            timeout_s)
        if fname is None and category == "bot_challenge" and cookie_file:
            fname, att2, category, out = self._retrieve_plan(
                canonical_url, dest_dir, size_mb, fmt, match_filter,
                timeout_s, cookie_file=cookie_file, authenticated=True)
            attempts += att2
        if fname is not None:
            return {"ok": True, "tool": tool, "filename": fname,
                    "attempts": attempts}
        stderr = (out.get("stderr") or "") + (out.get("error") or "")
        return {"ok": False, "tool": tool, "category": category,
                "attempts": attempts,
                "error": sanitize_diag(stderr) or "retrieval failed"}
