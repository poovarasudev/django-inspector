import uuid
from contextvars import ContextVar
from typing import Optional

_trace_id_var: ContextVar[Optional[str]] = ContextVar("django_inspector_trace_id", default=None)


def generate_trace_id() -> str:
    """Generate a new unique trace ID (UUID4 hex string)."""
    return uuid.uuid4().hex


def set_trace_id(trace_id: str) -> object:
    """
    Set the trace_id for the current context.
    Returns the ContextVar Token (use to reset in finally blocks).
    """
    return _trace_id_var.set(trace_id)


def get_current_trace_id() -> Optional[str]:
    """Return the trace_id for the current context, or None if not set."""
    return _trace_id_var.get()


def clear_trace_id(token=None) -> None:
    """
    Clear the trace_id from the current context.
    If a token is provided (from set_trace_id), reset to the prior value.
    Otherwise set to None.
    """
    if token is not None:
        _trace_id_var.reset(token)
    else:
        _trace_id_var.set(None)
