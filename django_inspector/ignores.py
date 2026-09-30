"""
Ignore-list support for django-inspector.

Provides path-based and exception-class-based filtering so health checks,
readiness probes, and expected exceptions don't pollute the event store.

Requirements: IGN-01..04
"""

import logging
import re
from functools import lru_cache

from django.core.signals import setting_changed

logger = logging.getLogger("django_inspector")


@lru_cache(maxsize=1)
def _get_path_patterns_key():
    """Return current IGNORE_PATHS as a hashable tuple (used as cache key)."""
    from django_inspector.conf import inspector_settings
    return tuple(inspector_settings.IGNORE_PATHS or [])


def _compiled_path_patterns():
    return _compiled_patterns(_get_path_patterns_key())


@lru_cache(maxsize=4)
def _compiled_patterns(patterns):
    """Compile each IGNORE_PATHS regex once; invalid ones are logged and skipped."""
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            logger.warning("inspector: IGNORE_PATHS entry %r is not a valid regex", pattern)
    return tuple(compiled)


@lru_cache(maxsize=1)
def _compiled_exception_classes():
    """
    Return a frozenset of resolved exception classes from IGNORE_EXCEPTIONS.

    Uses import_string so public aliases like 'django.http.Http404' work even
    when the class lives in 'django.http.response'. Entries that fail to
    import are logged and skipped.
    """
    from django.utils.module_loading import import_string

    from django_inspector.conf import inspector_settings

    dotted_names = inspector_settings.IGNORE_EXCEPTIONS or []
    classes = set()
    for name in dotted_names:
        try:
            cls = import_string(name)
            classes.add(cls)
        except ImportError:
            logger.warning("inspector: IGNORE_EXCEPTIONS entry %r could not be imported", name)
    return frozenset(classes)


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
        return type(exc) in _compiled_exception_classes()
    except Exception:
        logger.debug("inspector: error evaluating IGNORE_EXCEPTIONS", exc_info=True)
        return False


def invalidate_ignore_caches() -> None:
    """Clear compiled pattern caches. Runs automatically when DJANGO_INSPECTOR changes."""
    _get_path_patterns_key.cache_clear()
    _compiled_patterns.cache_clear()
    _compiled_exception_classes.cache_clear()


def _on_setting_changed(setting, **kwargs):
    if setting == "DJANGO_INSPECTOR":
        invalidate_ignore_caches()


setting_changed.connect(_on_setting_changed)


def is_dashboard_path(path: str) -> bool:
    """
    Return True if ``path`` is under DASHBOARD_URL_PREFIX.

    The middleware skips tracing for these paths so the dashboard's own SQL,
    template and cache activity never becomes events. The prefix must match
    where the host mounts ``django_inspector.dashboard.urls``. An empty or "/"
    prefix matches nothing: treating the whole site as the dashboard would
    silently switch the inspector off.
    """
    from django_inspector.conf import inspector_settings

    prefix = (inspector_settings.DASHBOARD_URL_PREFIX or "").strip("/")
    if not prefix:
        return False
    prefix = "/" + prefix
    return path == prefix or path.startswith(prefix + "/")
