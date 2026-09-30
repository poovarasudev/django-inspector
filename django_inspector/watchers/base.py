"""
BaseWatcher — abstract base class all watchers must subclass.

Each concrete watcher:
1. Defines `watcher_name` class attribute (e.g., "sql")
2. Implements `install_hooks()` and `remove_hooks()` to connect/disconnect
   Django signals or monkey-patches
3. Calls `self.record(event_type, metadata)` to buffer events for storage
"""

import abc
import logging

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id

logger = logging.getLogger("django_inspector")


class BaseWatcher(abc.ABC):
    """
    Abstract base for all django-inspector watchers.

    Lifecycle:
    - AppConfig.ready() instantiates each registered watcher and calls enable()
      if the watcher is active per settings.
    - enable() calls install_hooks() to connect Django signals / patches.
    - disable() calls remove_hooks() to disconnect them.
    - record() buffers an event for the current request trace.
    """

    watcher_name: str = ""

    def __init__(self):
        if not self.watcher_name:
            raise ValueError(
                f"{self.__class__.__name__} must define a non-empty `watcher_name`"
            )
        self._enabled: bool = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """Enable this watcher — installs hooks if not already enabled."""
        if self._enabled:
            return
        self.install_hooks()
        self._enabled = True
        logger.debug("django-inspector: %s watcher enabled", self.watcher_name)

    def disable(self) -> None:
        """Disable this watcher — removes hooks if currently enabled."""
        if not self._enabled:
            return
        self.remove_hooks()
        self._enabled = False
        logger.debug("django-inspector: %s watcher disabled", self.watcher_name)

    def record(self, event_type: str, metadata: dict, timestamp=None) -> None:
        """
        Buffer a watcher event for the current request trace.

        Args:
            event_type: Dotted string, e.g. "sql.query". Conventionally
                        "{watcher_name}.{event_kind}".
            metadata:   Dict of event-specific data. Must be JSON-serializable.
            timestamp:  When the event happened; defaults to now.
        """
        if not self._enabled:
            return

        if not inspector_settings.is_enabled:
            return

        trace_id = get_current_trace_id()
        if trace_id is None:
            return

        from django_inspector.masking import mask_metadata
        from django_inspector.storage.flush import buffer_event

        try:
            masked = mask_metadata(metadata)
        except Exception as exc:
            # MASK-05: fail closed. Unmasked data never reaches the buffer; a
            # placeholder keeps the event visible in its trace. Only the
            # exception type is kept, since its message could contain the data.
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning(
                "inspector: masking failed for a %s event; stored a placeholder instead",
                event_type,
                exc_info=True,
            )
            masked = {"masking_failed": True, "error_type": type(exc).__name__}

        buffer_event(trace_id=trace_id, event_type=event_type, metadata=masked, timestamp=timestamp)

    @abc.abstractmethod
    def install_hooks(self) -> None:
        """Connect signals, monkey-patches, or other hooks."""

    @abc.abstractmethod
    def remove_hooks(self) -> None:
        """Disconnect signals, monkey-patches, or other hooks."""
