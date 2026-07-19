"""
media_ingest.remote — provider-neutral remote-media retrieval layer.

YouTube is the FIRST adapter, not a special case (approved S5
requirement). An adapter is a class exposing:

    provider                  short id ("youtube", ...)
    classify(url)   -> {supported, provider, media_id, canonical_url,
                        reject_reason}   # deterministic, no network
    probe(canonical, timeout_s)          # metadata only, no media
                    -> {ok, metadata, tool, category?, error?}
    retrieve(canonical, dest_dir, limits, timeout_s)
                    -> {ok, filename, tool, category?, error?}

Retrieval failures are EVIDENCE-BEARING events, categorized so the
report can state the effect on analysis confidence:
"""

FAILURE_CATEGORIES = (
    "bot_challenge",            # platform demanded human verification
    "geo_restricted",
    "age_restricted",
    "copyright_restricted",
    "unavailable",              # private/deleted/nonexistent media
    "policy_disabled",          # retrieval disabled by configuration
    "over_duration",
    "oversize",
    "live_stream",
    "timeout",
    "network_failure",
    "tool_missing",
    "retrieval_error",          # uncategorized residual
)

# What each category means for analysis confidence (deterministic text
# used by evidence packages and reports; never speculative).
CATEGORY_CONFIDENCE_EFFECT = {
    "bot_challenge": "The platform blocked automated retrieval; the "
                     "audiovisual media was not examined, so technical, "
                     "visual, and audio evidence is absent and any "
                     "conclusion rests on contextual evidence alone.",
    "geo_restricted": "The media is region-blocked from this runtime; "
                      "signal-level evidence is absent.",
    "age_restricted": "Age-restricted media requires authenticated "
                      "access, which this service does not use; "
                      "signal-level evidence is absent.",
    "copyright_restricted": "Access is restricted by the rights holder "
                            "or platform licensing; signal-level "
                            "evidence is absent.",
    "unavailable": "The media is private, removed, or nonexistent; only "
                   "contextual evidence about the reference could be "
                   "assessed.",
    "policy_disabled": "Remote retrieval is disabled by service policy; "
                       "signal-level evidence is absent.",
    "over_duration": "The media exceeds this service's duration limit "
                     "and was not retrieved; signal-level evidence is "
                     "absent.",
    "oversize": "The media exceeds this service's size limit and was "
                "not retrieved; signal-level evidence is absent.",
    "live_stream": "Live streams are not retrieved by this service; "
                   "signal-level evidence is absent.",
    "timeout": "Retrieval timed out; signal-level evidence is absent "
               "for this attempt.",
    "network_failure": "A network failure prevented retrieval; "
                       "signal-level evidence is absent for this "
                       "attempt.",
    "tool_missing": "The retrieval tool is not installed in this "
                    "runtime; signal-level evidence is absent.",
    "retrieval_error": "Retrieval failed for an uncategorized reason; "
                       "signal-level evidence is absent for this "
                       "attempt.",
}


def confidence_effect(category):
    return CATEGORY_CONFIDENCE_EFFECT.get(
        category, CATEGORY_CONFIDENCE_EFFECT["retrieval_error"])


def get_adapter(url):
    """Return the first adapter claiming the URL, else None. Import is
    lazy so the registry never drags optional tooling in unnecessarily."""
    from . import youtube
    for adapter in (youtube.YouTubeAdapter(),):
        c = adapter.classify(url)
        if c.get("provider_match"):
            return adapter
    return None
