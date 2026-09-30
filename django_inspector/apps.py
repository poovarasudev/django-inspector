import logging
from importlib import import_module

from django.apps import AppConfig

_logger = logging.getLogger("django_inspector")


class DjangoInspectorConfig(AppConfig):
    name = "django_inspector"
    label = "django_inspector"
    verbose_name = "Django Inspector"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django_inspector import checks  # noqa: F401  (registers the system checks)
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

        _import_watcher_modules()

        for name, watcher_class in registry.all_watchers().items():
            watcher = watcher_class()
            if inspector_settings.watcher_enabled(name):
                watcher.enable()
            self._watcher_instances.append(watcher)
            registry.set_instance(name, watcher)


WATCHER_MODULES = (
    "django_inspector.watchers.request",
    "django_inspector.watchers.sql",
    "django_inspector.watchers.exception",
    "django_inspector.watchers.cache",
    "django_inspector.watchers.template",
    "django_inspector.watchers.signal",
    "django_inspector.watchers.log",
)


def _import_watcher_modules():
    """Import each watcher module; one that fails is logged (or raised) and skipped."""
    from django_inspector.conf import inspector_settings

    for module in WATCHER_MODULES:
        try:
            import_module(module)
        except ImportError:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            _logger.warning(
                "django-inspector: could not import %s; that watcher is unavailable",
                module,
                exc_info=True,
            )
