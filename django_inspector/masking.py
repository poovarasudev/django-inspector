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
    "authorization", "proxy-authorization", "x-auth-token",
    "cookie", "set-cookie",
    "csrfmiddlewaretoken", "csrf_token", "x-csrftoken", "x-csrf-token",
    "session", "sessionid",
    "private_key", "auth",
])

_REDACTED = "***REDACTED***"
_REDACTED_DEEP = "***REDACTED_DEEP***"

# Candidate card numbers: 13+ digits, optionally grouped by single spaces or
# dashes, not glued to further digits. Candidates are confirmed by Luhn.
_CC_CANDIDATE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,}(?!\d)")
_CC_GROUP_SEPARATOR = re.compile(r"[ -]")
_JWT_PATTERN = re.compile(r"[A-Za-z0-9_-]{2,}\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def mask_metadata(metadata: dict) -> dict:
    """
    Return a copy of ``metadata`` with sensitive values redacted.

    - MASK-01: Key-based redaction (case-insensitive); SENSITIVE_KEYS adds to the built-ins.
    - MASK-02: Built-in sensitive keys (password, token, secret, authorization, cookie, …).
    - MASK-03: Redacted values become "***REDACTED***"; keys stay visible.
    - MASK-04: Values holding a Luhn-valid card number or a JWT are redacted whatever their key.
    - MASK-06: Nested dicts/lists are walked, depth-capped at 10.
    (MASK-05, masking before buffering and failing closed, lives in BaseWatcher.record.)
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
    if _contains_card_number(value):
        return _REDACTED
    return value


def _contains_card_number(value: str) -> bool:
    """
    True if ``value`` contains a Luhn-valid 13–19 digit number (MASK-04).

    Digit groups (e.g. "4111 1111 1111 1111") are joined whole, starting at
    any group, so a card followed by other digits ("4111111111111111 12/26")
    is still found, while order ids, timestamps and long digit strings that
    fail Luhn pass through.
    """
    for match in _CC_CANDIDATE.finditer(value):
        groups = _CC_GROUP_SEPARATOR.split(match.group())
        for start in range(len(groups)):
            digits = ""
            for group in groups[start:]:
                digits += group
                if len(digits) > 19:
                    break
                if len(digits) >= 13 and _luhn_valid(digits):
                    return True
    return False


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        n = ord(char) - 48
        if position % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0
