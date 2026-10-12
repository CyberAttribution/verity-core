"""
verity_core.artifacts.media — Media Evidence Bundle (v0.2.0, future-ready).

A media artifact is never "just a video". A bundle holds references to all
components of a media evidence item; empty fields today are intentional so
signal-level capabilities (vision, waveform, frame extraction) plug in
later WITHOUT schema or UI change. References only — the bundle never
duplicates the original evidence.

Availability of any capability is declared by the workflow registry
(workflows.py), not implied by this schema. A populated `transcript` means
a transcript exists; an empty `derived_frames` means none were extracted,
never that frame analysis ran and found nothing.
"""
from dataclasses import dataclass, field, asdict


@dataclass
class MediaRef:
    """A reference to a media component. `kind` = local_path | url |
    inline_text | manifest. Never the bytes themselves."""
    kind: str = "url"
    value: str = ""
    label: str = ""
    mime: str = ""
    sha256: str = ""            # when a local file was hashed at intake
    retrieval_status: str = ""  # adapter status vocabulary (see workflows)

    def to_dict(self):
        return asdict(self)


@dataclass
class MediaEvidenceBundle:
    """Future-ready container. All list fields may be empty in v0.1."""
    # primary source
    original_media: MediaRef = None       # local original if imported
    media_url: MediaRef = None            # source/post URL
    # textual + structured components (available now)
    transcript: str = ""
    captions: str = ""
    metadata: dict = field(default_factory=dict)      # exif/codec/sidecar
    platform_context: dict = field(default_factory=dict)
    source_account: str = ""
    claimed_date: str = ""
    claimed_place: str = ""
    claimed_context: str = ""
    analyst_notes: str = ""
    # signal-level components (populated when an approved runtime exists)
    derived_frames: list = field(default_factory=list)   # [MediaRef]
    extracted_audio: MediaRef = None
    comparison_media: list = field(default_factory=list)  # [MediaRef]
    # cross-workflow linkage
    related_evidence_ids: list = field(default_factory=list)
    # provenance of the retrieval itself
    adapter: str = ""
    adapter_status: str = ""   # supported|metadata_only|manual_import|blocked|failed
    # v0.5.0 additive: populated by verity_core.media_ingest (S2+).
    # `extraction` holds a media_evidence_package/v1 dict (four-way
    # metadata-origin classification, artifact chain, processing events,
    # observations); the fields below surface its headline provenance.
    extraction: dict = field(default_factory=dict)
    processing_events: list = field(default_factory=list)
    cleanup_status: str = ""
    source_kind: str = ""      # upload | youtube
    limits_applied: dict = field(default_factory=dict)

    def to_dict(self):
        d = {}
        for k, v in asdict(self).items():
            d[k] = v
        # asdict already recursed MediaRef dataclasses; keep None as None
        return d

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        for key in ("original_media", "media_url", "extracted_audio"):
            if isinstance(data.get(key), dict):
                data[key] = MediaRef(**data[key])
        for key in ("derived_frames", "comparison_media"):
            data[key] = [MediaRef(**r) if isinstance(r, dict) else r
                         for r in (data.get(key) or [])]
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def summarize_bundle(bundle) -> dict:
    """Compact display summary: which components are present (never claims
    a capability ran — only what evidence exists)."""
    b = bundle if isinstance(bundle, MediaEvidenceBundle) \
        else MediaEvidenceBundle.from_dict(bundle)
    return {
        "has_original": bool(b.original_media and b.original_media.value),
        "has_url": bool(b.media_url and b.media_url.value),
        "has_transcript": bool(b.transcript.strip()),
        "has_captions": bool(b.captions.strip()),
        "has_metadata": bool(b.metadata),
        "has_frames": bool(b.derived_frames),
        "has_audio": bool(b.extracted_audio and b.extracted_audio.value),
        "has_comparison": bool(b.comparison_media),
        "has_extraction": bool(b.extraction),
        "adapter_status": b.adapter_status or "n/a",
        "claimed_date": b.claimed_date,
        "claimed_place": b.claimed_place,
    }
