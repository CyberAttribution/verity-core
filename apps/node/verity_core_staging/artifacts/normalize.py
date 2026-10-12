"""
verity_core.artifacts.normalize — evidence intake normalization.

Deterministic only: typing, envelope creation, indicator extraction.
Extraction is observation, not judgment — every indicator feeds fusion
and the Atlas graph with provenance back to its artifact.
"""
import re

from .schemas import EvidenceArtifact, IndicatorSet

_URL_RE = re.compile(r"""https?://[^\s"'<>)\]}]+""", re.I)
_EMAIL_RE = re.compile(
    r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,24}", re.I)
_IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:[a-z]{2,24})\b", re.I)

# Noise domains that appear in log formats / boilerplate, not evidence.
_DOMAIN_STOP = {"w3.org", "schemas.microsoft.com"}


def make_artifact(text: str, *, label: str = "", evidence_type: str = "freeform_text",
                  event_category: str = "", claimed_sender: str = "",
                  claimed_org: str = "", requested_action: str = "",
                  source: str = "manual") -> EvidenceArtifact:
    text = (text or "").strip()
    return EvidenceArtifact(
        label=label.strip() or (text[:48] + "…" if len(text) > 48 else text),
        evidence_type=evidence_type, event_category=event_category,
        text=text, claimed_sender=claimed_sender.strip(),
        claimed_org=claimed_org.strip(),
        requested_action=requested_action.strip(), source=source)


def _domain_of(value: str) -> str:
    return value.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()


def extract_indicators(text: str, claimed_sender: str = "") -> IndicatorSet:
    """Pull observable infrastructure out of specimen text."""
    text = text or ""
    urls = sorted({u.rstrip(".,;:!?") for u in _URL_RE.findall(text)})
    emails = sorted({e.lower() for e in _EMAIL_RE.findall(text)})
    if claimed_sender and _EMAIL_RE.fullmatch(claimed_sender.strip()):
        emails = sorted(set(emails) | {claimed_sender.strip().lower()})
    ips = sorted(set(_IP_RE.findall(text)))

    domains = set()
    for m in _DOMAIN_RE.findall(text):
        d = m.lower()
        # exclude bare IPs matched as domains and version-like tokens
        if _IP_RE.fullmatch(d) or d.replace(".", "").isdigit():
            continue
        if d in _DOMAIN_STOP:
            continue
        domains.add(d)
    # fold URL hosts and email domains in (canonical infrastructure view)
    for u in urls:
        host = _domain_of(u.split("//", 1)[-1])
        if host and not _IP_RE.fullmatch(host):
            domains.add(host)
    for e in emails:
        domains.add(_domain_of(e))
    # drop domains that are merely prefixes of longer captured hosts? keep all —
    # subdomain + registrable domain are both useful graph nodes; dedupe exact.
    return IndicatorSet(domains=sorted(domains), ips=ips,
                        emails=emails, urls=urls)
