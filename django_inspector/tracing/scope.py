"""
TraceScope — the trace lifecycle shared by InspectorMiddleware and trace().

Owns a trace's id, event buffer, query stats and early-sampling detail flag,
and the flush/prune/reset steps that end it. Extracted so the middleware and
the public trace() use exactly one lifecycle (TRACE-02).
"""

import logging
import time
from typing import Optional

from django.utils import timezone

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import (
    clear_trace_id,
    generate_trace_id,
    get_current_trace_id,
    set_trace_id,
)

logger = logging.getLogger("django_inspector")


def is_active() -> bool:
    """True when a trace is already open in the current context."""
    return get_current_trace_id() is not None


class TraceScope:
    """One trace's lifecycle. Use ``TraceScope.start()`` to begin one."""

    def __init__(self, trace_id: str, pick: Optional[bool] = None):
        self.trace_id = trace_id
        self.pick = pick
        self.started = time.monotonic()
        # Wall-clock start: anchor events are stamped with it, so they come
        # first in their trace's timeline.
        self.started_at = timezone.now()
        self._trace_token: Optional[object] = None
        self._buffer_token: Optional[object] = None
        self._query_log_token: Optional[object] = None
        self._detail_token: Optional[object] = None

    @classmethod
    def start(cls, trace_id: Optional[str] = None) -> "TraceScope":
        """
        Begin a trace: generate an id when none is given, and start a fresh
        event buffer, query stats and early-sampling detail flag. Keeps
        every token so close() can restore the context precisely.
        """
        from django_inspector.sampling import early_pick, start_detail
        from django_inspector.storage.flush import start_buffer
        from django_inspector.watchers.sql import start_query_log

        pick = early_pick()
        scope = cls(trace_id or generate_trace_id(), pick)
        scope._trace_token = set_trace_id(scope.trace_id)
        scope._buffer_token = start_buffer()
        scope._query_log_token = start_query_log()
        scope._detail_token = start_detail(pick is not False)
        return scope

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000

    def flush(self, keep: bool) -> None:
        """
        Write the buffered events (unless ``keep`` is False, which discards
        them instead), prune old events, and clear the query log. Each step
        is independently guarded: a failure there is logged and swallowed,
        or raised under INSPECTOR_RAISE_ERRORS, and never stops the others.
        """
        if not keep:
            try:
                from django_inspector.storage.flush import clear_buffer
                clear_buffer()
            except Exception:
                if inspector_settings.INSPECTOR_RAISE_ERRORS:
                    raise
                logger.warning("inspector: clear_buffer failed", exc_info=True)
        try:
            from django_inspector.storage.flush import flush_events
            flush_events()
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: flush_events failed", exc_info=True)
        try:
            from django_inspector.storage.retention import maybe_prune
            maybe_prune()
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: pruning old events failed", exc_info=True)
        try:
            from django_inspector.watchers.sql import clear_query_log
            clear_query_log()
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: clear_query_log failed", exc_info=True)

    def close(self) -> None:
        """
        Reset the detail flag, query log, buffer and trace id, in reverse of
        the order start() set them. Must run in the context that called
        start() — a ContextVar token can only be reset where it was set, so
        this can never go through sync_to_async.
        """
        from django_inspector.sampling import reset_detail
        from django_inspector.storage.flush import reset_buffer
        from django_inspector.watchers.sql import reset_query_log

        reset_detail(self._detail_token)
        reset_query_log(self._query_log_token)
        reset_buffer(self._buffer_token)
        clear_trace_id(self._trace_token)
