"""Pure comparison of declared relationship endpoint versions; grants nothing."""
import re


def validate_snapshot(current, asserted):
    """Two ordered endpoints, each with kind/id/version/digest.

    Direction is significant. Legacy absent bindings are unbound. The returned
    declared_current label establishes neither identity nor connectivity.
    """
    def endpoints(value, legacy=False):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("Exactly two ordered endpoints required")
        for endpoint in value:
            if not isinstance(endpoint, dict) or set(endpoint) != {"kind", "id", "version", "digest"}:
                raise ValueError("Invalid endpoint fields")
            for key in ("kind", "id"):
                if not isinstance(endpoint[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", endpoint[key]):
                    raise ValueError("Invalid endpoint identity")
            if legacy and (endpoint["version"] is None or endpoint["digest"] is None):
                continue
            if type(endpoint["version"]) is not int or endpoint["version"] < 1 or not isinstance(endpoint["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", endpoint["digest"]):
                raise ValueError("Invalid endpoint version binding")
        if (value[0]["kind"], value[0]["id"]) == (value[1]["kind"], value[1]["id"]):
            raise ValueError("Distinct relationship endpoints required")

    endpoints(current)
    if asserted is None:
        return "unbound"
    endpoints(asserted, legacy=True)
    if any(e["version"] is None or e["digest"] is None for e in asserted):
        return "unbound"
    return "declared_current" if current == asserted else "needs_review"
