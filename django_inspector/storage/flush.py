"""
Event flush manager — buffers events in memory and writes them to the
database via bulk_create. Called by InspectorMiddleware at end-of-request.
"""

from contextvars import ContextVar
from typing import List

from django_inspector.conf import inspector_settings

_buffer_var: ContextVar[List[dict]] = ContextVar("inspector_event_buffer", default=None)


def _get_buffer() -> List[dict]:
    """Return the per-request event buffer (ContextVar-based, async-safe)."""
    buf = _buffer_var.get()
    if buf is None:
        buf = []
        _buffer_var.set(buf)
    return buf


def buffer_event(trace_id: str, event_type: str, metadata: dict) -> None:
    """Add an event to the current request buffer. Called by watchers."""
    if not inspector_settings.is_enabled:
        return
    _get_buffer().append(
        {
            "trace_id": trace_id,
            "event_type": event_type,
            "metadata": metadata,
        }
    )


def flush_events() -> int:
    """
    Write all buffered events to the database using bulk_create.
    Clears the buffer afterward. Returns the number of events written.
    Called by InspectorMiddleware at end-of-request.
    """
    from django_inspector.storage.models import Event

    buffer = _get_buffer()
    if not buffer:
        return 0

    events = [
        Event(
            trace_id=item["trace_id"],
            event_type=item["event_type"],
            metadata=item["metadata"],
        )
        for item in buffer
    ]

    Event.objects.bulk_create(events, ignore_conflicts=True)
    count = len(events)
    buffer.clear()
    return count


def clear_buffer() -> None:
    """Discard all buffered events without writing them. Used in tests."""
    _get_buffer().clear()
