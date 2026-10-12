"""
verity_core.artifacts.workflows — shared workflow registry (v0.2.0).

A modest registry so screens are not hard-coded as generic Verity. Each
entry declares only capabilities that genuinely exist; a declared runtime
capability the current runtime lacks is reported as NOT operational rather
than implied to work. Media Evidence is a first-class workflow whose
currently available capabilities are textual/contextual/deterministic;
signal-level capabilities are declared `future` so they plug in later
without schema or UI change.
"""
from dataclasses import dataclass, field, asdict

# adapter retrieval status vocabulary (honest, five values)
ADAPTER_STATUSES = ("supported", "metadata_only", "manual_import",
                    "blocked", "failed")


@dataclass
class Capability:
    key: str
    label: str
    availability: str   # available | future | unavailable

    def to_dict(self):
        return asdict(self)


@dataclass
class Workflow:
    id: str
    display_name: str
    tagline: str
    artifact_types: list = field(default_factory=list)
    local_available: bool = True
    hosted_available: bool = False
    report_extensions: list = field(default_factory=list)
    ui_panels: list = field(default_factory=list)
    capabilities: list = field(default_factory=list)   # [Capability]
    keel_policy_profile: str = "read_only_analysis"
    network_retrieval: bool = False
    retention: str = "session_or_project"
    human_review: str = "optional"

    def to_dict(self):
        d = asdict(self)
        d["capabilities"] = [c if isinstance(c, dict) else c.to_dict()
                             for c in self.capabilities]
        return d

    def available_capabilities(self):
        return [c for c in self.capabilities
                if (c.availability if hasattr(c, "availability")
                    else c["availability"]) == "available"]


_MEDIA_CAPS = [
    Capability("transcripts", "Transcript analysis", "available"),
    Capability("captions", "Caption analysis", "available"),
    Capability("metadata", "Metadata & codec-history review", "available"),
    Capability("provenance", "Provenance / manifest review", "available"),
    Capability("platform_context", "Platform & post context", "available"),
    Capability("claimed_time_place", "Claimed time/place consistency",
               "available"),
    Capability("narrative", "Narrative framing analysis", "available"),
    Capability("incentives",
               "Economic/social/behavioral/operational incentives",
               "available"),
    Capability("deterministic_checks",
               "Deterministic technical checks (hash, sidecar, time delta)",
               "available"),
    Capability("visual_forensics", "Visual / frame signal analysis",
               "future"),
    Capability("audio_forensics", "Audio / waveform signal analysis",
               "future"),
    Capability("synthesis_detection", "Synthesis / deepfake signal scoring",
               "future"),
]

REGISTRY = {
    "general_evidence": Workflow(
        id="general_evidence", display_name="General Evidence",
        tagline="Cross-vector analysis of communications, logs, and business "
                "evidence.",
        artifact_types=["email_message", "email_headers", "url_domain",
                        "authentication_log", "dns_log", "firewall_proxy_log",
                        "endpoint_edr_alert", "vendor_payment_request",
                        "ticket_incident_note", "freeform_text"],
        local_available=True, hosted_available=True,
        ui_panels=["fused_report", "attribution", "campaigns", "atlas"],
        capabilities=[Capability("multi_artifact_fusion",
                                 "Multi-artifact fusion", "available"),
                      Capability("attribution", "Bounded attribution",
                                 "available")],
        network_retrieval=False),
    "communications_phishing": Workflow(
        id="communications_phishing", display_name="Communications & Phishing",
        tagline="Suspicious messages, senders, and payment-change lures.",
        artifact_types=["email_message", "email_headers", "url_domain",
                        "vendor_payment_request", "freeform_text"],
        local_available=True, hosted_available=True,
        ui_panels=["fused_report", "attribution", "atlas"],
        capabilities=[Capability("sender_analysis", "Sender/domain analysis",
                                 "available")],
        network_retrieval=False),
    "media_authenticity": Workflow(
        id="media_authenticity", display_name="Media Evidence",
        tagline="Deepfake Defender™ — media as technical, contextual, "
                "narrative, economic, social, and behavioral evidence.",
        artifact_types=["media_bundle", "transcript", "captions",
                        "media_metadata", "media_url", "freeform_text"],
        local_available=True, hosted_available=True,
        report_extensions=["media_evidence_assessment"],
        ui_panels=["media_preview", "authenticity_findings",
                   "source_provenance", "narrative_incentives", "atlas"],
        capabilities=_MEDIA_CAPS,
        keel_policy_profile="read_only_analysis",
        network_retrieval=True,   # governed; adapters metadata-only
        retention="project", human_review="recommended"),
    "emerging_technology": Workflow(
        id="emerging_technology", display_name="Emerging Technology",
        tagline="Vendor security claims, post-quantum/HNDL, crypto migration.",
        artifact_types=["freeform_text", "vendor_security_claim"],
        local_available=True, hosted_available=True,
        ui_panels=["fused_report", "atlas"],
        capabilities=[Capability("claim_triage", "Claim triage", "available"),
                      Capability("crypto_signal_analysis",
                                 "Cryptographic signal analysis", "future")],
        network_retrieval=False),
}


def get(workflow_id):
    return REGISTRY.get(workflow_id, REGISTRY["general_evidence"])


def list_workflows(hosted=False, local=False):
    out = []
    for wf in REGISTRY.values():
        if hosted and not wf.hosted_available:
            continue
        if local and not wf.local_available:
            continue
        out.append(wf)
    return out


def display_name(workflow_id):
    return get(workflow_id).display_name
