"""
Celery Watcher — turns a Celery task run into its own trace, linked back to
the trace that sent it.

Hooks are Celery signals only (before_task_publish, task_prerun,
task_failure, task_retry, task_postrun) — no patching of Celery's own
classes, since signals are the stable, version-independent interface. Celery
is never imported at module level: install_hooks() imports celery.signals
inside a try, so without Celery installed the watcher just enables as a
no-op.

Linking is by reference, not nesting: a task is sent before the sender's
keep/drop decision is made, so nesting its events inside the sender's trace
would orphan them if the sender's trace is dropped. Instead the sender gets
a task.sent event, and the task gets its own trace sampled on its own,
anchored by task.completed with parent_trace_id pointing back to the
sender's trace (which may not itself be stored).

Requirements: CELERY-01..05
"""

import logging
import threading
from typing import Dict, Optional, Tuple

from django_inspector.tracing.api import _Run
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")

FLUSH_CHUNK_SIZE = 500        # events flushed per chunk, as for traced commands
MAX_ARG_LENGTH = 500           # characters kept per repr-bounded arg/kwarg/result value
MAX_OPEN_TASK_RUNS = 1000      # open task runs tracked between prerun/postrun; oldest evicted past this

_DISPATCH_UID = "django_inspector.watchers.celery"
_JSON_SCALARS = (str, int, float, bool, type(None))

# task_id -> _TaskRun, carrying state between the separate task_prerun,
# task_failure/task_retry and task_postrun signal dispatches. A ContextVar
# can't be used here: it only lives inside one task's own enter()/exit()
# pair, which prerun and postrun are, but the dict is read and written by
# other signal handlers running at other points in that same lifecycle.
_runs: Dict[str, "_TaskRun"] = {}
_runs_lock = threading.Lock()  # thread-pool workers store and evict concurrently


class _TaskRun:
    __slots__ = (
        "run", "args", "kwargs", "retries", "hostname", "queue", "eta",
        "retrying", "exc_info", "postrun_state", "retval",
    )

    def __init__(self, args, kwargs, retries, hostname, queue, eta):
        self.run: Optional[_Run] = None
        self.args = args
        self.kwargs = kwargs
        self.retries = retries
        self.hostname = hostname
        self.queue = queue
        self.eta = eta
        self.retrying = False
        self.exc_info: Tuple = (None, None, None)
        self.postrun_state = None
        self.retval = None


class CeleryWatcher(BaseWatcher):
    """
    CELERY-01..05: a task sent during a trace carries that trace's id in its
    message headers (task.sent on the sender's trace); the worker gives the
    task's own run its own trace, anchored by task.completed with
    parent_trace_id pointing back to the sender.
    """

    watcher_name = "celery"

    def install_hooks(self):
        try:
            from celery import signals
        except ImportError:
            logger.debug("inspector: celery is not installed; the celery watcher is a no-op")
            return
        signals.before_task_publish.connect(_on_before_task_publish, weak=False, dispatch_uid=_DISPATCH_UID)
        signals.task_prerun.connect(_on_task_prerun, weak=False, dispatch_uid=_DISPATCH_UID)
        signals.task_failure.connect(_on_task_failure, weak=False, dispatch_uid=_DISPATCH_UID)
        signals.task_retry.connect(_on_task_retry, weak=False, dispatch_uid=_DISPATCH_UID)
        signals.task_postrun.connect(_on_task_postrun, weak=False, dispatch_uid=_DISPATCH_UID)

    def remove_hooks(self):
        try:
            from celery import signals
        except ImportError:
            return
        signals.before_task_publish.disconnect(dispatch_uid=_DISPATCH_UID)
        signals.task_prerun.disconnect(dispatch_uid=_DISPATCH_UID)
        signals.task_failure.disconnect(dispatch_uid=_DISPATCH_UID)
        signals.task_retry.disconnect(dispatch_uid=_DISPATCH_UID)
        signals.task_postrun.disconnect(dispatch_uid=_DISPATCH_UID)


def _run_guarded(action, message):
    """Run action(); log (or raise under INSPECTOR_RAISE_ERRORS) on failure. Never lets a
    handler's own error reach Celery — see module docstring."""
    from django_inspector.conf import inspector_settings

    try:
        action()
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS:
            raise
        logger.warning("inspector: %s", message, exc_info=True)


# --- Sending side -----------------------------------------------------------

def _on_before_task_publish(sender=None, headers=None, body=None, routing_key=None,
                            exchange=None, properties=None, **kw):
    from django_inspector.watchers import registry

    watcher = registry.get_instance("celery")
    if watcher is None or not watcher.is_enabled:
        return
    _run_guarded(
        lambda: _record_task_sent(watcher, sender, headers, routing_key, exchange),
        "failed to record a task.sent event for %r" % sender,
    )


def _record_task_sent(watcher, sender, headers, routing_key, exchange):
    from django_inspector.tracing.context import get_current_trace_id

    trace_id = get_current_trace_id()
    if trace_id is None or headers is None:
        return
    # The link header goes on even when detail sampling skipped this trace:
    # the task's own trace is sampled on its own and should still point back.
    headers["inspector_trace_id"] = trace_id
    if not watcher.is_capturing():
        return

    metadata = {
        "task": sender,
        "task_id": headers.get("id"),
        "queue": routing_key,
        "exchange": exchange if isinstance(exchange, (str, type(None))) else str(exchange),
        "eta": _json_safe(headers.get("eta")),
        "retries": headers.get("retries"),
    }
    watcher.record("task.sent", metadata)


