"""
Django system checks for django-inspector's configuration.

They run with `manage.py check`, `runserver` and `migrate`, so a typo or an
invalid value is reported once at startup instead of being silently ignored
(or failing on every request). Warnings can be silenced with
SILENCED_SYSTEM_CHECKS, e.g. ["django_inspector.W006"].
"""

import difflib
import ipaddress
import logging
import numbers
import re

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from django_inspector.conf import DEFAULTS

MIDDLEWARE_PATH = "django_inspector.middleware.InspectorMiddleware"

_BOOL_KEYS = ("INSPECTOR_ENABLED", "SQL_CAPTURE_PARAMS", "EARLY_SAMPLING", "INSPECTOR_RAISE_ERRORS")
_NON_NEGATIVE_NUMBER_KEYS = ("SQL_SLOW_THRESHOLD_MS", "SLOW_REQUEST_THRESHOLD_MS")
_NON_NEGATIVE_INT_KEYS = ("MAX_BODY_SIZE", "MAX_EVENTS_PER_TRACE", "TRUSTED_PROXY_COUNT")
_STRING_LIST_KEYS = (
    "SENSITIVE_KEYS",
    "IGNORE_PATHS",
    "IGNORE_EXCEPTIONS",
    "SIGNAL_WATCH_LIST",
    "INSPECTOR_DASHBOARD_IP_ALLOWLIST",
)


def _user_settings():
    return getattr(settings, "DJANGO_INSPECTOR", {})


