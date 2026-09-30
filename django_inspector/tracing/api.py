"""
Public trace() — start a trace from any code, not just an HTTP request.

    with trace("nightly-report"):
        ...

    async with trace("sync-prices"):
        ...

    @trace("send-digest")
    def send_digest(): ...

    @trace("refresh-cache")
    async def refresh_cache(): ...

A trace() opened while a trace is already active joins it: no new trace, no
flush, no anchor event, and the outer trace id is returned. Otherwise it
starts a TraceScope, runs the block, records the exception (if any) through
the exception watcher's capture(), records a trace.completed anchor, applies
should_keep() the same way a request does, flushes and closes the scope.

Every storage or watcher import here is deferred to call time, so importing
this module — and django_inspector itself — never imports the storage
models.

Requirements: TRACE-01, TRACE-03, TRACE-04
"""

import asyncio
import functools
import logging
from typing import Optional

from django_inspector.sampling import should_keep
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.tracing.scope import TraceScope, is_active

logger = logging.getLogger("django_inspector")


def _guarded(action, message, protect=False):
    """
    Run ``action()``. A failure is logged and swallowed, or re-raised under
    INSPECTOR_RAISE_ERRORS — unless ``protect`` is set, which keeps a user
    exception already propagating from being replaced by the inspector's own.
    """
    from django_inspector.conf import inspector_settings

    try:
        action()
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS and not protect:
            raise
        logger.warning("inspector: %s", message, exc_info=True)


def _capture_exception(exc_info):
    from django_inspector.watchers import registry

    watcher = registry.get_instance("exception")
    if watcher is not None:
        watcher.capture(exc_info)


def _record_anchor(trace_id, name, duration_ms, failed, error_type, timestamp):
    from django_inspector.masking import mask_metadata
    from django_inspector.storage.flush import buffer_event

    metadata = {
        "name": name,
        "duration_ms": duration_ms,
        "status": "error" if failed else "ok",
        "error_type": error_type,
    }
    # Stamped with the trace's start, like request.completed, so it precedes
    # the events recorded inside the block.
    buffer_event(
        trace_id=trace_id, event_type="trace.completed",
        metadata=mask_metadata(metadata), timestamp=timestamp,
    )


class _Run:
    """One open run of a trace() block: a fresh TraceScope, or a join."""

    __slots__ = ("name", "scope")

    def __init__(self, name):
        self.name = name
        self.scope: Optional[TraceScope] = None

    def enter(self):
        from django_inspector.conf import inspector_settings

        if not inspector_settings.is_enabled:
            return None
        if is_active():
            return get_current_trace_id()
        try:
            self.scope = TraceScope.start()
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: trace() failed to start", exc_info=True)
            return None
        return self.scope.trace_id

    def _prepare(self, scope: TraceScope, exc_type, exc_value, exc_tb):
        """Capture the exception (if any) and record the anchor; return keep."""
        failed = exc_type is not None
        protect = failed
        error_type = None

        if failed:
            _guarded(
                lambda: _capture_exception((exc_type, exc_value, exc_tb)),
                "trace() failed to capture the exception",
                protect=protect,
            )
            error_type = "%s.%s" % (exc_type.__module__, exc_type.__qualname__)

        duration_ms = scope.elapsed_ms()
        _guarded(
            lambda: _record_anchor(
                scope.trace_id, self.name, duration_ms, failed, error_type, scope.started_at
            ),
            "trace() failed to record the trace.completed anchor",
            protect=protect,
        )
        return should_keep(failed, duration_ms, scope.pick)

    def exit(self, exc_type, exc_value, exc_tb):
        scope = self.scope
        if scope is None:
            return
        protect = exc_type is not None
        try:
            keep = self._prepare(scope, exc_type, exc_value, exc_tb)
            _guarded(lambda: scope.flush(keep), "trace() failed to flush", protect=protect)
        finally:
            scope.close()

    async def aexit(self, exc_type, exc_value, exc_tb):
        scope = self.scope
        if scope is None:
            return
        from asgiref.sync import sync_to_async

        protect = exc_type is not None
        try:
            keep = self._prepare(scope, exc_type, exc_value, exc_tb)
            await sync_to_async(_guarded)(lambda: scope.flush(keep), "trace() failed to flush", protect)
        finally:
            scope.close()


class trace:
    """
    Start (or join) a trace around a block of code. See the module docstring
    for the four forms. Each ``with trace(...)`` / ``trace(...)()`` creates a
    new object, so only re-entering the *same* object concurrently is an
    error.
    """

    def __init__(self, name):
        self.name = name
        self._run: Optional[_Run] = None

    def __enter__(self):
        if self._run is not None:
            raise RuntimeError("trace(%r) is already open" % self.name)
        self._run = _Run(self.name)
        return self._run.enter()

    def __exit__(self, exc_type, exc_value, exc_tb):
        run, self._run = self._run, None
        assert run is not None  # __exit__ always follows a successful __enter__
        run.exit(exc_type, exc_value, exc_tb)
        return False

    async def __aenter__(self):
        if self._run is not None:
            raise RuntimeError("trace(%r) is already open" % self.name)
        self._run = _Run(self.name)
        return self._run.enter()

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        run, self._run = self._run, None
        assert run is not None  # __aexit__ always follows a successful __aenter__
        await run.aexit(exc_type, exc_value, exc_tb)
        return False

    def __call__(self, func):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def _async_wrapper(*args, **kwargs):
                async with trace(self.name):
                    return await func(*args, **kwargs)
            return _async_wrapper

        @functools.wraps(func)
        def _wrapper(*args, **kwargs):
            with trace(self.name):
                return func(*args, **kwargs)
        return _wrapper
