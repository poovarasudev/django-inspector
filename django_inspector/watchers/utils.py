"""Helpers shared by watchers."""

import time
import traceback


def elapsed_ms(start: float) -> float:
    """Milliseconds since ``start`` (a time.monotonic() value), rounded to 2 dp."""
    return round((time.monotonic() - start) * 1000, 2)


def extract_origin() -> dict:
    """
    Walk the call stack to find the application code that triggered the event.
    Skips django internals and django-inspector's own frames.
    """
    skip_patterns = (
        "django_inspector",
        "django/db",
        "django/core",
        "django/utils",
        "django/test",
    )
    for frame_info in reversed(traceback.extract_stack()):
        filename = frame_info.filename
        # Skip Python internals, Django internals, and our own code
        if any(pat in filename for pat in skip_patterns):
            continue
        if "site-packages" in filename:
            continue
        if "<" in filename:  # <frozen>, <string>, etc.
            continue
        # Skip standard library modules
        if "/lib/python" in filename and "/tests/" not in filename:
            continue
        return {
            "file": filename,
            "line": frame_info.lineno,
            "function": frame_info.name,
        }
    return {"file": None, "line": None, "function": None}
