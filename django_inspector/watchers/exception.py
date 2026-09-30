"""
Exception Watcher — captures unhandled exceptions with full stack traces,
local variables, and chained exception chains.

Requirements: EXC-01..EXC-04
"""

import sys
import logging
import traceback
from itertools import islice

from django.core.signals import got_request_exception
from django.views.debug import SafeExceptionReporterFilter

from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")

MAX_FRAMES = 50          # innermost frames kept per exception
MAX_CHAIN = 10           # chained exceptions kept
MAX_VALUE_LENGTH = 200   # characters of each local's repr
MAX_CONTAINER_ITEMS = 50  # items of a dict/list/tuple local masked and shown


class _FrameFilter(SafeExceptionReporterFilter):
    """
    Django's own filter, always active: honours @sensitive_variables and
    @sensitive_post_parameters even when DEBUG is True (Django's debug page
    skips them then, but inspector events are stored and shown later).
    """

    def is_active(self, request):
        return True


_frame_filter = _FrameFilter()


class ExceptionWatcher(BaseWatcher):
    """
    Captures exception details via Django's got_request_exception signal.

    - EXC-01: Exception type, message, and full stack trace
    - EXC-02: Local variables at each stack frame
    - EXC-03: Chained exceptions (__cause__ and __context__)
    - EXC-04: Link to originating request via trace_id (handled by BaseWatcher.record)
    """

    watcher_name = "exception"

    def install_hooks(self):
        """Connect to Django's got_request_exception signal."""
        got_request_exception.connect(self._on_exception)

    def remove_hooks(self):
        """Disconnect from Django's got_request_exception signal."""
        got_request_exception.disconnect(self._on_exception)

    def _on_exception(self, sender, request=None, **kwargs):
        """Signal handler for unhandled exceptions."""
        exc_info = sys.exc_info()
        exc_type, exc_value, exc_tb = exc_info

        if exc_type is None or exc_value is None:
            return

        from django_inspector.ignores import should_ignore_exception
        if should_ignore_exception(exc_value):
            return

        frames, omitted = _extract_frames(exc_tb, request)
        metadata = {
            # EXC-01: type, message, stack trace (innermost MAX_FRAMES frames)
            "exception_type": f"{exc_type.__module__}.{exc_type.__qualname__}",
            "exception_message": str(exc_value),
            "stack_trace": traceback.format_exception(
                exc_type, exc_value, exc_tb, limit=-MAX_FRAMES, chain=False
            ),
            # EXC-02: locals at each frame
            "frames": frames,
            # EXC-03: chained exceptions
            "chained_exceptions": _extract_chain(exc_value),
        }
        if omitted:
            metadata["frames_omitted"] = omitted

        # Add request context if available
        if request is not None:
            metadata["request_method"] = getattr(request, "method", None)
            metadata["request_path"] = getattr(request, "path", None)

        self.record("exception.raised", metadata)


def _extract_frames(tb, request=None):
    """
    Return (frames, omitted): the innermost MAX_FRAMES stack frames with their
    local variables (EXC-02), and how many outer frames were left out.
    """
    tbs = []
    while tb is not None:
        tbs.append(tb)
        tb = tb.tb_next
    omitted = max(0, len(tbs) - MAX_FRAMES)
    frames = []
    for current in tbs[omitted:]:
        frame = current.tb_frame
        frames.append({
            "file": frame.f_code.co_filename,
            "line": current.tb_lineno,
            "function": frame.f_code.co_name,
            "locals": _safe_locals(_frame_variables(request, frame)),
        })
    return frames, omitted


def _frame_variables(request, frame) -> dict:
    """Locals with @sensitive_variables values replaced by Django's stars."""
    try:
        return dict(_frame_filter.get_traceback_frame_variables(request, frame))
    except Exception:
        logger.debug("inspector: could not filter frame variables", exc_info=True)
        return dict(frame.f_locals)


def _safe_locals(local_vars: dict) -> dict:
    """
    Convert local variables to short, masked string representations.
    Containers are masked before repr, so a secret nested in a dict local is
    redacted like any other event field.
    """
    safe = {}
    for key, value in local_vars.items():
        if key.startswith("__") and key.endswith("__"):
            continue
        safe[key] = _safe_repr(value)
    return safe


def _safe_repr(value) -> str:
    from django.db.models.query import QuerySet

    from django_inspector.masking import mask_value

    try:
        if isinstance(value, QuerySet) and value._result_cache is None:
            # repr() would run the query, maybe inside a broken transaction.
            return "<unevaluated QuerySet: %s>" % value.model._meta.label
        if isinstance(value, tuple) and hasattr(value, "_asdict"):
            value = value._asdict()  # namedtuple: mask by field name
        if isinstance(value, dict):
            value = mask_value(dict(islice(value.items(), MAX_CONTAINER_ITEMS)))
        elif isinstance(value, list):
            value = mask_value(list(islice(value, MAX_CONTAINER_ITEMS)))
        elif isinstance(value, tuple):
            value = mask_value(tuple(islice(value, MAX_CONTAINER_ITEMS)))
        text = repr(value)
    except Exception:
        return "<unrepresentable>"
    if len(text) > MAX_VALUE_LENGTH:
        text = text[:MAX_VALUE_LENGTH] + "..."
    return text


def _extract_chain(exc: BaseException) -> list:
    """
    Extract chained exceptions — __cause__ (explicit chaining via `raise ... from`)
    and __context__ (implicit chaining) — EXC-03.
    """
    chain = []
    seen = set()
    current = exc

    while True:
        # Check __cause__ first (explicit), then __context__ (implicit)
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        next_exc = cause or context

        if next_exc is None:
            break

        exc_id = id(next_exc)
        if exc_id in seen or len(chain) >= MAX_CHAIN:
            break  # Avoid infinite loops and unbounded chains
        seen.add(exc_id)

        chain_type = "cause" if cause is not None else "context"
        chain.append({
            "type": f"{type(next_exc).__module__}.{type(next_exc).__qualname__}",
            "message": str(next_exc),
            "chain_type": chain_type,
            "stack_trace": traceback.format_exception(
                type(next_exc), next_exc, next_exc.__traceback__,
                limit=-MAX_FRAMES, chain=False,
            ),
        })

        current = next_exc

    return chain


# Auto-register on import
register("exception", ExceptionWatcher)
