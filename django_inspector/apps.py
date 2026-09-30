import logging

from django.apps import AppConfig

_logger = logging.getLogger("django_inspector")


class DjangoInspectorConfig(AppConfig):
    name = "django_inspector"
    label = "django_inspector"
    verbose_name = "Django Inspector"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django_inspector.conf import inspector_settings

        if not inspector_settings.is_enabled:
            return

        self._watcher_instances = []
        self._autodiscover_watchers()
        self._check_production_auth_config(inspector_settings)

    def _check_production_auth_config(self, inspector_settings):
        """Warn at startup when running in production with only default is_staff protection (AUTH-04)."""
        from django.conf import settings as django_settings
        is_debug = getattr(django_settings, "DEBUG", True)
        if not is_debug:
            perm = inspector_settings.INSPECTOR_DASHBOARD_PERMISSION
            allowlist = inspector_settings.INSPECTOR_DASHBOARD_IP_ALLOWLIST
            if perm is None and not allowlist:
                _logger.warning(
                    "django-inspector: dashboard is running in production (DEBUG=False) with "
                    "default is_staff permission only. Set INSPECTOR_DASHBOARD_PERMISSION or "
                    "INSPECTOR_DASHBOARD_IP_ALLOWLIST to restrict access, or set "
                    "INSPECTOR_ENABLED=False to disable the dashboard entirely."
                )

    def _autodiscover_watchers(self):
        """
        Import watcher modules (triggering self-registration) then
        instantiate and enable each registered watcher based on settings.
        """
        from django_inspector.conf import inspector_settings
        from django_inspector.watchers import registry

        # Import concrete watchers to trigger self-registration
        try:
            import django_inspector.watchers.request  # noqa: F401
        except ImportError:
            pass
        try:
            import django_inspector.watchers.sql  # noqa: F401
        except ImportError:
            pass
        try:
            import django_inspector.watchers.exception  # noqa: F401
        except ImportError:
            pass
        try:
            import django_inspector.watchers.cache  # noqa: F401
        except ImportError:
            pass
        try:
            import django_inspector.watchers.template  # noqa: F401
        except ImportError:
            pass
        try:
            import django_inspector.watchers.signal  # noqa: F401
        except ImportError:
            pass
        try:
            import django_inspector.watchers.log  # noqa: F401
        except ImportError:
            pass

        for name, watcher_class in registry.all_watchers().items():
            watcher = watcher_class()
            if inspector_settings.watcher_enabled(name):
                watcher.enable()
            self._watcher_instances.append(watcher)
            registry.set_instance(name, watcher)
