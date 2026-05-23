from django.conf import settings as django_settings

DEFAULTS = {
    "INSPECTOR_ENABLED": True,
    "WATCHERS": {
        "request": True,
        "sql": True,
        "exception": True,
    },
    "DASHBOARD_URL_PREFIX": "inspector/",
    "SQL_SLOW_THRESHOLD_MS": 100,
    "MAX_BODY_SIZE": 8192,
    "SENSITIVE_KEYS": [],               # extra keys to redact (merged additive with built-in defaults in masking.py)
    "SAMPLING_RATE": 1.0,               # fraction of successful requests to capture (0.0–1.0); errors always captured
    "SLOW_REQUEST_THRESHOLD_MS": 1000,  # requests at or above this latency are always captured regardless of rate
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