# --- Worker side -------------------------------------------------------------

def _on_task_prerun(task_id=None, task=None, args=None, kwargs=None, **kw):
    from django_inspector.conf import inspector_settings
    from django_inspector.watchers import registry

    watcher = registry.get_instance("celery")
    if watcher is None or not watcher.is_enabled or not inspector_settings.is_enabled:
        return
    _run_guarded(
        lambda: _start_task_run(task_id, task, args, kwargs),
        "failed to start tracing task %r" % task_id,
    )


def _start_task_run(task_id, task, args, kwargs):
    request = getattr(task, "request", None)
    run_data = _TaskRun(
        args=_bounded_args(args),
        kwargs=_bounded_kwargs(kwargs),
        retries=getattr(request, "retries", 0) or 0,
        hostname=getattr(request, "hostname", None),
        queue=_queue_name(request),
        eta=_json_safe(getattr(request, "eta", None)),
    )
    run = _Run(
        task.name, anchor_type="task.completed",
        describe=_make_describe(task.name, task_id, run_data),
        record_when_joined=True, chunk_size=FLUSH_CHUNK_SIZE,
        parent_trace_id=_parent_trace_id(request),
    )
    run_data.run = run
    run.enter()
    _store_run(task_id, run_data)


def _store_run(task_id, run_data):
    with _runs_lock:
        evicted = None
        if task_id not in _runs and len(_runs) >= MAX_OPEN_TASK_RUNS:
            evicted = next(iter(_runs))
            del _runs[evicted]
        _runs[task_id] = run_data
    if evicted is not None:
        logger.warning(
            "inspector: open task run cap (%d) reached; evicted task %r without finishing its trace",
            MAX_OPEN_TASK_RUNS, evicted,
        )


def _parent_trace_id(request):
    """
    Protocol-2 custom headers (inspector_trace_id, set by _record_task_sent)
    end up as direct attributes on Context — but fall back to request.headers
    for safety across Celery versions/protocols.
    """
    parent = getattr(request, "inspector_trace_id", None)
    if parent:
        return parent
    headers = getattr(request, "headers", None) or {}
    return headers.get("inspector_trace_id")


def _queue_name(request):
    delivery_info = getattr(request, "delivery_info", None) or {}
    return delivery_info.get("routing_key")


def _on_task_failure(sender=None, task_id=None, exception=None, traceback=None, **kw):
    _run_guarded(
        lambda: _store_failure(task_id, exception, traceback),
        "failed to record a task failure for %r" % task_id,
    )


def _store_failure(task_id, exception, traceback):
    run_data = _runs.get(task_id)
    if run_data is not None:
        run_data.exc_info = (type(exception), exception, traceback)


def _on_task_retry(sender=None, request=None, reason=None, **kw):
    _run_guarded(lambda: _mark_retrying(request), "failed to record a task retry")


def _mark_retrying(request):
    task_id = getattr(request, "id", None) if request is not None else None
    if task_id is None:
        return
    run_data = _runs.get(task_id)
    if run_data is not None:
        run_data.retrying = True


def _on_task_postrun(task_id=None, task=None, args=None, kwargs=None, retval=None, state=None, **kw):
    run_data = _runs.pop(task_id, None)
    if run_data is None or run_data.run is None:
        return
    run_data.postrun_state = state
    run_data.retval = retval
    exc_type, exc_value, exc_tb = run_data.exc_info
    run = run_data.run
    _run_guarded(
        lambda: run.exit(exc_type, exc_value, exc_tb),
        "failed to finish tracing task %r" % task_id,
    )


def _make_describe(task_name, task_id, run_data):
    def describe(exc_type, exc_value):
        exc_stored_type, exc_stored_value, _ = run_data.exc_info
        failed = run_data.postrun_state == "FAILURE" or (
            exc_stored_type is not None and not run_data.retrying
        )
        extra = {
            "task": task_name,
            "task_id": task_id,
            "state": "RETRY" if run_data.retrying else run_data.postrun_state,
            "retries": run_data.retries,
            "worker": run_data.hostname,
            "queue": run_data.queue,
            "eta": run_data.eta,
            "args": run_data.args,
            "kwargs": run_data.kwargs,
        }
        if not failed and not run_data.retrying:
            extra["result"] = _bounded_repr(run_data.retval)
        if exc_stored_type is not None:
            extra["exception"] = _format_exception(exc_stored_type, exc_stored_value)
        return failed, extra
    return describe


def _format_exception(exc_type, exc_value):
    return "%s.%s: %s" % (exc_type.__module__, exc_type.__qualname__, exc_value)


def _bounded_repr(value) -> str:
    text = repr(value)
    if len(text) > MAX_ARG_LENGTH:
        return text[:MAX_ARG_LENGTH] + "..."
    return text


def _is_json_friendly(value) -> bool:
    if isinstance(value, _JSON_SCALARS):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_friendly(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_friendly(v) for k, v in value.items())
    return False


def _json_safe(value):
    """``value`` as is when JSON can store it, else its str() (an eta can be a datetime)."""
    return value if _is_json_friendly(value) else str(value)


def _bounded_args(args) -> list:
    return [v if _is_json_friendly(v) else _bounded_repr(v) for v in (args or ())]


def _bounded_kwargs(kwargs) -> dict:
    return {k: (v if _is_json_friendly(v) else _bounded_repr(v)) for k, v in (kwargs or {}).items()}


# Auto-register on import
register("celery", CeleryWatcher)
