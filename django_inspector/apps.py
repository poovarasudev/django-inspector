from django.apps import AppConfig


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

        for name, watcher_class in registry.all_watchers().items():
            watcher = watcher_class()
            if inspector_settings.watcher_enabled(name):
                watcher.enable()
            self._watcher_instances.append(watcher)
