"""Pure validation of manual inventory proposals; never provider evidence."""
from datetime import datetime
import math
import re
from .inference_budget import identifier


def validate_interval(*, start, end=None, actual_rate=None, reference_rate=None,
                      session_id, provider_id, resource_id,
                      gpu_count, gpu_type, model_revision, provenance):
    def timestamp(value):
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("Timezone-aware ISO timestamp required")
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Timezone-aware ISO timestamp required") from None
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("Timezone-aware ISO timestamp required")
        return result

    for value in (session_id, provider_id, resource_id):
        identifier(value)
    begun = timestamp(start)
    if end is not None and timestamp(end) <= begun:
        raise ValueError("End must follow start")
    for rate in (actual_rate, reference_rate):
        if rate is not None and (type(rate) not in (int, float) or
                not math.isfinite(rate) or not 0 <= rate <= 100000):
            raise ValueError("Finite nonnegative rate or unknown required")
    if type(gpu_count) is not int or not 0 <= gpu_count <= 32:
        raise ValueError("GPU count must be an integer from 0 to 32")
    for value in (gpu_type, provenance):
        if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(ord(c) < 32 for c in value):
            raise ValueError("GPU type and attributed provenance required")
    if not isinstance(model_revision, str) or not re.fullmatch(r"(?:[a-f0-9]{40}|(?:sha256:)?[a-f0-9]{64})", model_revision):
        raise ValueError("Exact model revision required")
    return {"start": start, "end": end, "actual_rate": actual_rate,
            "session_id": session_id, "provider_id": provider_id, "resource_id": resource_id,
            "reference_rate": reference_rate, "gpu_count": gpu_count,
            "gpu_type": gpu_type, "model_revision": model_revision,
            "provenance": provenance, "source": "operator",
            "termination_confirmed": False}
