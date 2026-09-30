"""
Retention: deleting old events.

Events accumulate until deleted. With RETENTION_HOURS set, the middleware
prunes old events after a flush, at most once every PRUNE_INTERVAL_SECONDS
per process and at most PRUNE_BATCH_SIZE rows at a time, so the cost on the
request path stays small and bounded. `inspector_cleanup` deletes in batches
too, for scheduled clean-ups of larger backlogs.
"""

import datetime
import threading
import time

from django.utils import timezone

from django_inspector.conf import inspector_settings

PRUNE_INTERVAL_SECONDS = 300
PRUNE_BATCH_SIZE = 1000

_prune_lock = threading.Lock()
_last_prune = None  # time.monotonic() of the last prune in this process


def delete_older_than(cutoff, batch_size=10000, max_batches=None) -> int:
    """
    Delete events with a timestamp before ``cutoff``, ``batch_size`` rows at
    a time (short transactions, no long table locks). Returns rows deleted.
    """
    from django_inspector.storage.models import Event

    deleted = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        ids = list(
            Event.objects.filter(timestamp__lt=cutoff)
            .order_by("timestamp")
            .values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            break
        deleted += Event.objects.filter(id__in=ids).delete()[0]
        batches += 1
    return deleted


def maybe_prune() -> int:
    """
    Delete up to PRUNE_BATCH_SIZE events older than RETENTION_HOURS, unless
    this process pruned in the last PRUNE_INTERVAL_SECONDS. Returns rows
    deleted. Does nothing when RETENTION_HOURS is None.
    """
    global _last_prune

    hours = inspector_settings.RETENTION_HOURS
    if not hours:
        return 0
    now = time.monotonic()
    if _last_prune is not None and now - _last_prune < PRUNE_INTERVAL_SECONDS:
        return 0
    if not _prune_lock.acquire(blocking=False):
        return 0  # another thread is pruning
    try:
        if _last_prune is not None and now - _last_prune < PRUNE_INTERVAL_SECONDS:
            return 0
        _last_prune = now
        cutoff = timezone.now() - datetime.timedelta(hours=hours)
        return delete_older_than(cutoff, batch_size=PRUNE_BATCH_SIZE, max_batches=1)
    finally:
        _prune_lock.release()


def _reset_prune_clock():
    """Forget the last prune time. Used in tests."""
    global _last_prune
    _last_prune = None
