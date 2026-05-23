"""
Sensitive-data masking for django-inspector events.

Applies key-based redaction and value-pattern scanning before events reach the DB.
Called by BaseWatcher.record() for every captured event.

Requirements: MASK-01..06
"""

import re

_BUILTIN_SENSITIVE_KEYS = frozenset([
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token",
    "secret", "api_key", "api-key", "x-api-key",
    "authorization", "cookie", "set-cookie",
    "csrfmiddlewaretoken", "csrf_token",
    "session", "sessionid",
    "private_key", "auth",
])

_REDACTED = "***REDACTED***"
_REDACTED_DEEP = "***REDACTED_DEEP***"

_CC_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_JWT_PATTERN = re.compile(r"[A-Za-z0-9_-]{2,}\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def mask_metadata(metadata: dict) -> dict:
    """
    Return a copy of ``metadata`` with sensitive values redacted.

    - MASK-01: Key-based redaction using built-in + configurable SENSITIVE_KEYS list.
    - MASK-02: Nested dict/list walking (depth-capped at 10).
    - MASK-03: Value scanning for credit-card-shaped numbers.
    - MASK-04: Value scanning for JWT-shaped strings.
    - MASK-05: Extra keys from settings are additive to built-in defaults.
    - MASK-06: Depth > 10 truncated to avoid unbounded recursion.
    """
    from django_inspector.conf import inspector_settings
    extra = inspector_settings.SENSITIVE_KEYS or []
    extra_keys = frozenset(k.lower() for k in extra)
    all_keys = _BUILTIN_SENSITIVE_KEYS | extra_keys
    return _walk(metadata, all_keys, depth=0)


def _walk(obj, sensitive_keys: frozenset, depth: int):
    if depth > 10:
        return _REDACTED_DEEP
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in sensitive_keys:
                result[k] = _REDACTED
            else:
                result[k] = _walk(v, sensitive_keys, depth + 1)
        return result
    elif isinstance(obj, list):
        return [_walk(item, sensitive_keys, depth + 1) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(_walk(item, sensitive_keys, depth + 1) for item in obj)
    elif isinstance(obj, str):
        return _scan_value(obj)
    return obj


def _scan_value(value: str) -> str:
    """Replace JWT-shaped and CC-shaped strings with REDACTED."""
    if _JWT_PATTERN.search(value):
        return _REDACTED
    if _CC_PATTERN.search(value):
        return _REDACTED
    return value
