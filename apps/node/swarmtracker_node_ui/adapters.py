"""
adapters — media platform adapter status reporting (v0.2.0, honest).

This module does NOT download media and does NOT scrape behind auth. It
reports, per platform, what a governed metadata-only retrieval could do,
using the five-status vocabulary. Actual network retrieval is gated by
Keel (network_allowed); in Local Only / Disconnected mode every adapter
returns a disabled status and nothing is fetched.

Statuses: supported | metadata_only | manual_import | blocked | failed
"""
import re
from urllib.parse import urlparse

# Per-platform capability posture (metadata-only everywhere this release).
PLATFORMS = {
    "youtube.com": ("YouTube", "metadata_only",
                    "Public oEmbed metadata (title, author, thumbnail). "
                    "Media is not downloaded."),
    "youtu.be": ("YouTube", "metadata_only", "Public oEmbed metadata."),
    "tiktok.com": ("TikTok", "metadata_only",
                   "Public oEmbed metadata where available."),
    "reddit.com": ("Reddit", "metadata_only",
                   "Public post JSON metadata."),
    "bsky.app": ("Bluesky", "metadata_only",
                 "Public API post metadata + media references."),
    "mastodon.social": ("Mastodon", "metadata_only",
                        "Public API status metadata."),
    "x.com": ("X", "metadata_only",
              "oEmbed where available; often restricted."),
    "twitter.com": ("X", "metadata_only",
                    "oEmbed where available; often restricted."),
    "facebook.com": ("Facebook", "manual_import",
                     "Authentication-restricted; import media locally."),
    "instagram.com": ("Instagram", "manual_import",
                      "Authentication-restricted; import media locally."),
    "threads.net": ("Threads", "manual_import",
                    "Authentication-restricted; import media locally."),
    "linkedin.com": ("LinkedIn", "manual_import",
                     "Authentication-restricted; import media locally."),
    "snapchat.com": ("Snapchat", "manual_import",
                     "Authentication-restricted; import media locally."),
}

DIRECT_MEDIA_RE = re.compile(
    r"\.(mp4|mov|webm|mkv|avi|mp3|wav|m4a|ogg|jpg|jpeg|png|gif|webp)"
    r"($|\?)", re.I)

STATUS_LABEL = {
    "supported": "Supported",
    "metadata_only": "Metadata only",
    "manual_import": "Import media locally",
    "blocked": "Blocked by platform",
    "failed": "Retrieval failed",
}


def _host(url):
    try:
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def classify(url, network_allowed=False):
    """Classify a submitted media URL WITHOUT fetching it. Returns a dict:
    platform, status, note, network_allowed. Never performs I/O."""
    url = (url or "").strip()
    if not url:
        return {"platform": "", "status": "failed", "note": "No URL given.",
                "network_allowed": network_allowed}
    host = _host(url)
    if DIRECT_MEDIA_RE.search(url):
        platform, status, note = ("Direct media URL", "metadata_only",
                                  "Direct media link: metadata (type/size) "
                                  "only; media is not downloaded.")
    else:
        match = None
        for domain, meta in PLATFORMS.items():
            if host == domain or host.endswith("." + domain):
                match = meta
                break
        if match:
            platform, status, note = match
        else:
            platform, status, note = (host or "Unknown source",
                                      "metadata_only",
                                      "Unrecognized source: treated as a "
                                      "metadata-only reference.")
    if not network_allowed:
        # Keel Local Only / Disconnected: nothing is fetched.
        note = ("Local Only mode: no remote retrieval performed. " + note)
        if status == "metadata_only":
            status = "manual_import"
    return {"platform": platform, "status": status, "note": note,
            "status_label": STATUS_LABEL.get(status, status),
            "network_allowed": network_allowed}
