"""
verity_core.artifacts.schemas — shared evidence/report schemas (v0.1.5).

Plain dataclasses with dict round-trip. Workflow-neutral: consumed by
SwarmTracker Node, public Verity, PhishFinder, and future adapters
(Deepfake Defender, Origin workflows). No framework imports.
"""
import hashlib
import time
import uuid
from dataclasses import dataclass, field, asdict

RISK_LEVELS = ("low", "medium", "high", "unknown")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass
class EvidenceArtifact:
    """One piece of untrusted specimen evidence, normalized at intake."""
    id: str = ""
    label: str = ""                 # operator-facing name
    evidence_type: str = "freeform_text"
    event_category: str = ""
    text: str = ""
    claimed_sender: str = ""
    claimed_org: str = ""
    requested_action: str = ""
    source: str = "manual"          # manual | sample | upload | api
    sha256: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = _new_id("art")
        if not self.sha256 and self.text:
            self.sha256 = hashlib.sha256(
                self.text.encode("utf-8", "replace")).hexdigest()

    def to_dict(self):
        return asdict(self)


@dataclass
class IndicatorSet:
    """Deterministically extracted observables (extraction, not judgment)."""
    domains: list = field(default_factory=list)
    ips: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    urls: list = field(default_factory=list)

    def all(self) -> list:
        return self.domains + self.ips + self.emails + self.urls

    def to_dict(self):
        return asdict(self)


@dataclass
class ArtifactAnalysis:
    """Structured per-artifact model output + deterministic indicators."""
    artifact_id: str = ""
    risk_level: str = "unknown"
    confidence: int = 0
    verdict: str = ""
    signals: list = field(default_factory=list)
    recommended_action: str = ""
    org_questions: list = field(default_factory=list)
    indicators: IndicatorSet = field(default_factory=IndicatorSet)
    extra: dict = field(default_factory=dict)   # profile/evidence-type fields
    event_id: str = ""                          # governed-runtime ledger id
    raw_response: str = ""
    parsed: bool = False

    def to_dict(self):
        d = asdict(self)
        return d


@dataclass
class AttributionHypothesis:
    """Bounded hypothesis. Never a named actor."""
    hypothesis: str = ""
    campaign_type: str = "unknown"
    confidence: int = 0
    supporting_evidence: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class CampaignIndicator:
    """Within-submission cluster. Durable ID for future Origin adoption."""
    campaign_id: str = ""
    artifact_ids: list = field(default_factory=list)
    shared_indicators: list = field(default_factory=list)
    basis: str = ""                 # e.g. "shared infrastructure"
    confidence: int = 0

    def __post_init__(self):
        if not self.campaign_id:
            seed = ",".join(sorted(self.artifact_ids)) + "|" + \
                ",".join(sorted(self.shared_indicators))
            self.campaign_id = "CAMP-" + hashlib.sha256(
                seed.encode()).hexdigest()[:8].upper()

    def to_dict(self):
        return asdict(self)


@dataclass
class Entity:
    """Atlas graph node."""
    id: str = ""
    type: str = "artifact"   # artifact|domain|ip|email|url|sender|organization|campaign
    label: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Relationship:
    """Atlas graph edge."""
    source: str = ""
    target: str = ""
    relation: str = "references"
    artifact_ids: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class FusedReport:
    """The one report schema shared by Node and public Verity."""
    report_id: str = ""
    created: float = 0.0
    profile_mode: str = "smb"
    engine: str = "verity_core.artifacts v0.1.5"
    overall_risk: str = "unknown"
    overall_confidence: int = 0
    fused_verdict: str = ""
    corroborations: list = field(default_factory=list)
    contradictions: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    attribution: list = field(default_factory=list)     # AttributionHypothesis
    campaigns: list = field(default_factory=list)       # CampaignIndicator
    entities: list = field(default_factory=list)        # Entity
    relationships: list = field(default_factory=list)   # Relationship
    artifact_analyses: list = field(default_factory=list)  # ArtifactAnalysis
    limitations: list = field(default_factory=list)
    fusion_event_id: str = ""
    fusion_parsed: bool = False

    def __post_init__(self):
        if not self.report_id:
            self.report_id = _new_id("rpt")
        if not self.created:
            self.created = time.time()

    def to_dict(self):
        return {
            **{k: v for k, v in asdict(self).items()
               if k not in ("attribution", "campaigns", "entities",
                            "relationships", "artifact_analyses")},
            "attribution": [a.to_dict() if hasattr(a, "to_dict") else a
                            for a in self.attribution],
            "campaigns": [c.to_dict() if hasattr(c, "to_dict") else c
                          for c in self.campaigns],
            "entities": [e.to_dict() if hasattr(e, "to_dict") else e
                         for e in self.entities],
            "relationships": [r.to_dict() if hasattr(r, "to_dict") else r
                              for r in self.relationships],
            "artifact_analyses": [a.to_dict() if hasattr(a, "to_dict") else a
                                  for a in self.artifact_analyses],
        }
