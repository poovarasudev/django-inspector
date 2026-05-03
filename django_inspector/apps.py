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

        self._autodiscover_watchers()

    def _autodiscover_watchers(self):
        """
        Auto-discover and register all watcher modules.
        Watchers are imported here so their signals/hooks are connected.
        """
        from django_inspector.watchers import registry  # noqa: F401