def _is_number(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _invalid(key, expected, value):
    return Error(
        "DJANGO_INSPECTOR[%r] must be %s; got %r." % (key, expected, value),
        id="django_inspector.E002",
    )


@register()
def check_settings(app_configs=None, **kwargs):
    user = _user_settings()
    if not isinstance(user, dict):
        return [Error("DJANGO_INSPECTOR must be a dict.", id="django_inspector.E001")]

    errors = []
    for key in user:
        if key not in DEFAULTS:
            close = difflib.get_close_matches(str(key), DEFAULTS, n=1)
            errors.append(Warning(
                "Unknown DJANGO_INSPECTOR setting %r; it is ignored." % (key,),
                hint="Did you mean %r?" % close[0] if close else None,
                id="django_inspector.W001",
            ))

    errors.extend(_check_values(user))
    errors.extend(_check_references(user))
    return errors


def _check_values(user):
    errors = []
    for key in _BOOL_KEYS:
        if key in user and not isinstance(user[key], bool):
            errors.append(_invalid(key, "True or False", user[key]))
    for key in _NON_NEGATIVE_NUMBER_KEYS:
        if key in user and not (_is_number(user[key]) and user[key] >= 0):
            errors.append(_invalid(key, "a number >= 0", user[key]))
    for key in _NON_NEGATIVE_INT_KEYS:
        if key in user and not (_is_int(user[key]) and user[key] >= 0):
            errors.append(_invalid(key, "an integer >= 0", user[key]))
    for key in _STRING_LIST_KEYS:
        if key in user and not (
            isinstance(user[key], (list, tuple)) and all(isinstance(v, str) for v in user[key])
        ):
            errors.append(_invalid(key, "a list of strings", user[key]))

    if "SAMPLING_RATE" in user:
        rate = user["SAMPLING_RATE"]
        if not (_is_number(rate) and 0 <= rate <= 1):
            errors.append(_invalid("SAMPLING_RATE", "a number from 0.0 to 1.0", rate))
    if "RETENTION_HOURS" in user:
        hours = user["RETENTION_HOURS"]
        if hours is not None and not (_is_number(hours) and hours > 0):
            errors.append(_invalid("RETENTION_HOURS", "None or a number > 0", hours))
    if "DASHBOARD_URL_PREFIX" in user and not isinstance(user["DASHBOARD_URL_PREFIX"], str):
        errors.append(_invalid("DASHBOARD_URL_PREFIX", "a string", user["DASHBOARD_URL_PREFIX"]))
    if "INSPECTOR_DASHBOARD_PERMISSION" in user:
        perm = user["INSPECTOR_DASHBOARD_PERMISSION"]
        if perm is not None and not isinstance(perm, str):
            errors.append(_invalid("INSPECTOR_DASHBOARD_PERMISSION", "None or a dotted path", perm))
    if "LOG_LEVEL_THRESHOLD" in user:
        level = user["LOG_LEVEL_THRESHOLD"]
        valid = _is_int(level) or (
            isinstance(level, str) and isinstance(logging.getLevelName(level.upper()), int)
        )
        if not valid:
            errors.append(_invalid("LOG_LEVEL_THRESHOLD", "a level name or number", level))

    if "WATCHERS" in user:
        watchers = user["WATCHERS"]
        if not isinstance(watchers, dict):
            errors.append(_invalid("WATCHERS", "a dict of watcher name -> bool", watchers))
        else:
            for name, enabled in watchers.items():
                if name not in DEFAULTS["WATCHERS"]:
                    errors.append(Warning(
                        "Unknown watcher %r in DJANGO_INSPECTOR['WATCHERS']." % (name,),
                        hint="Known watchers: %s." % ", ".join(sorted(DEFAULTS["WATCHERS"])),
                        id="django_inspector.W002",
                    ))
                elif not isinstance(enabled, bool):
                    errors.append(_invalid("WATCHERS'][%r" % name, "True or False", enabled))

    for pattern in _strings(user.get("IGNORE_PATHS")):
        try:
            re.compile(pattern)
        except re.error as exc:
            errors.append(Error(
                "IGNORE_PATHS entry %r is not a valid regex: %s." % (pattern, exc),
                id="django_inspector.E003",
            ))
    for entry in _strings(user.get("INSPECTOR_DASHBOARD_IP_ALLOWLIST")):
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            errors.append(Error(
                "INSPECTOR_DASHBOARD_IP_ALLOWLIST entry %r is not an IP address or CIDR range." % (entry,),
                id="django_inspector.E004",
            ))
    return errors


def _check_references(user):
    """Dotted paths that must import."""
    from django.dispatch import Signal
    from django.utils.module_loading import import_string

    errors = []
    for name in _strings(user.get("IGNORE_EXCEPTIONS")):
        try:
            import_string(name)
        except ImportError:
            errors.append(Warning(
                "IGNORE_EXCEPTIONS entry %r can't be imported; it is ignored." % (name,),
                id="django_inspector.W003",
            ))
    for name in _strings(user.get("SIGNAL_WATCH_LIST")):
        try:
            signal = import_string(name)
        except ImportError:
            signal = None
        if not isinstance(signal, Signal):
            errors.append(Warning(
                "SIGNAL_WATCH_LIST entry %r is not an importable django.dispatch.Signal." % (name,),
                id="django_inspector.W004",
            ))
    perm = user.get("INSPECTOR_DASHBOARD_PERMISSION")
    if isinstance(perm, str):
        try:
            checker = import_string(perm)
        except ImportError:
            checker = None
        if not callable(checker):
            errors.append(Error(
                "INSPECTOR_DASHBOARD_PERMISSION %r is not an importable callable; "
                "every dashboard request will be denied." % (perm,),
                id="django_inspector.E005",
            ))
    return errors


def _strings(value):
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


def _enabled():
    user = _user_settings()
    return not isinstance(user, dict) or user.get("INSPECTOR_ENABLED", True) is not False


@register()
def check_middleware(app_configs=None, **kwargs):
    if not _enabled():
        return []
    middleware = list(getattr(settings, "MIDDLEWARE", None) or [])
    if MIDDLEWARE_PATH not in middleware:
        return [Warning(
            "%s is not in MIDDLEWARE, so nothing is traced." % MIDDLEWARE_PATH,
            hint="Add it as the first entry of MIDDLEWARE.",
            id="django_inspector.W005",
        )]
    if middleware[0] != MIDDLEWARE_PATH:
        return [Warning(
            "%s is not the first MIDDLEWARE entry; work done by the middleware "
            "before it is not part of the trace." % MIDDLEWARE_PATH,
            hint="Move it to the top of MIDDLEWARE, or silence django_inspector.W006.",
            id="django_inspector.W006",
        )]
    return []


@register(Tags.urls)
def check_dashboard_prefix(app_configs=None, **kwargs):
    """The dashboard's mount point must match DASHBOARD_URL_PREFIX, or it traces itself."""
    from django.urls import NoReverseMatch, reverse

    from django_inspector.ignores import is_dashboard_path

    if not _enabled():
        return []
    try:
        url = reverse("inspector:live-feed")
    except NoReverseMatch:
        return []  # dashboard not mounted
    except Exception:
        return []  # a broken URLconf is reported by Django's own checks
    if is_dashboard_path(url):
        return []
    from django_inspector.conf import inspector_settings

    return [Warning(
        "The dashboard is mounted at %r but DASHBOARD_URL_PREFIX is %r, so "
        "browsing the dashboard records its own requests." % (url, inspector_settings.DASHBOARD_URL_PREFIX),
        hint="Set DASHBOARD_URL_PREFIX to %r." % (url.strip("/") + "/"),
        id="django_inspector.W007",
    )]


@register()
def check_production_access(app_configs=None, **kwargs):
    """AUTH-04: staff status alone is thin protection for a production dashboard."""
    if not _enabled() or getattr(settings, "DEBUG", False):
        return []
    user = _user_settings() if isinstance(_user_settings(), dict) else {}
    if user.get("INSPECTOR_DASHBOARD_PERMISSION") or user.get("INSPECTOR_DASHBOARD_IP_ALLOWLIST"):
        return []
    return [Warning(
        "The inspector dashboard is protected only by is_staff while DEBUG is False.",
        hint="Set INSPECTOR_DASHBOARD_PERMISSION or INSPECTOR_DASHBOARD_IP_ALLOWLIST, "
        "set INSPECTOR_ENABLED = False, or silence django_inspector.W008.",
        id="django_inspector.W008",
    )]
