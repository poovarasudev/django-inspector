"""
Exception Watcher — captures unhandled exceptions with full stack traces,
local variables, and chained exception chains.

Requirements: EXC-01..EXC-04
"""

import sys
import logging
import traceback

from django.core.signals import got_request_exception

from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")


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

        metadata = {
            # EXC-01: type, message, full stack trace
            "exception_type": f"{exc_type.__module__}.{exc_type.__qualname__}",
            "exception_message": str(exc_value),
            "stack_trace": traceback.format_exception(exc_type, exc_value, exc_tb),
            # EXC-02: locals at each frame
            "frames": _extract_frames(exc_tb),
            # EXC-03: chained exceptions
            "chained_exceptions": _extract_chain(exc_value),
        }

        # Add request context if available
        if request is not None:
            metadata["request_method"] = getattr(request, "method", None)
            metadata["request_path"] = getattr(request, "path", None)

        self.record("exception.raised", metadata)


def _extract_frames(tb) -> list:
    """
    Extract stack frames from a traceback, including local variables
    at each frame (EXC-02).
    """
    frames = []
    current = tb
    while current is not None:
        frame = current.tb_frame
        frame_info = {
            "file": frame.f_code.co_filename,
            "line": current.tb_lineno,
            "function": frame.f_code.co_name,
            "locals": _safe_locals(frame.f_locals),
        }
        frames.append(frame_info)
        current = current.tb_next
    return frames


def _safe_locals(local_vars: dict) -> dict:
    """
    Convert local variables to safe, serializable representations.
    Truncate long values, skip unserializable objects.
    """
    MAX_VALUE_LENGTH = 200
    safe = {}
    for key, value in local_vars.items():
        # Skip dunder and private Django internals
        if key.startswith("__") and key.endswith("__"):
            continue
        try:
            val_str = repr(value)
            if len(val_str) > MAX_VALUE_LENGTH:
                val_str = val_str[:MAX_VALUE_LENGTH] + "..."
            safe[key] = val_str
        except Exception:
            safe[key] = "<unrepresentable>"
    return safe


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
        if exc_id in seen:
            break  # Avoid infinite loops
        seen.add(exc_id)

        chain_type = "cause" if cause is not None else "context"
        chain.append({
            "type": f"{type(next_exc).__module__}.{type(next_exc).__qualname__}",
            "message": str(next_exc),
            "chain_type": chain_type,
            "stack_trace": traceback.format_exception(
                type(next_exc), next_exc, next_exc.__traceback__
            ),
        })

        current = next_exc

    return chain


# Auto-register on import
register("exception", ExceptionWatcher)
