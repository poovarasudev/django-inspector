from django.db import models
from django.utils import timezone


class Event(models.Model):
    """
    Stores a single watcher event correlated to a request trace.

    Fields:
    - trace_id: UUID hex of the originating request (from InspectorMiddleware)
    - event_type: dotted string identifying the watcher + event kind
                  e.g. "request.started", "sql.query", "exception.raised"
    - timestamp: when the event was captured (set by the watcher, not at write
                 time; a request event is stamped with the request's start)
    - metadata: JSON blob of event-specific data (query, status code, etc.)
    """

    trace_id = models.CharField(max_length=32, db_index=True)
    event_type = models.CharField(max_length=128, db_index=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        app_label = "django_inspector"
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["trace_id", "timestamp"]),
            # Dashboard lists filter by type and page newest first.
            models.Index(fields=["event_type", "-timestamp"], name="inspector_type_ts_idx"),
        ]

    def __str__(self):
        return f"Event({self.event_type}, trace={self.trace_id[:8]}…)"
