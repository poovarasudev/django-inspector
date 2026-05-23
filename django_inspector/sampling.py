"""
Request sampling for django-inspector.

Controls what fraction of successful requests have their events captured.
Error responses (5xx) and slow requests are always captured regardless of rate.

Requirements: SAMP-01..05
"""

import random
import time
from contextvars import ContextVar

_sampled_var: ContextVar[bool] = ContextVar("inspector_sampled", default=True)


def set_sampled(sampled: bool) -> None:
    """Set the sampling decision for the current request context."""
    _sampled_var.set(sampled)


def is_sampled() -> bool:
    """Return True if the current request's events should be persisted."""
    return _sampled_var.get()


def compute_sampling_decision(request, response) -> bool:
    """
    Compute the final sampling decision given the completed response.

    - SAMP-01: SAMPLING_RATE controls the fraction of successful requests captured.
    - SAMP-02: Error responses (5xx) are always captured regardless of rate.
    - SAMP-03: Slow requests (>= SLOW_REQUEST_THRESHOLD_MS) are always captured.
    - SAMP-04: SAMPLING_RATE=1.0 (default) captures everything.
    - SAMP-05: SAMPLING_RATE=0.0 captures nothing except errors and slow requests.
    """
    from django_inspector.conf import inspector_settings

    rate = inspector_settings.SAMPLING_RATE
    if rate >= 1.0:
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

    return random.random() < rate
