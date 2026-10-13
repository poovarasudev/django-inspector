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

_Run is the run of one trace-shaped block: trace() uses it with its defaults,
and the command watcher (watchers/command.py) uses it directly with a
different anchor type, a custom describe() and record_when_joined=True, so
there is exactly one lifecycle behind both (TRACE-02).

Every storage or watcher import here is deferred to call time, so importing
this module — and django_inspector itself — never imports the storage
models.

Requirements: TRACE-01, TRACE-03, TRACE-04
"""

import asyncio
import functools
import logging
import time
from typing import Optional

from django.utils import timezone

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


def _guarded_value(action, default, message, protect=False):
    """Like _guarded(), but for a call whose result is needed; returns ``default`` on failure."""
    from django_inspector.conf import inspector_settings

    try:
        return action()
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS and not protect:
            raise
        logger.warning("inspector: %s", message, exc_info=True)
        return default


def _capture_exception(exc_info):
    from django_inspector.watchers import registry

    watcher = registry.get_instance("exception")
    if watcher is not None:
        watcher.capture(exc_info)


def _default_describe(exc_type, exc_value):
    """The default describe(): failed iff there was an exception, no extra metadata."""
    return exc_type is not None, {}


def _record_anchor(
    trace_id, anchor_type, name, duration_ms, failed, error_type, timestamp, extra=None, parent_trace_id=None,
):
    from django_inspector.masking import mask_metadata
    from django_inspector.storage.flush import buffer_event, dropped_count

    metadata = {
        "name": name,
        "duration_ms": duration_ms,
        "status": "error" if failed else "ok",
        "error_type": error_type,
    }
    if extra:
        metadata.update(extra)
    # Like request.completed: past MAX_EVENTS_PER_TRACE, later events are
    # dropped and counted here so the anchor still says how many were lost.
    dropped = dropped_count()
    if dropped:
        metadata["events_dropped"] = dropped
    # Stamped with the trace's start, like request.completed, so it precedes
    # the events recorded inside the block.
    buffer_event(
        trace_id=trace_id, event_type=anchor_type,
        metadata=mask_metadata(metadata), timestamp=timestamp,
        parent_trace_id=parent_trace_id,
    )


class _Run:
    """
    One open run of a trace-shaped block: a fresh TraceScope, or a join.

    - ``anchor_type``: the event type of the anchor this run records
      ("trace.completed" for trace(), "command.completed" for the command
      watcher).
    - ``describe(exc_type, exc_value) -> (failed, extra)``: decides whether
      the run failed and what extra metadata its anchor carries; defaults to
      "failed iff there was an exception, no extra metadata".
    - ``record_when_joined``: when this run joins an already-active trace, it
      still records its own anchor into the outer trace at exit (stamped with
      this run's own start time) — but never captures the exception, flushes
      or closes, since the outer trace owns all of that.
    - ``chunk_size``: passed to TraceScope.start(), so a long run's buffer is
      flushed in chunks instead of held until the end.
    - ``parent_trace_id``: carried only on this run's own anchor event, never
      on the events inside it (used by the Celery watcher to link a task
      trace back to the trace that sent it).
    """

    __slots__ = (
        "name", "anchor_type", "describe", "record_when_joined", "chunk_size",
        "parent_trace_id", "scope", "joined_trace_id", "started", "started_at",
    )

    def __init__(
        self, name, anchor_type="trace.completed", describe=None,
        record_when_joined=False, chunk_size=None, parent_trace_id=None,
    ):
        self.name = name
        self.anchor_type = anchor_type
        self.describe = describe or _default_describe
        self.record_when_joined = record_when_joined
        self.chunk_size = chunk_size
        self.parent_trace_id = parent_trace_id
        self.scope: Optional[TraceScope] = None
        self.joined_trace_id: Optional[str] = None
        self.started: Optional[float] = None
        self.started_at = None

    def enter(self):
        from django_inspector.conf import inspector_settings

        if not inspector_settings.is_enabled:
            return None
        if is_active():
            trace_id = get_current_trace_id()
            if self.record_when_joined:
                self.joined_trace_id = trace_id
                self.started = time.monotonic()
                self.started_at = timezone.now()
            return trace_id
        try:
            self.scope = TraceScope.start(chunk_size=self.chunk_size)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: trace() failed to start", exc_info=True)
            return None
        return self.scope.trace_id

    def _describe(self, exc_type, exc_value, protect):
        """Run describe(), guarded; falls back to the default outcome on failure."""
        return _guarded_value(
            lambda: self.describe(exc_type, exc_value),
            _default_describe(exc_type, exc_value),
            "failed to describe the %s anchor" % self.anchor_type,
            protect=protect,
        )

    def _error_type(self, exc_type, failed):
        if not failed or exc_type is None:
            return None
        return "%s.%s" % (exc_type.__module__, exc_type.__qualname__)

    def _prepare(self, scope: TraceScope, exc_type, exc_value, exc_tb):
        """Capture the exception (if any) and record the anchor; return keep."""
        protect = exc_type is not None
        failed, extra = self._describe(exc_type, exc_value, protect)

        if failed and exc_type is not None and issubclass(exc_type, Exception):
            _guarded(
                lambda: _capture_exception((exc_type, exc_value, exc_tb)),
                "trace() failed to capture the exception",
                protect=protect,
            )
        error_type = self._error_type(exc_type, failed)

        duration_ms = scope.elapsed_ms()
        _guarded(
            lambda: _record_anchor(
                scope.trace_id, self.anchor_type, self.name, duration_ms, failed, error_type,
                scope.started_at, extra, self.parent_trace_id,
            ),
            "trace() failed to record the %s anchor" % self.anchor_type,
            protect=protect,
        )
        return should_keep(failed, duration_ms, scope.pick)

    def _prepare_joined(self, exc_type, exc_value, exc_tb):
        """
        Record this run's anchor into the outer trace it joined. No capture
        (the outer trace's own exit handles that), no flush, no close.
        """
        assert self.started is not None  # set alongside joined_trace_id in enter()
        protect = exc_type is not None
        failed, extra = self._describe(exc_type, exc_value, protect)
        error_type = self._error_type(exc_type, failed)
        duration_ms = (time.monotonic() - self.started) * 1000
        _guarded(
            lambda: _record_anchor(
                self.joined_trace_id, self.anchor_type, self.name, duration_ms, failed, error_type,
                self.started_at, extra, self.parent_trace_id,
            ),
            "trace() failed to record the %s anchor" % self.anchor_type,
            protect=protect,
        )

    def exit(self, exc_type, exc_value, exc_tb):
        scope = self.scope
        if scope is not None:
            protect = exc_type is not None
            try:
                keep = self._prepare(scope, exc_type, exc_value, exc_tb)
                _guarded(lambda: scope.flush(keep), "trace() failed to flush", protect=protect)
            finally:
                scope.close()
            return
        if self.record_when_joined and self.joined_trace_id is not None:
            self._prepare_joined(exc_type, exc_value, exc_tb)

    async def aexit(self, exc_type, exc_value, exc_tb):
        scope = self.scope
        if scope is not None:
            from asgiref.sync import sync_to_async

            protect = exc_type is not None
            try:
                keep = self._prepare(scope, exc_type, exc_value, exc_tb)
                await sync_to_async(_guarded)(lambda: scope.flush(keep), "trace() failed to flush", protect)
            finally:
                scope.close()
            return
        if self.record_when_joined and self.joined_trace_id is not None:
            from asgiref.sync import sync_to_async

            await sync_to_async(self._prepare_joined)(exc_type, exc_value, exc_tb)


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
