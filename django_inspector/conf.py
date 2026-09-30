from django.conf import settings as django_settings

DEFAULTS = {
    "INSPECTOR_ENABLED": True,
    "WATCHERS": {
        "request": True,
        "sql": True,
        "exception": True,
        "cache": False,     # CACHE-07: opt in with {"WATCHERS": {"cache": True}}
        "template": False,  # opt in with {"WATCHERS": {"template": True}}
        "signal": False,    # SIGL-06: opt in with {"WATCHERS": {"signal": True}}
        "log": True,        # records at LOG_LEVEL_THRESHOLD or above
    },
    "DASHBOARD_URL_PREFIX": "inspector/",  # must match where the host mounts django_inspector.dashboard.urls; requests under it are never traced
    "SQL_SLOW_THRESHOLD_MS": 100,
    "SQL_CAPTURE_PARAMS": True,         # store bound SQL parameters (they are never masked by column name)
    "MAX_BODY_SIZE": 8192,
    "SENSITIVE_KEYS": [],               # extra keys to redact (merged additive with built-in defaults in masking.py)
    "SAMPLING_RATE": 1.0,               # fraction of successful requests to capture (0.0–1.0); errors always captured
    "SLOW_REQUEST_THRESHOLD_MS": 1000,  # requests at or above this latency are always captured regardless of rate
    "INSPECTOR_DASHBOARD_PERMISSION": None,  # dotted path to (request) -> bool callable; None = require is_staff
    "INSPECTOR_DASHBOARD_IP_ALLOWLIST": [],  # list of IP address or CIDR strings; empty = no IP restriction
    "TRUSTED_PROXY_COUNT": 0,           # reverse proxies in front of Django; X-Forwarded-For is trusted for this many hops only
    "IGNORE_PATHS": [],                 # list of regex strings matched against request.path; matching → skip entirely
    "IGNORE_EXCEPTIONS": [],            # list of dotted exception class names; matching → not recorded by exception watcher
    "SIGNAL_WATCH_LIST": [              # dotted paths of the signals the signal watcher captures
        "django.db.models.signals.pre_save",
        "django.db.models.signals.post_save",
        "django.db.models.signals.pre_delete",
        "django.db.models.signals.post_delete",
        "django.db.models.signals.m2m_changed",
    ],
    "LOG_LEVEL_THRESHOLD": "WARNING",   # level name or number; the log watcher ignores records below it
    "INSPECTOR_RAISE_ERRORS": False,    # if True, inspector-internal errors propagate (useful in dev/tests)
}


def _get_inspector_settings():
    user_settings = getattr(django_settings, "DJANGO_INSPECTOR", {})
    merged = {**DEFAULTS, **user_settings}
    if "WATCHERS" in user_settings:
        merged["WATCHERS"] = {**DEFAULTS["WATCHERS"], **user_settings["WATCHERS"]}
    return merged


class InspectorSettings:
    """
    Lazy accessor for django-inspector settings.
    Usage: from django_inspector.conf import inspector_settings
           inspector_settings.INSPECTOR_ENABLED
    """

    def __getattr__(self, name):
        config = _get_inspector_settings()
        if name not in config:
            raise AttributeError(f"Invalid django-inspector setting: {name!r}")
        return config[name]

    @property
    def is_enabled(self):
        return _get_inspector_settings().get("INSPECTOR_ENABLED", True)

    def watcher_enabled(self, watcher_name: str) -> bool:
        if not self.is_enabled:
            return False
        return _get_inspector_settings()["WATCHERS"].get(watcher_name, True)


inspector_settings = InspectorSettings()
