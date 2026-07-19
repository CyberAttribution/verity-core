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
    def probe(self, canonical_url, timeout_s=60):
        tool = {"name": self.tool_name,
                "version": tools.tool_version("yt-dlp")}
        out = tools.run_tool(
            ["yt-dlp", "--no-playlist", "--skip-download",
             "--dump-single-json", "--no-warnings",
             "--socket-timeout", "15", "--retries", "1",
             "--no-cache-dir", "--", canonical_url],
            timeout_s=timeout_s)
        if not out.get("ok"):
            category = self.categorize_failure(
                (out.get("stderr") or "") + (out.get("error") or ""),
                timed_out="timed out" in (out.get("error") or ""),
                tool_missing=bool(out.get("tool_missing")))
            return {"ok": False, "tool": tool, "category": category,
                    "error": (out.get("stderr") or out.get("error")
                              or "probe failed")[:300]}
        try:
            data = json.loads(out.get("stdout") or "null") or {}
        except ValueError:
            return {"ok": False, "tool": tool,
                    "category": "retrieval_error",
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
        return {"ok": True, "tool": tool, "metadata": md}

    # ---- bounded retrieval ----
    def retrieve(self, canonical_url, dest_dir, limits=None,
                 timeout_s=300):
        """limits: {max_filesize_mb, max_resolution}. Writes
        source.<ext> inside dest_dir only."""
        limits = limits or {}
        tool = {"name": self.tool_name,
                "version": tools.tool_version("yt-dlp")}
        os.makedirs(dest_dir, exist_ok=True)
        height = int(limits.get("max_resolution") or 720)
        size_mb = int(limits.get("max_filesize_mb") or 100)
        fmt = (f"bv*[height<={height}]+ba/b[height<={height}]/b")
        out = tools.run_tool(
            ["yt-dlp", "--no-playlist", "--no-warnings",
             "--socket-timeout", "15", "--retries", "2",
             "--no-cache-dir", "--max-filesize", f"{size_mb}M",
             "-f", fmt, "--no-part",
             "-o", os.path.join(dest_dir, "source.%(ext)s"),
             "--", canonical_url],
            timeout_s=timeout_s)
        files = [n for n in sorted(os.listdir(dest_dir))
                 if n.startswith("source.")]
        if out.get("ok") and files:
            return {"ok": True, "tool": tool, "filename": files[0]}
        # cleanup partial output on failure
        for n in files:
            try:
                os.unlink(os.path.join(dest_dir, n))
            except OSError:
                pass
        stderr = (out.get("stderr") or "") + (out.get("error") or "")
        category = self.categorize_failure(
            stderr, timed_out="timed out" in (out.get("error") or ""),
            tool_missing=bool(out.get("tool_missing")))
        if "max-filesize" in stderr.lower() or \
                "file is larger than max" in stderr.lower():
            category = "oversize"
        return {"ok": False, "tool": tool, "category": category,
                "error": stderr[:300] or "retrieval failed"}
