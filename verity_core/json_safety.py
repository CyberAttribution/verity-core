"""Portable bounded-depth JSON loading.

Python's json module recursion depth varies by interpreter build and
version (a Mac CPython 3.13 happily parses 1500-2000 nested containers
that a Lambda CPython 3.10 rejects with RecursionError). Callers that
need deterministic, portable behavior must enforce an explicit nesting
budget BEFORE json.loads recurses.

bounded_json_loads scans the raw text/bytes first (no recursion, no
parsing of values) counting only structural object/array nesting
outside quoted strings, honoring backslash escapes. If the depth
exceeds the cap, ValueError is raised before json.loads is ever
invoked, so behavior is identical on every interpreter.

The default cap is 128 and callers cannot widen it: max_depth values
that are not ints, are bools, are <= 0, or exceed 128 are rejected.
"""

import json

MAX_SUPPORTED_DEPTH = 128


def _iter_text(text):
    """Yield characters from str or UTF-8 bytes input."""
    if isinstance(text, (bytes, bytearray)):
        return iter(text.decode("utf-8", "strict"))
    if isinstance(text, str):
        return iter(text)
    raise TypeError("bounded_json_loads expects str or UTF-8 bytes")


def _scan_depth(text, max_depth):
    """Iterative scan: max object/array nesting outside strings.

    Handles escaped quotes (\") and escaped backslashes (\\) inside
    strings so quote toggling is correct. Never recurses.
    """
    depth = 0
    in_string = False
    escaped = False
    for ch in _iter_text(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" or ch == "[":
            depth += 1
            if depth > max_depth:
                raise ValueError(
                    f"JSON nesting exceeds the supported depth "
                    f"({max_depth})")
        elif ch == "}" or ch == "]":
            if depth:
                depth -= 1
    return depth


def bounded_json_loads(text, *, max_depth=MAX_SUPPORTED_DEPTH, **kwargs):
    """json.loads with a portable, explicit nesting cap.

    Scans text/bytes (UTF-8 strict for bytes) for structural depth
    before delegating to json.loads, forwarding all keyword hooks
    (object_pairs_hook, parse_constant, parse_float, ...) unchanged.
    Raises ValueError('JSON nesting exceeds ...') before json.loads
    runs when nesting would exceed max_depth, regardless of the
    interpreter's own recursion limit.
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise TypeError("max_depth must be an integer")
    if max_depth <= 0:
        raise ValueError("max_depth must be positive")
    if max_depth > MAX_SUPPORTED_DEPTH:
        raise ValueError(
            f"max_depth cannot exceed {MAX_SUPPORTED_DEPTH}")
    _scan_depth(text, max_depth)
    return json.loads(text, **kwargs)
