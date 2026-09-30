"""
Event flush manager — buffers events in memory and writes them to the
database via bulk_create. Called by InspectorMiddleware at end-of-request.
"""

from contextvars import ContextVar

from django.utils import timezone

from django_inspector.conf import inspector_settings

# The request event is always kept, even past MAX_EVENTS_PER_TRACE: it is the
# trace's anchor and carries the dropped-event count.
_NEVER_DROPPED = frozenset({"request.completed"})


class _EventBuffer(list):
    """A request's buffered events, plus how many were dropped past the limit."""

    def __init__(self):
        super().__init__()
        self.dropped = 0

    def clear(self):
        super().clear()
        self.dropped = 0


_buffer_var: ContextVar = ContextVar("inspector_event_buffer", default=None)


def start_buffer():
    """
    Give the current request its own event buffer. Returns a token for
    reset_buffer(). Called by the middleware at the start of every request so
    a buffer inherited from the parent context (e.g. an ASGI server's) is never
    shared between concurrent requests.
    """
    return _buffer_var.set(_EventBuffer())


def reset_buffer(token) -> None:
    """Restore the buffer the context had before start_buffer()."""
    _buffer_var.reset(token)


def _get_buffer() -> _EventBuffer:
    """Return the per-request event buffer (ContextVar-based, async-safe)."""
    buf = _buffer_var.get()
    if buf is None:
        buf = _EventBuffer()
        _buffer_var.set(buf)
    return buf


def buffer_event(trace_id: str, event_type: str, metadata: dict, timestamp=None) -> None:
    """
    Add an event to the current request buffer. Called by watchers.

    ``timestamp`` defaults to now: events keep the time they were captured,
    not the time they are written. Past MAX_EVENTS_PER_TRACE events are
    counted and dropped.
    """
    if not inspector_settings.is_enabled:
        return
    buf = _get_buffer()
    limit = inspector_settings.MAX_EVENTS_PER_TRACE
    if limit and len(buf) >= limit and event_type not in _NEVER_DROPPED:
        buf.dropped += 1
        return
    buf.append(
        {
            "trace_id": trace_id,
            "event_type": event_type,
            "metadata": metadata,
            "timestamp": timestamp or timezone.now(),
        }
    )


def dropped_count() -> int:
    """How many of the current request's events were dropped past MAX_EVENTS_PER_TRACE."""
    return _get_buffer().dropped


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
            timestamp=item.get("timestamp") or timezone.now(),
        )
        for item in buffer
    ]

    Event.objects.bulk_create(events)
    count = len(events)
    buffer.clear()
    return count


def clear_buffer() -> None:
    """Discard all buffered events without writing them."""
    _get_buffer().clear()
