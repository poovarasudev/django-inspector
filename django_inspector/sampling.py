"""
Request sampling for django-inspector.

Controls what fraction of successful requests have their events captured.
Error responses (5xx) and slow requests are always captured regardless of rate.

By default the decision is made when the response is ready, so every request
pays the full capture cost. With EARLY_SAMPLING the request is picked (or not)
when it starts: requests that aren't picked record only the request and
exception watchers' events, which are kept if the request fails or is slow.

Requirements: SAMP-01..05
"""

import random
import time
from contextvars import ContextVar

# Watchers that still record when a request wasn't picked by early sampling:
# they are cheap, and they are what an error or slow request needs to show.
LIGHT_WATCHERS = frozenset({"request", "exception"})

_detail_var: ContextVar[bool] = ContextVar("inspector_detail", default=True)


def early_pick():
    """
    With EARLY_SAMPLING and a rate below 1: True if this request is picked for
    full capture, False if not. None when the decision is left to the end.
    """
    from django_inspector.conf import inspector_settings

    rate = inspector_settings.SAMPLING_RATE
    if not inspector_settings.EARLY_SAMPLING or rate >= 1.0:
        return None
    if early_pick is False:
        return False
    return random.random() < rate


def start_detail(enabled: bool):
    """Set whether detailed watchers capture in this request; returns a reset token."""
    return _detail_var.set(enabled)


def reset_detail(token) -> None:
    _detail_var.reset(token)


def detail_enabled() -> bool:
    """False while a request that early sampling didn't pick is running."""
    return _detail_var.get()


def compute_sampling_decision(request, response, early_pick=None) -> bool:
    """
    Compute the final sampling decision given the completed response.

    - SAMP-01: SAMPLING_RATE controls the fraction of successful requests captured.
    - SAMP-02: Error responses (5xx) are always captured regardless of rate.
    - SAMP-03: Slow requests (>= SLOW_REQUEST_THRESHOLD_MS) are always captured.
    - SAMP-04: SAMPLING_RATE=1.0 (default) captures everything.
    - SAMP-05: SAMPLING_RATE=0.0 captures nothing except errors and slow requests.

    ``early_pick`` is the EARLY_SAMPLING decision: True keeps the request,
    False keeps it only if it failed or was slow, None rolls the dice now.
    """
    from django_inspector.conf import inspector_settings

    rate = inspector_settings.SAMPLING_RATE
    if rate >= 1.0 or early_pick is True:
        return True

    status = getattr(response, "status_code", None)
    if status is not None and status >= 500:
        return True

    slow_threshold = inspector_settings.SLOW_REQUEST_THRESHOLD_MS
    start = getattr(request, "_inspector_start_time", None)
    if start is not None:
        latency_ms = (time.monotonic() - start) * 1000
        if latency_ms >= slow_threshold:
            return True

    if early_pick is False:
        return False
    return random.random() < rate
