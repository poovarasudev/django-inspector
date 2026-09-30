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

    # Derived at flush time from the masked metadata (storage/columns.py).
    # NULL (not "") means "not derived for this event type", which an empty
    # string couldn't distinguish, so these stay nullable despite DJ001.
    # Requests-list filters run after the (event_type, -timestamp) index has
    # narrowed the rows to request.completed, so these get no index of
    # their own.
    method = models.CharField(max_length=16, null=True, blank=True)  # noqa: DJ001
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    path = models.CharField(max_length=1024, null=True, blank=True)  # noqa: DJ001
    duration_ms = models.FloatField(null=True, blank=True)

    # Groups like events: SQL by normalised query shape, exceptions by type
    # plus the innermost application frame's file and function.
    fingerprint = models.CharField(max_length=16, null=True, blank=True)  # noqa: DJ001

    # Links a trace to the trace that started it (e.g. a Celery task to its
    # request). Only a column in this phase; a later phase fills it.
    parent_trace_id = models.CharField(max_length=32, null=True, blank=True, db_index=True)  # noqa: DJ001

    # Keeps a trace's events out of RETENTION_HOURS pruning and inspector_cleanup.
    pinned = models.BooleanField(default=False)

    class Meta:
        app_label = "django_inspector"
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["trace_id", "timestamp"]),
            # Dashboard lists filter by type and page newest first.
            models.Index(fields=["event_type", "-timestamp"], name="inspector_type_ts_idx"),
            models.Index(fields=["event_type", "fingerprint"], name="inspector_type_fp_idx"),
        ]

    def __str__(self):
        return f"Event({self.event_type}, trace={self.trace_id[:8]}…)"
