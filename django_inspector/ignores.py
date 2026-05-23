"""
Ignore-list support for django-inspector.

Provides path-based and exception-class-based filtering so health checks,
readiness probes, and expected exceptions don't pollute the event store.

Requirements: IGN-01..04
"""

import logging
import re
from functools import lru_cache

logger = logging.getLogger("django_inspector")


@lru_cache(maxsize=1)
def _get_path_patterns_key():
    """Return current IGNORE_PATHS as a hashable tuple (used as cache key)."""
    from django_inspector.conf import inspector_settings
    return tuple(inspector_settings.IGNORE_PATHS or [])


def _compiled_path_patterns():
    patterns = _get_path_patterns_key()
    return [re.compile(p) for p in patterns]


@lru_cache(maxsize=1)
def _compiled_exception_names():
    from django_inspector.conf import inspector_settings
    return frozenset(inspector_settings.IGNORE_EXCEPTIONS or [])


def should_ignore_path(path: str) -> bool:
    """Return True if the path matches any pattern in IGNORE_PATHS (IGN-01, IGN-03)."""
    try:
        return any(p.search(path) for p in _compiled_path_patterns())
    except Exception:
        logger.debug("inspector: error evaluating IGNORE_PATHS for %r", path, exc_info=True)
        return False


def should_ignore_exception(exc: BaseException) -> bool:
    """Return True if the exception class matches any entry in IGNORE_EXCEPTIONS (IGN-02, IGN-03)."""
    try:
        exc_class = type(exc)
        fqn = f"{exc_class.__module__}.{exc_class.__qualname__}"
        return fqn in _compiled_exception_names()
    except Exception:
        logger.debug("inspector: error evaluating IGNORE_EXCEPTIONS", exc_info=True)
        return False


def invalidate_ignore_caches() -> None:
    """Clear compiled pattern caches. Call after override_settings in tests."""
    _get_path_patterns_key.cache_clear()
    _compiled_exception_names.cache_clear()
