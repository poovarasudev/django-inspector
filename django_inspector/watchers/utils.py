"""Helpers shared by watchers."""

import os
import sys
import sysconfig
import time

import django

import django_inspector


def elapsed_ms(start: float) -> float:
    """Milliseconds since ``start`` (a time.monotonic() value), rounded to 2 dp."""
    return round((time.monotonic() - start) * 1000, 2)


def _dir_prefix(path):
    return os.path.normcase(os.path.abspath(path)).rstrip(os.sep) + os.sep


# Frames under these directories are never "app code": django-inspector
# itself, Django, and the standard library. Installed packages are caught by
# their site-packages / dist-packages path segment.
_LIBRARY_PREFIXES = tuple(
    {
        _dir_prefix(os.path.dirname(django_inspector.__file__)),
        _dir_prefix(os.path.dirname(django.__file__)),
    }
    | {
        _dir_prefix(p)
        for p in (sysconfig.get_paths().get("stdlib"), sysconfig.get_paths().get("platstdlib"))
        if p
    }
)
_LIBRARY_SEGMENTS = ("/site-packages/", "/dist-packages/")
_library_file_cache = {}


def _is_library_file(filename: str) -> bool:
    cached = _library_file_cache.get(filename)
    if cached is not None:
        return cached
    if filename.startswith("<"):  # <frozen ...>, <string>, ...
        result = True
    else:
        normalized = os.path.normcase(filename)
        slashed = filename.replace("\\", "/").lower()
        result = normalized.startswith(_LIBRARY_PREFIXES) or any(
            segment in slashed for segment in _LIBRARY_SEGMENTS
        )
    if len(_library_file_cache) < 10000:
        _library_file_cache[filename] = result
    return result


def extract_origin() -> dict:
    """
    The innermost application frame on the call stack: the code that
    triggered the event. Skips django-inspector, Django, the standard library
    and installed packages. Walks frame objects directly, which is far cheaper
    than traceback.extract_stack() (that reads source lines for every frame).
    """
    frame = sys._getframe(1)
    while frame is not None:
        code = frame.f_code
        if not _is_library_file(code.co_filename):
            return {"file": code.co_filename, "line": frame.f_lineno, "function": code.co_name}
        frame = frame.f_back
    return {"file": None, "line": None, "function": None}
