# Cache & Template Watchers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture Django cache operations and template renders as trace-correlated events, and browse them on new dashboard pages.

**Architecture:**
- `CacheWatcher` wraps the methods of each backend class configured in `settings.CACHES`, at the class level.
- `TemplateWatcher` wraps `django.template.base.Template._render`.
- Both watchers build their wrappers as closures that call `self.record(...)`. Per-trace state lives in `ContextVar`s.
- The middleware stops opening a trace for dashboard URLs, so the dashboard never records itself.
- New dashboard pages copy the existing Queries and Exceptions pattern.

**Tech Stack:** Python ≥ 3.8, Django ≥ 4.0 (developed against 5.2), pytest + pytest-django, HTMX dashboard with server-rendered templates.

**Spec:** `docs/superpowers/specs/2026-09-30-cache-template-watchers-design.md`

## Global Constraints

- Python ≥ 3.8, Django ≥ 4.0. **No new runtime dependencies.** Don't use 3.10+ syntax: no `X | Y` type unions and no `match`.
- `DJANGO_INSPECTOR` keys are public and additive. The only new settings are `WATCHERS["cache"] = False` and `WATCHERS["template"] = False`. No model change, so no migration.
- Middleware overhead stays under ~2 ms p50. With no active trace, a wrapper only reads a `ContextVar` and then calls the original.
- Must work under WSGI **and** ASGI. Per-trace state uses `ContextVar`, never `threading.local`.
- Never break the host request. Inspector errors are logged with `logger.warning(..., exc_info=True)` and re-raised only when `INSPECTOR_RAISE_ERRORS` is on. The host's return values and exceptions pass through unchanged.
- One logger only: `logging.getLogger("django_inspector")`. No `print`.
- Read settings via `django_inspector.conf.inspector_settings`.
- Watchers record only through `self.record(...)`.
- Never record cached values or template context values. Only sizes, types and key names.
- Test command: `uv run --extra dev python -m pytest` (the `python -m` is required so `tests.settings` imports).
- Commits use conventional style with a module scope, e.g. `feat(cache-watcher): …`. Put requirement IDs in the body.

## Review Focus

1. **`get_many` / `delete_many` given a generator of keys.** The call must still work on the real backend, and the event must list the keys. The wrapper must turn the iterator into a list *before* the call and pass that list on (Task 3).
2. **Third-party backends with extra arguments,** e.g. django-redis `set(..., nx=True)` or `get(..., client=...)`. Unknown positional and keyword arguments must reach the backend untouched (Task 2).
3. **Paths that only look like the dashboard.** With prefix `inspector/`, a host page at `/inspectors-guide/` must still be traced. An empty or `/` prefix must not turn tracing off for the whole site (Task 1).
4. **A template render that raises partway through a tree.** The next render in the same trace must be top-level (`depth 0`, `parent_id None`), not nested under the failed one (Task 4).
5. **`cache.get` with a positional `default` and `version`,** e.g. `cache.get("k", "fallback", 2)`. On a miss it must return `"fallback"`, and it must honour the version (Task 2).

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `django_inspector/ignores.py` | + `is_dashboard_path()` | 1 |
| `django_inspector/middleware.py` | skip dashboard paths | 1 |
| `django_inspector/watchers/request.py` | delegate to `is_dashboard_path` | 1 |
| `django_inspector/watchers/utils.py` (new) | `extract_origin()`, `elapsed_ms()` shared by watchers | 2 |
| `django_inspector/watchers/sql.py` | use `watchers.utils.extract_origin` | 2 |
| `django_inspector/watchers/cache.py` (new) | `CacheWatcher`, `CACHE_EVENT_TYPES` | 2, 3 |
| `django_inspector/watchers/template.py` (new) | `TemplateWatcher` | 4 |
| `django_inspector/conf.py` | `WATCHERS` defaults for cache/template | 2, 4 |
| `django_inspector/apps.py` | import new watcher modules | 2, 4 |
| `django_inspector/dashboard/views.py`, `urls.py` | cache/template pages, request-detail integration | 5, 6, 7 |
| `django_inspector/templates/inspector/cache/*`, `template_renders/*` (new) | page templates | 5, 6 |
| `django_inspector/templates/inspector/_sidebar.html`, `base.html`, `requests/detail.html` | nav, CSS, trace view | 5, 6, 7 |
| `tests/settings.py`, `tests/urls.py` (new), `tests/templates/` (new) | test harness | 4, 5 |
| `tests/test_ignores.py`, `tests/test_cache_watcher.py` (new), `tests/test_template_watcher.py` (new), `tests/test_dashboard_pages.py` (new) | tests | 1–7 |
| `docs/roadmap.md`, `docs/requirements.md`, `docs/project.md`, `CLAUDE.md` | status updates | 8 |

---

### Task 1: Dashboard requests are not traced

**Files:**
- Modify: `django_inspector/ignores.py` (append after `should_ignore_path`)
- Modify: `django_inspector/middleware.py` (the `should_ignore_path` check in `__call__`)
- Modify: `django_inspector/watchers/request.py` (`should_ignore_request`)
- Test: `tests/test_ignores.py`

**Interfaces:**
- Produces: `django_inspector.ignores.is_dashboard_path(path: str) -> bool`

- [ ] **Step 1: Write the failing tests.** Add `is_dashboard_path` to the existing `from django_inspector.ignores import (...)` block in `tests/test_ignores.py`, then append:

```python
class TestIsDashboardPath:
    @override_settings(DJANGO_INSPECTOR={"DASHBOARD_URL_PREFIX": "inspector/"})
    def test_paths_under_prefix_match(self):
        assert is_dashboard_path("/inspector/") is True
        assert is_dashboard_path("/inspector/queries/12/") is True
        assert is_dashboard_path("/inspector") is True

    @override_settings(DJANGO_INSPECTOR={"DASHBOARD_URL_PREFIX": "inspector/"})
    def test_lookalike_paths_do_not_match(self):
        assert is_dashboard_path("/inspectors-guide/") is False
        assert is_dashboard_path("/api/inspector/") is False

    @override_settings(DJANGO_INSPECTOR={"DASHBOARD_URL_PREFIX": "/ops/inspector"})
    def test_prefix_slashes_are_normalised(self):
        assert is_dashboard_path("/ops/inspector/requests/") is True

    @pytest.mark.parametrize("prefix", ["", "/"])
    def test_empty_prefix_matches_nothing(self, prefix):
        with override_settings(DJANGO_INSPECTOR={"DASHBOARD_URL_PREFIX": prefix}):
            assert is_dashboard_path("/") is False
            assert is_dashboard_path("/api/users/") is False


@pytest.mark.django_db
class TestDashboardPathsAreNotTraced(TestCase):
    """The dashboard's own SQL/template/cache activity must never become events."""

    def _run(self, path):
        from django.db import connection
        from django_inspector.middleware import InspectorMiddleware

        request = RequestFactory().get(path)

        def view(req):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return HttpResponse("ok")

        InspectorMiddleware(view)(request)
        return request

    def test_dashboard_request_produces_no_events(self):
        Event.objects.all().delete()
        request = self._run("/inspector/queries/")
        assert not hasattr(request, "inspector_trace_id")
        assert Event.objects.count() == 0

    def test_lookalike_path_is_still_traced(self):
        Event.objects.all().delete()
        request = self._run("/inspectors-guide/")
        assert hasattr(request, "inspector_trace_id")
        assert Event.objects.filter(event_type="request.completed").exists()
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_ignores.py -q`
Expected: FAIL with `ImportError: cannot import name 'is_dashboard_path'`.

- [ ] **Step 3: Implement.** Append to `django_inspector/ignores.py`:

```python
def is_dashboard_path(path: str) -> bool:
    """
    Return True if ``path`` is under DASHBOARD_URL_PREFIX.

    The middleware skips tracing for these paths so the dashboard's own SQL,
    template and cache activity never becomes events. The prefix must match
    where the host mounts ``django_inspector.dashboard.urls``. An empty or "/"
    prefix matches nothing: treating the whole site as the dashboard would
    silently switch the inspector off.
    """
    from django_inspector.conf import inspector_settings

    prefix = (inspector_settings.DASHBOARD_URL_PREFIX or "").strip("/")
    if not prefix:
        return False
    prefix = "/" + prefix
    return path == prefix or path.startswith(prefix + "/")
```

In `django_inspector/middleware.py`, replace:

```python
        from django_inspector.ignores import should_ignore_path
        if should_ignore_path(request.path):
            return self.get_response(request)
```

with:

```python
        from django_inspector.ignores import is_dashboard_path, should_ignore_path
        if should_ignore_path(request.path) or is_dashboard_path(request.path):
            return self.get_response(request)
```

In `django_inspector/watchers/request.py`, replace the body of `should_ignore_request`:

```python
    def should_ignore_request(self, request) -> bool:
        """Return True if this request should not be recorded (REQ-06)."""
        from django_inspector.ignores import is_dashboard_path

        return is_dashboard_path(getattr(request, "path", ""))
```

In `django_inspector/conf.py`, change the `DASHBOARD_URL_PREFIX` line comment to:

```python
    "DASHBOARD_URL_PREFIX": "inspector/",  # must match where the host mounts django_inspector.dashboard.urls; requests under it are never traced
```

- [ ] **Step 4: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_ignores.py tests/test_request_watcher.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run --extra dev python -m pytest -q`
Expected: all PASS (128 existing + the new tests).

- [ ] **Step 6: Commit**

```bash
git add django_inspector/ignores.py django_inspector/middleware.py django_inspector/watchers/request.py django_inspector/conf.py tests/test_ignores.py
git commit -m "fix(middleware): do not trace dashboard requests

Dashboard views ran under a trace, so their auth/session SQL was stored as
orphan events with no request.completed. is_dashboard_path() now gates the
middleware and the request watcher; lookalike paths and empty prefixes are
handled explicitly."
```

---

### Task 2: Cache watcher — lifecycle and single-key operations

**Files:**
- Create: `django_inspector/watchers/utils.py`
- Modify: `django_inspector/watchers/sql.py` (remove `_extract_origin`, import from utils)
- Create: `django_inspector/watchers/cache.py`
- Modify: `django_inspector/conf.py` (`DEFAULTS["WATCHERS"]`)
- Modify: `django_inspector/apps.py` (`_autodiscover_watchers`)
- Test: `tests/test_cache_watcher.py`

**Interfaces:**
- Produces:
  - `django_inspector.watchers.utils.extract_origin() -> dict` with keys `file`, `line`, `function`
  - `django_inspector.watchers.utils.elapsed_ms(start: float) -> float`, where `start` comes from `time.monotonic()` and the result is rounded to 2 dp
  - `django_inspector.watchers.cache.CacheWatcher`, with `watcher_name = "cache"`
  - `django_inspector.watchers.cache.CACHE_EVENT_TYPES`, a tuple of `"cache.<op>"` strings. Task 3 extends it, and Tasks 5 and 7 use it.
  - `django_inspector.watchers.cache.WRAPPED_METHODS`, a tuple of method names
  - Event types `cache.get`, `cache.set`, `cache.add`, `cache.delete`, `cache.clear`
  - Metadata keys:
    - on every event: `operation`, `alias`, `backend`, `duration_ms`, `origin_file`, `origin_line`, `origin_function`, and `error` (only when the call failed)
    - per operation: `key`, `hit`, `ttl_seconds`, `value_type`, `value_size_bytes`, `stored`, `deleted`

- [ ] **Step 1: Move the origin helper.** Create `django_inspector/watchers/utils.py`:

```python
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
```

In `django_inspector/watchers/sql.py`:
- Delete the whole `def _extract_origin() -> dict:` function.
- Add `from django_inspector.watchers.utils import extract_origin` to the imports.
- Change `origin = _extract_origin()` to `origin = extract_origin()`.
- If nothing else uses `import traceback` after this, remove it.

Run: `uv run --extra dev python -m pytest tests/test_sql_watcher.py -q`
Expected: all PASS (this is a pure move).

- [ ] **Step 2: Write the failing tests.** Create `tests/test_cache_watcher.py`:

```python
"""Tests for the Cache Watcher (CACHE-01..CACHE-07)."""

import pytest
from asgiref.sync import async_to_sync
from django.core.cache import caches
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase, override_settings

from django_inspector.conf import inspector_settings
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.cache import CacheWatcher

LOCMEM = "django.core.cache.backends.locmem.LocMemCache"


class BrokenCache(LocMemCache):
    """A backend whose get() fails, like a cache server that is down."""

    def get(self, key, default=None, version=None):
        raise ConnectionError("cache down")


class KwargsCache(LocMemCache):
    """Mimics third-party backends (e.g. django-redis) whose methods take extra arguments."""

    received_nx = None

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None, nx=False):
        KwargsCache.received_nx = nx
        return super().set(key, value, timeout=timeout, version=version)


TEST_CACHES = {
    "default": {"BACKEND": LOCMEM, "LOCATION": "inspector-tests-default"},
    "other": {"BACKEND": LOCMEM, "LOCATION": "inspector-tests-other"},
    "broken": {"BACKEND": "tests.test_cache_watcher.BrokenCache", "LOCATION": "inspector-tests-broken"},
    "kwargs": {"BACKEND": "tests.test_cache_watcher.KwargsCache", "LOCATION": "inspector-tests-kwargs"},
}


def cache_events():
    return [e for e in _get_buffer() if e["event_type"].startswith("cache.")]


@override_settings(CACHES=TEST_CACHES)
class TestCacheWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert CacheWatcher.watcher_name == "cache"

    def test_off_by_default(self):
        assert inspector_settings.watcher_enabled("cache") is False

    def test_enable_wraps_and_disable_restores_own_methods(self):
        original = LocMemCache.__dict__["get"]
        watcher = CacheWatcher()
        watcher.enable()
        try:
            assert LocMemCache.__dict__["get"] is not original
            assert LocMemCache.__dict__["get"].__wrapped__ is original
        finally:
            watcher.disable()
        assert LocMemCache.__dict__["get"] is original

    def test_enable_and_disable_are_idempotent(self):
        original = LocMemCache.__dict__["set"]
        watcher = CacheWatcher()
        watcher.enable()
        watcher.enable()
        try:
            assert LocMemCache.__dict__["set"].__wrapped__ is original
        finally:
            watcher.disable()
        watcher.disable()
        assert LocMemCache.__dict__["set"] is original

    def test_second_watcher_does_not_double_wrap(self):
        original = LocMemCache.__dict__["get"]
        first, second = CacheWatcher(), CacheWatcher()
        first.enable()
        second.enable()
        try:
            assert LocMemCache.__dict__["get"].__wrapped__ is original
        finally:
            second.disable()
            first.disable()
        assert LocMemCache.__dict__["get"] is original

    def test_inherited_methods_are_removed_on_disable(self):
        watcher = CacheWatcher()
        with override_settings(CACHES={"default": TEST_CACHES["kwargs"]}):
            watcher.enable()
        try:
            assert "get" in KwargsCache.__dict__
        finally:
            watcher.disable()
        assert "get" not in KwargsCache.__dict__
        assert KwargsCache.get is LocMemCache.get

    def test_unimportable_backend_is_skipped_with_warning(self):
        config = {**TEST_CACHES, "missing": {"BACKEND": "no.such.CacheBackend"}}
        watcher = CacheWatcher()
        with override_settings(CACHES=config):
            with self.assertLogs("django_inspector", level="WARNING") as logs:
                watcher.enable()
        try:
            assert any("missing" in line for line in logs.output)
            assert getattr(LocMemCache.__dict__["get"], "_inspector_wrapped", False)
        finally:
            watcher.disable()


@override_settings(CACHES=TEST_CACHES)
class CacheWatcherTestCase(TestCase):
    """Watcher enabled, an active trace, empty caches and an empty buffer."""

    def setUp(self):
        for alias in ("default", "other", "kwargs"):
            caches[alias].clear()
        self.watcher = CacheWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()


class TestSingleKeyOperations(CacheWatcherTestCase):
    def test_get_hit(self):
        caches["default"].set("greeting", "hello")
        clear_buffer()
        assert caches["default"].get("greeting") == "hello"
        [event] = cache_events()
        meta = event["metadata"]
        assert event["event_type"] == "cache.get"
        assert meta["operation"] == "get"
        assert meta["key"] == "greeting"
        assert meta["hit"] is True
        assert meta["alias"] == "default"
        assert meta["backend"] == LOCMEM
        assert meta["duration_ms"] >= 0

    def test_get_miss_returns_callers_default(self):
        assert caches["default"].get("absent", "fallback") == "fallback"
        [event] = cache_events()
        assert event["metadata"]["hit"] is False

    def test_get_with_positional_default_and_version(self):
        caches["default"].set("k", "v2", version=2)
        clear_buffer()
        assert caches["default"].get("k", "fallback", 1) == "fallback"
        assert caches["default"].get("k", "fallback", 2) == "v2"
        assert [e["metadata"]["hit"] for e in cache_events()] == [False, True]

    def test_stored_none_is_a_hit(self):
        caches["default"].set("nothing", None)
        clear_buffer()
        assert caches["default"].get("nothing", "fallback") is None
        assert cache_events()[0]["metadata"]["hit"] is True

    def test_set_records_ttl_and_size_but_never_the_value(self):
        caches["default"].set("name", "héllo", timeout=30)
        [event] = cache_events()
        meta = event["metadata"]
        assert event["event_type"] == "cache.set"
        assert meta["key"] == "name"
        assert meta["ttl_seconds"] == 30
        assert meta["value_type"] == "str"
        assert meta["value_size_bytes"] == 6
        assert "héllo" not in str(meta)

    def test_set_default_timeout_uses_backend_default(self):
        caches["default"].set("k", b"abc")
        meta = cache_events()[0]["metadata"]
        assert meta["ttl_seconds"] == 300
        assert meta["value_size_bytes"] == 3

    def test_set_timeout_none_means_never_expires(self):
        caches["default"].set("k", 1, timeout=None)
        assert cache_events()[0]["metadata"]["ttl_seconds"] is None

    def test_value_size_is_none_for_other_types(self):
        caches["default"].set("k", {"a": 1})
        meta = cache_events()[0]["metadata"]
        assert meta["value_type"] == "dict"
        assert meta["value_size_bytes"] is None

    def test_add_records_stored_flag(self):
        caches["default"].add("k", "first")
        caches["default"].add("k", "second")
        assert [e["metadata"]["stored"] for e in cache_events()] == [True, False]

    def test_delete_records_deleted_flag(self):
        caches["default"].set("k", 1)
        clear_buffer()
        caches["default"].delete("k")
        caches["default"].delete("k")
        events = cache_events()
        assert [e["event_type"] for e in events] == ["cache.delete", "cache.delete"]
        assert [e["metadata"]["deleted"] for e in events] == [True, False]

    def test_clear_records_star_key(self):
        caches["default"].clear()
        [event] = cache_events()
        assert event["event_type"] == "cache.clear"
        assert event["metadata"]["key"] == "*"

    def test_records_origin_in_app_code(self):
        caches["default"].get("k")
        meta = cache_events()[0]["metadata"]
        assert "test_cache_watcher" in meta["origin_file"]
        assert meta["origin_line"] is not None

    def test_get_or_set_records_the_underlying_get_and_add(self):
        assert caches["default"].get_or_set("k", "computed") == "computed"
        ops = [e["metadata"]["operation"] for e in cache_events()]
        assert ops[:2] == ["get", "add"]


class TestCacheEventContext(CacheWatcherTestCase):
    def test_events_carry_trace_id(self):
        caches["default"].get("k")
        assert cache_events()[0]["trace_id"] == self.trace_id

    def test_no_events_without_active_trace(self):
        clear_trace_id(self.token)
        self.token = None
        assert caches["default"].get("k", "d") == "d"
        caches["default"].set("k", 1)
        assert cache_events() == []

    def test_alias_resolved_when_two_aliases_share_a_class(self):
        caches["default"].get("k")
        caches["other"].get("k")
        assert [e["metadata"]["alias"] for e in cache_events()] == ["default", "other"]

    def test_backend_error_is_recorded_and_reraised(self):
        with pytest.raises(ConnectionError, match="cache down"):
            caches["broken"].get("k")
        [event] = cache_events()
        assert event["metadata"]["error"] == "ConnectionError: cache down"
        assert event["metadata"]["alias"] == "broken"
        assert "hit" not in event["metadata"]

    def test_extra_backend_arguments_pass_through(self):
        KwargsCache.received_nx = None
        caches["kwargs"].set("k", "v", nx=True)
        assert KwargsCache.received_nx is True
        [event] = cache_events()
        assert event["event_type"] == "cache.set"
        assert event["metadata"]["alias"] == "kwargs"
        assert event["metadata"]["backend"] == "tests.test_cache_watcher.KwargsCache"

    def test_async_aget_is_recorded(self):
        caches["default"].set("k", "v")
        clear_buffer()

        async def run():
            return await caches["default"].aget("k")

        assert async_to_sync(run)() == "v"
        [event] = cache_events()
        assert event["event_type"] == "cache.get"
        assert event["trace_id"] == self.trace_id

    def test_card_number_in_key_is_masked(self):
        caches["default"].get("card:4111111111111111")
        assert cache_events()[0]["metadata"]["key"] == "***REDACTED***"
```

- [ ] **Step 3: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_cache_watcher.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_inspector.watchers.cache'`.

- [ ] **Step 4: Implement.** Create `django_inspector/watchers/cache.py`:

```python
"""
Cache Watcher — captures Django cache operations with key, alias, backend,
TTL, hit/miss status and timing.

Wraps methods on every backend class configured in settings.CACHES at the
class level, so per-thread backend instances and the async API (which calls
the sync methods through sync_to_async) are all covered.

Requirements: CACHE-01..CACHE-07
"""

import functools
import logging
import time
from contextvars import ContextVar

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.utils.module_loading import import_string

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms, extract_origin

logger = logging.getLogger("django_inspector")

# Positional parameter names of each wrapped method, per Django's BaseCache.
# Used to read arguments however the caller passed them; extra backend-specific
# arguments are passed through untouched.
_PARAMS = {
    "get": ("key", "default", "version"),
    "set": ("key", "value", "timeout", "version"),
    "add": ("key", "value", "timeout", "version"),
    "delete": ("key", "version"),
    "clear": (),
}

WRAPPED_METHODS = tuple(_PARAMS)
CACHE_EVENT_TYPES = tuple("cache." + name for name in WRAPPED_METHODS)

# True while a wrapped cache call runs in this context. Nested calls
# (e.g. BaseCache.get_many -> get) pass straight through, so only the
# outermost operation is recorded.
_in_cache_op = ContextVar("inspector_in_cache_op", default=False)

# Passed to get() as `default` so a miss can't be confused with a stored None.
_MISS = object()
_UNRESOLVED = object()


class CacheWatcher(BaseWatcher):
    """
    Captures cache operations by wrapping backend classes.

    - CACHE-01: get — key, alias, hit/miss, duration
    - CACHE-02: set/add — key, alias, TTL, value size (never the value)
    - CACHE-03: delete/clear — key ("*" for clear), alias
    - CACHE-05: trace correlation via BaseWatcher.record()
    - CACHE-06: installs and removes cleanly; idempotent
    - CACHE-07: off by default (conf.DEFAULTS["WATCHERS"]["cache"])
    """

    watcher_name = "cache"

    def __init__(self):
        super().__init__()
        # (class, method name, original function or None if it was inherited)
        self._patches = []

    def install_hooks(self):
        for cls in _configured_backend_classes():
            for name in WRAPPED_METHODS:
                current = getattr(cls, name, None)
                if current is None or getattr(current, "_inspector_wrapped", False):
                    continue
                own = name in cls.__dict__
                setattr(cls, name, _make_wrapper(self, name, current))
                self._patches.append((cls, name, current if own else None))

    def remove_hooks(self):
        for cls, name, original in reversed(self._patches):
            if original is None:
                try:
                    delattr(cls, name)
                except AttributeError:
                    pass
            else:
                setattr(cls, name, original)
        self._patches = []

    def record_operation(self, cache, operation, bound, result, duration_ms, error):
        """Build and record one cache.* event. Never raises unless INSPECTOR_RAISE_ERRORS."""
        try:
            origin = extract_origin()
            metadata = {
                "operation": operation,
                "alias": _resolve_alias(cache),
                "backend": type(cache).__module__ + "." + type(cache).__qualname__,
                "duration_ms": duration_ms,
                "origin_file": origin["file"],
                "origin_line": origin["line"],
                "origin_function": origin["function"],
            }
            metadata.update(_operation_fields(cache, operation, bound, result, error is None))
            if error is not None:
                metadata["error"] = "%s: %s" % (type(error).__name__, error)
            self.record("cache." + operation, metadata)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording cache.%s event", operation, exc_info=True)


def _make_wrapper(watcher, operation, original):
    @functools.wraps(original)
    def wrapper(cache, *args, **kwargs):
        if _in_cache_op.get() or get_current_trace_id() is None:
            return original(cache, *args, **kwargs)

        try:
            bound, call_args, call_kwargs = _prepare_call(operation, args, kwargs)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error preparing cache.%s event", operation, exc_info=True)
            return original(cache, *args, **kwargs)

        token = _in_cache_op.set(True)
        start = time.monotonic()
        try:
            result = original(cache, *call_args, **call_kwargs)
        except Exception as exc:
            _in_cache_op.reset(token)
            watcher.record_operation(cache, operation, bound, None, elapsed_ms(start), exc)
            raise
        except BaseException:
            _in_cache_op.reset(token)
            raise
        _in_cache_op.reset(token)
        watcher.record_operation(cache, operation, bound, result, elapsed_ms(start), None)

        if operation == "get" and result is _MISS:
            return bound.get("default")
        return result

    wrapper._inspector_wrapped = True
    return wrapper


def _prepare_call(operation, args, kwargs):
    """
    Return (bound, call_args, call_kwargs): the named arguments of the call and
    the arguments to pass to the original. For get(), the caller's default is
    swapped for _MISS so a miss is detectable.
    """
    bound = dict(zip(_PARAMS[operation], args))
    bound.update(kwargs)
    call_args, call_kwargs = args, kwargs
    if operation == "get":
        call_args, call_kwargs = _replace_arg(operation, call_args, call_kwargs, "default", _MISS)
    return bound, call_args, call_kwargs


def _replace_arg(operation, args, kwargs, name, value):
    """Replace one named argument, whether it was passed positionally or by keyword."""
    index = _PARAMS[operation].index(name)
    if len(args) > index:
        return args[:index] + (value,) + args[index + 1:], kwargs
    return args, dict(kwargs, **{name: value})


def _operation_fields(cache, operation, bound, result, succeeded):
    """Per-operation metadata. Values are never recorded — only their type and size."""
    if operation == "clear":
        return {"key": "*"}
    fields = {"key": str(bound.get("key"))}
    if operation == "get":
        if succeeded:
            fields["hit"] = result is not _MISS
    elif operation in ("set", "add"):
        value = bound.get("value")
        fields["ttl_seconds"] = _ttl_seconds(cache, bound.get("timeout", DEFAULT_TIMEOUT))
        fields["value_type"] = type(value).__name__
        fields["value_size_bytes"] = _value_size(value)
        if operation == "add" and succeeded:
            fields["stored"] = bool(result)
    elif operation == "delete" and succeeded:
        fields["deleted"] = bool(result)
    return fields


def _ttl_seconds(cache, timeout):
    """The effective TTL in seconds; None means the entry never expires."""
    if timeout is DEFAULT_TIMEOUT:
        return getattr(cache, "default_timeout", None)
    return timeout


def _value_size(value):
    """Size in bytes for str/bytes values; None for anything else (no second pickle)."""
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return None


def _resolve_alias(cache):
    """The CACHES alias this backend instance serves, memoised on the instance."""
    alias = getattr(cache, "_inspector_alias", _UNRESOLVED)
    if alias is not _UNRESOLVED:
        return alias
    alias = None
    for name in settings.CACHES:
        try:
            if caches[name] is cache:
                alias = name
                break
        except Exception:
            continue
    try:
        cache._inspector_alias = alias
    except AttributeError:
        pass
    return alias


def _configured_backend_classes():
    """Unique backend classes named in settings.CACHES; unimportable ones are skipped."""
    classes = []
    for alias, config in settings.CACHES.items():
        try:
            cls = import_string(config["BACKEND"])
        except Exception:
            logger.warning(
                "inspector: cannot import cache backend for alias %r; it will not be watched",
                alias,
                exc_info=True,
            )
            continue
        if cls not in classes:
            classes.append(cls)
    return classes


register("cache", CacheWatcher)
```

In `django_inspector/conf.py`, make `DEFAULTS["WATCHERS"]`:

```python
    "WATCHERS": {
        "request": True,
        "sql": True,
        "exception": True,
        "cache": False,     # CACHE-07: opt in with {"WATCHERS": {"cache": True}}
    },
```

In `django_inspector/apps.py` `_autodiscover_watchers`, after the `exception` import block, add:

```python
        try:
            import django_inspector.watchers.cache  # noqa: F401
        except ImportError:
            pass
```

- [ ] **Step 5: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_cache_watcher.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run --extra dev python -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add django_inspector/watchers/utils.py django_inspector/watchers/sql.py django_inspector/watchers/cache.py django_inspector/conf.py django_inspector/apps.py tests/test_cache_watcher.py
git commit -m "feat(cache-watcher): capture get/set/add/delete/clear

Wraps configured backend classes; records key, alias, backend, TTL, value
size (never the value), hit/miss via a sentinel default, origin and errors.
Off by default. Moves extract_origin into watchers/utils for reuse.

Covers CACHE-01, CACHE-02, CACHE-03, CACHE-05, CACHE-06, CACHE-07."
```

---

### Task 3: Cache watcher — multi-key operations

**Files:**
- Modify: `django_inspector/watchers/cache.py`
- Test: `tests/test_cache_watcher.py`

**Interfaces:**
- Consumes: from Task 2, `_PARAMS`, `_prepare_call`, `_replace_arg`, `_operation_fields`, `_ttl_seconds` and `CacheWatcherTestCase`
- Produces:
  - `CACHE_EVENT_TYPES` now also contains `cache.get_many`, `cache.set_many` and `cache.delete_many`
  - Metadata keys: `keys` (list of at most `MAX_RECORDED_KEYS = 100`), `key_count`, `hit_count`, `miss_count`, `failed_keys`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_cache_watcher.py`:

```python
class TestMultiKeyOperations(CacheWatcherTestCase):
    def test_get_many_is_one_event_with_hit_counts(self):
        caches["default"].set("a", 1)
        caches["default"].set("b", 2)
        clear_buffer()
        assert caches["default"].get_many(["a", "b", "c"]) == {"a": 1, "b": 2}
        [event] = cache_events()  # BaseCache.get_many calls get() per key; those are not recorded
        meta = event["metadata"]
        assert event["event_type"] == "cache.get_many"
        assert meta["keys"] == ["a", "b", "c"]
        assert meta["key_count"] == 3
        assert meta["hit_count"] == 2
        assert meta["miss_count"] == 1

    def test_get_many_accepts_a_generator(self):
        caches["default"].set("a", 1)
        caches["default"].set("b", 2)
        clear_buffer()
        assert caches["default"].get_many(k for k in ["a", "b"]) == {"a": 1, "b": 2}
        assert cache_events()[0]["metadata"]["keys"] == ["a", "b"]

    def test_set_many_is_one_event(self):
        assert caches["default"].set_many({"a": 1, "b": 2}, timeout=60) == []
        [event] = cache_events()
        meta = event["metadata"]
        assert event["event_type"] == "cache.set_many"
        assert meta["keys"] == ["a", "b"]
        assert meta["key_count"] == 2
        assert meta["ttl_seconds"] == 60
        assert meta["failed_keys"] == []

    def test_delete_many_accepts_a_generator(self):
        caches["default"].set_many({"a": 1, "b": 2})
        clear_buffer()
        caches["default"].delete_many(k for k in ["a", "b"])
        [event] = cache_events()
        assert event["event_type"] == "cache.delete_many"
        assert event["metadata"]["keys"] == ["a", "b"]
        assert event["metadata"]["key_count"] == 2
        assert caches["default"].get("a") is None

    def test_recorded_keys_are_capped(self):
        keys = ["k%d" % i for i in range(150)]
        caches["default"].get_many(keys)
        meta = cache_events()[0]["metadata"]
        assert len(meta["keys"]) == 100
        assert meta["key_count"] == 150
        assert meta["miss_count"] == 150


@override_settings(CACHES=TEST_CACHES)
class TestMultiKeyLifecycle(TestCase):
    def test_inherited_many_methods_are_wrapped_then_removed(self):
        from django.core.cache.backends.base import BaseCache

        assert "get_many" not in LocMemCache.__dict__
        watcher = CacheWatcher()
        watcher.enable()
        try:
            assert "get_many" in LocMemCache.__dict__
        finally:
            watcher.disable()
        assert "get_many" not in LocMemCache.__dict__
        assert LocMemCache.get_many is BaseCache.get_many
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_cache_watcher.py -q -k "MultiKey"`
Expected: FAIL. `get_many` isn't wrapped yet, so `cache_events()` shows per-key `cache.get` events or nothing.

- [ ] **Step 3: Implement.** In `django_inspector/watchers/cache.py`:

Extend `_PARAMS`:

```python
_PARAMS = {
    "get": ("key", "default", "version"),
    "set": ("key", "value", "timeout", "version"),
    "add": ("key", "value", "timeout", "version"),
    "delete": ("key", "version"),
    "clear": (),
    "get_many": ("keys", "version"),
    "set_many": ("data", "timeout", "version"),
    "delete_many": ("keys", "version"),
}
```

Below `CACHE_EVENT_TYPES` add:

```python
MAX_RECORDED_KEYS = 100
_MULTI_KEY_OPERATIONS = ("get_many", "set_many", "delete_many")
```

Replace `_prepare_call` with:

```python
def _prepare_call(operation, args, kwargs):
    """
    Return (bound, call_args, call_kwargs): the named arguments of the call and
    the arguments to pass to the original. For get(), the caller's default is
    swapped for _MISS so a miss is detectable. For get_many()/delete_many(), an
    iterator of keys is materialised once so both the backend and the event
    see every key.
    """
    bound = dict(zip(_PARAMS[operation], args))
    bound.update(kwargs)
    call_args, call_kwargs = args, kwargs
    if operation == "get":
        call_args, call_kwargs = _replace_arg(operation, call_args, call_kwargs, "default", _MISS)
    elif operation in ("get_many", "delete_many") and "keys" in bound:
        keys = list(bound["keys"])
        bound["keys"] = keys
        call_args, call_kwargs = _replace_arg(operation, call_args, call_kwargs, "keys", keys)
    return bound, call_args, call_kwargs
```

At the top of `_operation_fields`, before the `clear` check, add:

```python
    if operation in _MULTI_KEY_OPERATIONS:
        if operation == "set_many":
            keys = list(bound.get("data") or {})
        else:
            keys = list(bound.get("keys") or [])
        fields = {
            "keys": [str(k) for k in keys[:MAX_RECORDED_KEYS]],
            "key_count": len(keys),
        }
        if operation == "get_many" and succeeded:
            fields["hit_count"] = len(result)
            fields["miss_count"] = len(keys) - len(result)
        elif operation == "set_many":
            fields["ttl_seconds"] = _ttl_seconds(cache, bound.get("timeout", DEFAULT_TIMEOUT))
            if succeeded:
                fields["failed_keys"] = [str(k) for k in (result or [])]
        return fields
```

Add this line to the `CacheWatcher` docstring:

```
    - CACHE-04: get_many/set_many/delete_many — one event with keys and key_count
```

- [ ] **Step 4: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_cache_watcher.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add django_inspector/watchers/cache.py tests/test_cache_watcher.py
git commit -m "feat(cache-watcher): capture get_many/set_many/delete_many

One event per multi-key call with keys (capped at 100), key_count and
hit/miss or failed-key counts. Key iterators are materialised once so the
backend still receives every key.

Covers CACHE-04."
```

---

### Task 4: Template watcher

**Files:**
- Create: `django_inspector/watchers/template.py`
- Modify: `django_inspector/conf.py` (`DEFAULTS["WATCHERS"]`)
- Modify: `django_inspector/apps.py` (`_autodiscover_watchers`)
- Modify: `tests/settings.py` (add `TEMPLATES`)
- Create: `tests/templates/inspector_tests/hello.html`
- Test: `tests/test_template_watcher.py`

**Interfaces:**
- Consumes: from Task 2, `django_inspector.watchers.utils.elapsed_ms`
- Produces:
  - `django_inspector.watchers.template.TemplateWatcher`, with `watcher_name = "template"`
  - Event type `template.rendered`, with metadata keys `render_id`, `parent_id`, `depth`, `relation` (`None` / `"extends"` / `"include"`), `name`, `origin_path`, `loader`, `duration_ms`, `context_key_count`, `context_keys`, and `error` (only on failure)
  - `MAX_CONTEXT_KEYS = 50`

- [ ] **Step 1: Test harness.** In `tests/settings.py`, add at the top:

```python
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
```

and append:

```python
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]
```

Create `tests/templates/inspector_tests/hello.html`:

```html
<p>Hello {{ name }}</p>
```

- [ ] **Step 2: Write the failing tests.** Create `tests/test_template_watcher.py`:

```python
"""Tests for the Template Watcher (TMPL-01..TMPL-05)."""

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.template import Context, Engine
from django.template.base import Template
from django.template.loader import render_to_string
from django.test import TestCase

from django_inspector.conf import inspector_settings
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.template import TemplateWatcher

SOURCES = {
    "base.html": "<main>{% block content %}{% endblock %}</main>",
    "child.html": '{% extends "base.html" %}{% block content %}{% include "part.html" %}{% endblock %}',
    "part.html": "<p>{{ title }}</p>",
    "page.html": '<h1>{{ title }}</h1>{% include "part.html" %}',
    "boom.html": "{{ obj.explode }}",
    "outer_boom.html": '{% include "boom.html" %}',
}


class Exploding:
    @property
    def explode(self):
        raise ValueError("boom")


def renders():
    events = [e for e in _get_buffer() if e["event_type"] == "template.rendered"]
    return sorted(events, key=lambda e: e["metadata"]["render_id"])


def tree():
    return [
        (m["name"], m["render_id"], m["parent_id"], m["depth"], m["relation"])
        for m in (e["metadata"] for e in renders())
    ]


class TestTemplateWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert TemplateWatcher.watcher_name == "template"

    def test_off_by_default(self):
        assert inspector_settings.watcher_enabled("template") is False

    def test_enable_wraps_and_disable_restores_render(self):
        original = Template._render
        watcher = TemplateWatcher()
        watcher.enable()
        watcher.enable()
        try:
            assert Template._render is not original
            assert Template._render.__wrapped__ is original
        finally:
            watcher.disable()
        watcher.disable()
        assert Template._render is original


class TemplateWatcherTestCase(TestCase):
    def setUp(self):
        self.engine = Engine(loaders=[("django.template.loaders.locmem.Loader", SOURCES)])
        self.watcher = TemplateWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()


class TestTemplateCapture(TemplateWatcherTestCase):
    def test_records_name_source_duration_and_context_keys(self):
        self.engine.get_template("part.html").render(Context({"title": "Hi", "user": "ann"}))
        [event] = renders()
        meta = event["metadata"]
        assert meta["name"] == "part.html"
        assert meta["origin_path"] == "part.html"
        assert meta["loader"] == "django.template.loaders.locmem.Loader"
        assert meta["duration_ms"] >= 0
        assert meta["context_keys"] == ["title", "user"]
        assert meta["context_key_count"] == 2
        assert "ann" not in str(meta)

    def test_context_keys_are_capped(self):
        context = {"k%03d" % i: i for i in range(80)}
        self.engine.from_string("x").render(Context(context))
        meta = renders()[0]["metadata"]
        assert meta["context_key_count"] == 80
        assert len(meta["context_keys"]) == 50

    def test_from_string_template_has_no_name(self):
        self.engine.from_string("hello").render(Context({}))
        meta = renders()[0]["metadata"]
        assert meta["name"] is None
        assert meta["origin_path"] == "<unknown source>"
        assert meta["loader"] is None
        assert (meta["depth"], meta["parent_id"], meta["relation"]) == (0, None, None)

    def test_filesystem_template_records_source_path(self):
        render_to_string("inspector_tests/hello.html", {"name": "Ann"})
        meta = renders()[0]["metadata"]
        assert meta["name"] == "inspector_tests/hello.html"
        assert meta["origin_path"].endswith("tests/templates/inspector_tests/hello.html")
        assert meta["loader"] == "django.template.loaders.filesystem.Loader"


class TestRenderTree(TemplateWatcherTestCase):
    def test_extends_and_include_tree(self):
        self.engine.get_template("child.html").render(Context({"title": "Hi"}))
        # part.html is written inside child's block, but it renders while base.html
        # is rendering, so its runtime parent is base.html.
        assert tree() == [
            ("child.html", 1, None, 0, None),
            ("base.html", 2, 1, 1, "extends"),
            ("part.html", 3, 2, 2, "include"),
        ]

    def test_include_is_nested_under_includer(self):
        self.engine.get_template("page.html").render(Context({"title": "Hi"}))
        assert tree() == [
            ("page.html", 1, None, 0, None),
            ("part.html", 2, 1, 1, "include"),
        ]

    def test_render_ids_restart_for_a_new_trace(self):
        self.engine.get_template("part.html").render(Context({}))
        self.engine.get_template("part.html").render(Context({}))
        assert [e["metadata"]["render_id"] for e in renders()] == [1, 2]
        assert [e["metadata"]["depth"] for e in renders()] == [0, 0]

        clear_trace_id(self.token)
        self.token = set_trace_id(generate_trace_id())
        clear_buffer()
        self.engine.get_template("part.html").render(Context({}))
        assert [e["metadata"]["render_id"] for e in renders()] == [1]


class TestTemplateErrorsAndContext(TemplateWatcherTestCase):
    def test_render_error_is_recorded_and_reraised(self):
        with pytest.raises(ValueError, match="boom"):
            self.engine.get_template("outer_boom.html").render(Context({"obj": Exploding()}))
        events = renders()
        assert [e["metadata"]["name"] for e in events] == ["outer_boom.html", "boom.html"]
        assert all(e["metadata"]["error"] == "ValueError: boom" for e in events)

    def test_render_after_an_error_is_top_level(self):
        with pytest.raises(ValueError):
            self.engine.get_template("outer_boom.html").render(Context({"obj": Exploding()}))
        clear_buffer()
        self.engine.get_template("part.html").render(Context({}))
        meta = renders()[0]["metadata"]
        assert (meta["depth"], meta["parent_id"]) == (0, None)

    def test_events_carry_trace_id(self):
        self.engine.get_template("part.html").render(Context({}))
        assert renders()[0]["trace_id"] == self.trace_id

    def test_no_events_without_active_trace(self):
        clear_trace_id(self.token)
        self.token = None
        self.engine.get_template("page.html").render(Context({}))
        assert renders() == []

    def test_render_through_sync_to_async_is_recorded(self):
        template = self.engine.get_template("part.html")

        async def run():
            return await sync_to_async(template.render)(Context({"title": "Hi"}))

        assert async_to_sync(run)() == "<p>Hi</p>"
        [event] = renders()
        assert event["trace_id"] == self.trace_id
```

- [ ] **Step 3: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_template_watcher.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'django_inspector.watchers.template'`.

- [ ] **Step 4: Implement.** Create `django_inspector/watchers/template.py`:

```python
"""
Template Watcher — captures each Django template render with name, source
path, loader, render time, context size and its place in the render tree.

Wraps django.template.base.Template._render, which runs for top-level renders,
{% include %}, inclusion tags and {% extends %} parents alike. Django template
language only; Jinja2 is not covered (TMPL-05).

Requirements: TMPL-01..TMPL-05
"""

import functools
import logging
import time
from contextvars import ContextVar

from django.template.base import Template
from django.template.loader_tags import ExtendsNode

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms

logger = logging.getLogger("django_inspector")

MAX_CONTEXT_KEYS = 50
_BUILTIN_CONTEXT_KEYS = frozenset({"True", "False", "None"})

# Render-tree state for the trace rendering in this context:
# {"trace_id": str, "next_id": int, "stack": [(render_id, template), ...]}.
# Replaced with a fresh state whenever the trace id changes.
_render_state = ContextVar("inspector_template_render_state", default=None)


class TemplateWatcher(BaseWatcher):
    """
    Captures template renders by wrapping Template._render.

    - TMPL-01: name, source path, render duration
    - TMPL-02: parent/child relationship (render_id, parent_id, depth, relation)
    - TMPL-03: context size (key count + key names, never values)
    - TMPL-04: trace correlation via BaseWatcher.record()
    - TMPL-05: Django template language only
    """

    watcher_name = "template"

    def __init__(self):
        super().__init__()
        self._original_render = None

    def install_hooks(self):
        current = Template._render
        if getattr(current, "_inspector_wrapped", False):
            return
        self._original_render = current
        Template._render = _make_render_wrapper(self, current)

    def remove_hooks(self):
        if self._original_render is not None:
            Template._render = self._original_render
            self._original_render = None

    def record_render(self, template, context, render_id, parent, depth, duration_ms, error):
        """Build and record one template.rendered event. Never raises unless INSPECTOR_RAISE_ERRORS."""
        try:
            parent_id, parent_template = parent if parent is not None else (None, None)
            origin = getattr(template, "origin", None)
            loader = getattr(origin, "loader", None)
            keys = _context_keys(context)
            metadata = {
                "render_id": render_id,
                "parent_id": parent_id,
                "depth": depth,
                "relation": _relation(parent_template),
                "name": template.name,
                "origin_path": getattr(origin, "name", None),
                "loader": (
                    type(loader).__module__ + "." + type(loader).__qualname__
                    if loader is not None
                    else None
                ),
                "duration_ms": duration_ms,
                "context_key_count": len(keys),
                "context_keys": keys[:MAX_CONTEXT_KEYS],
            }
            if error is not None:
                metadata["error"] = "%s: %s" % (type(error).__name__, error)
            self.record("template.rendered", metadata)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording template.rendered event", exc_info=True)


def _make_render_wrapper(watcher, original):
    @functools.wraps(original)
    def wrapper(template, context):
        trace_id = get_current_trace_id()
        if trace_id is None:
            return original(template, context)

        try:
            state = _state_for(trace_id)
            render_id = state["next_id"]
            state["next_id"] += 1
            parent = state["stack"][-1] if state["stack"] else None
            depth = len(state["stack"])
            state["stack"].append((render_id, template))
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error preparing template.rendered event", exc_info=True)
            return original(template, context)

        start = time.monotonic()
        try:
            output = original(template, context)
        except Exception as exc:
            state["stack"].pop()
            watcher.record_render(template, context, render_id, parent, depth, elapsed_ms(start), exc)
            raise
        except BaseException:
            state["stack"].pop()
            raise
        state["stack"].pop()
        watcher.record_render(template, context, render_id, parent, depth, elapsed_ms(start), None)
        return output

    wrapper._inspector_wrapped = True
    return wrapper


def _state_for(trace_id):
    state = _render_state.get()
    if state is None or state["trace_id"] != trace_id:
        state = {"trace_id": trace_id, "next_id": 1, "stack": []}
        _render_state.set(state)
    return state


def _relation(parent_template):
    """
    None at top level. "extends" when the parent starts with {% extends %}:
    an extending template renders nothing directly except its base.
    Otherwise "include" ({% include %} or an inclusion tag).
    """
    if parent_template is None:
        return None
    nodelist = getattr(parent_template, "nodelist", None) or []
    if any(isinstance(node, ExtendsNode) for node in nodelist):
        return "extends"
    return "include"


def _context_keys(context):
    """Sorted variable names visible to the template, minus the True/False/None builtins."""
    try:
        flat = context.flatten()
    except Exception:
        return []
    return sorted(str(k) for k in flat if k not in _BUILTIN_CONTEXT_KEYS)


register("template", TemplateWatcher)
```

In `django_inspector/conf.py`, add to `DEFAULTS["WATCHERS"]`:

```python
        "template": False,  # opt in with {"WATCHERS": {"template": True}}
```

In `django_inspector/apps.py` `_autodiscover_watchers`, after the `cache` import block, add:

```python
        try:
            import django_inspector.watchers.template  # noqa: F401
        except ImportError:
            pass
```

- [ ] **Step 5: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_template_watcher.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the full suite** (the new `TEMPLATES` setting must not break anything)

Run: `uv run --extra dev python -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add django_inspector/watchers/template.py django_inspector/conf.py django_inspector/apps.py tests/settings.py tests/templates tests/test_template_watcher.py
git commit -m "feat(template-watcher): capture template renders with render tree

Wraps Template._render; each render records name, origin path, loader,
inclusive duration, context key names/count, and render_id/parent_id/
depth/relation from a per-trace ContextVar stack. Off by default.

Covers TMPL-01..TMPL-05."
```

---

### Task 5: Dashboard — Cache pages

**Files:**
- Modify: `tests/settings.py` (add `ROOT_URLCONF`)
- Create: `tests/urls.py`
- Modify: `django_inspector/dashboard/views.py`, `django_inspector/dashboard/urls.py`
- Create: `django_inspector/templates/inspector/cache/list.html`, `_table.html`, `detail.html`
- Modify: `django_inspector/templates/inspector/_sidebar.html`, `django_inspector/templates/inspector/base.html`
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `CACHE_EVENT_TYPES` from Task 3
- Produces:
  - URL names `inspector:cache-list` and `inspector:cache-detail` (takes `pk`)
  - Views `cache_list(request)` and `cache_detail(request, pk)`
  - CSS classes `inspector-indicator--hit` and `inspector-indicator--miss`
  - Test helpers `DashboardTestCase.get(...)`, `cache_event(...)` and `TRACE`, which Tasks 6 and 7 reuse

- [ ] **Step 1: Test harness.** Append to `tests/settings.py`:

```python
ROOT_URLCONF = "tests.urls"
```

Create `tests/urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    path("inspector/", include("django_inspector.dashboard.urls")),
]
```

- [ ] **Step 2: Write the failing tests.** Create `tests/test_dashboard_pages.py`:

```python
"""Tests for the dashboard's cache and template pages and the request detail page."""

import pytest
from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from django_inspector.storage.models import Event

TRACE = "a" * 32


def cache_event(operation, **metadata):
    data = {
        "operation": operation,
        "alias": "default",
        "backend": "django.core.cache.backends.locmem.LocMemCache",
        "duration_ms": 0.1,
        "key": "k",
    }
    data.update(metadata)
    return Event.objects.create(trace_id=TRACE, event_type="cache." + operation, metadata=data)


class DashboardTestCase(TestCase):
    def get(self, url_name, *args, params=None, htmx=False):
        """Call a dashboard view through its URL (so inspector_required applies) as a staff user."""
        path = reverse("inspector:" + url_name, args=args)
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        request = RequestFactory().get(path, params or {}, **headers)
        request.user = User(username="staff", is_staff=True, is_active=True)
        match = resolve(path)
        return match.func(request, *match.args, **match.kwargs)

    def body(self, *args, **kwargs):
        response = self.get(*args, **kwargs)
        assert response.status_code == 200
        return response.content.decode()


class TestCachePages(DashboardTestCase):
    def setUp(self):
        self.hit = cache_event("get", key="profile:alpha", hit=True)
        self.miss = cache_event("get", key="profile:beta", hit=False, alias="other")
        self.write = cache_event(
            "set", key="settings-blob", ttl_seconds=None, value_type="str", value_size_bytes=12
        )
        self.bulk = cache_event(
            "get_many", key=None, keys=["k-one", "k-two", "k-three"],
            key_count=3, hit_count=2, miss_count=1,
        )
        self.sql = Event.objects.create(
            trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"}
        )

    def test_list_shows_only_cache_events(self):
        body = self.body("cache-list")
        assert "profile:alpha" in body
        assert "settings-blob" in body
        assert "3 keys" in body
        assert "SELECT 1" not in body

    def test_filter_by_operation(self):
        body = self.body("cache-list", params={"operation": "set"})
        assert "settings-blob" in body
        assert "profile:alpha" not in body

    def test_filter_by_result(self):
        body = self.body("cache-list", params={"result": "miss"})
        assert "profile:beta" in body
        assert "profile:alpha" not in body

    def test_filter_by_alias(self):
        body = self.body("cache-list", params={"alias": "other"})
        assert "profile:beta" in body
        assert "profile:alpha" not in body

    def test_filter_by_key_is_case_insensitive(self):
        body = self.body("cache-list", params={"key": "PROFILE"})
        assert "profile:alpha" in body
        assert "settings-blob" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("cache-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_shows_metadata_and_request_link(self):
        request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "GET", "path": "/menu/", "status_code": 200},
        )
        body = self.body("cache-detail", self.write.pk)
        assert "settings-blob" in body
        assert "Never expires" in body
        assert "12 bytes" in body
        assert reverse("inspector:request-detail", args=[request_event.pk]) in body

    def test_detail_lists_keys_of_multi_key_operation(self):
        body = self.body("cache-detail", self.bulk.pk)
        assert "Keys (3)" in body
        assert "k-three" in body
        assert "2 / 3 hit" in body

    def test_detail_404s_for_non_cache_event(self):
        with pytest.raises(Http404):
            self.get("cache-detail", self.sql.pk)

    def test_sidebar_links_to_cache(self):
        assert reverse("inspector:cache-list") in self.body("live-feed")
```

- [ ] **Step 3: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_dashboard_pages.py -q`
Expected: FAIL with `NoReverseMatch: Reverse for 'cache-list' not found`.

- [ ] **Step 4: Implement the views.** In `django_inspector/dashboard/views.py`, add to the imports:

```python
from django.conf import settings

from django_inspector.watchers.cache import CACHE_EVENT_TYPES
```

Below the imports add:

```python
CACHE_OPERATIONS = [event_type.split(".", 1)[1] for event_type in CACHE_EVENT_TYPES]
```

Append:

```python
def cache_list(request):
    qs = Event.objects.filter(event_type__in=CACHE_EVENT_TYPES).order_by("-timestamp")

    operation = request.GET.get("operation", "")
    alias = request.GET.get("alias", "")
    result = request.GET.get("result", "")
    key = request.GET.get("key", "")

    if operation:
        qs = qs.filter(metadata__operation=operation)
    if alias:
        qs = qs.filter(metadata__alias=alias)
    if result == "hit":
        qs = qs.filter(metadata__hit=True)
    elif result == "miss":
        qs = qs.filter(metadata__hit=False)
    if key:
        qs = qs.filter(metadata__key__icontains=key)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {
        "page": page,
        "filters": request.GET,
        "operations": CACHE_OPERATIONS,
        "aliases": list(settings.CACHES),
    }

    if request.headers.get("HX-Request"):
        return render(request, "inspector/cache/_table.html", context)
    return render(request, "inspector/cache/list.html", context)


def cache_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type__in=CACHE_EVENT_TYPES)
    parent_request = Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()
    context = {"event": event, "parent_request": parent_request}
    return render(request, "inspector/cache/detail.html", context)
```

In `django_inspector/dashboard/urls.py`, add after the `exception-detail` route:

```python
    path("cache/", inspector_required(views.cache_list), name="cache-list"),
    path("cache/<int:pk>/", inspector_required(views.cache_detail), name="cache-detail"),
```

- [ ] **Step 5: Create the page templates.** Create `django_inspector/templates/inspector/cache/list.html`:

```html
{% extends "inspector/base.html" %}
{% block title %}Cache — Inspector{% endblock %}
{% block content %}
<div class="inspector-page-header">
    <h1>Cache</h1>
</div>

<form class="inspector-filter-bar"
      hx-get="{% url 'inspector:cache-list' %}"
      hx-target="#results-table"
      hx-push-url="true"
      hx-trigger="change, keyup changed delay:300ms from:input[name=key]">
    <label>
        Operation
        <select name="operation">
            <option value="">All</option>
            {% for op in operations %}
            <option value="{{ op }}" {% if filters.operation == op %}selected{% endif %}>{{ op }}</option>
            {% endfor %}
        </select>
    </label>
    <label>
        Cache
        <select name="alias">
            <option value="">All</option>
            {% for a in aliases %}
            <option value="{{ a }}" {% if filters.alias == a %}selected{% endif %}>{{ a }}</option>
            {% endfor %}
        </select>
    </label>
    <label>
        Result
        <select name="result">
            <option value="">All</option>
            <option value="hit" {% if filters.result == "hit" %}selected{% endif %}>Hit</option>
            <option value="miss" {% if filters.result == "miss" %}selected{% endif %}>Miss</option>
        </select>
    </label>
    <label>
        Key
        <input type="search" name="key" value="{{ filters.key|default:'' }}" placeholder="contains…">
    </label>
</form>

<div id="results-table">
    {% include "inspector/cache/_table.html" %}
</div>

{% include "inspector/_pagination.html" %}
{% endblock %}
```

Create `django_inspector/templates/inspector/cache/_table.html`. Note: use `key_count`, never `metadata.keys`, to tell multi-key events apart. On a single-key event, `metadata.keys` resolves to the dict's `keys()` method and is always truthy.

```html
<table class="inspector-table">
    <thead>
        <tr>
            <th>Operation</th>
            <th>Key</th>
            <th>Result</th>
            <th>Cache</th>
            <th>Duration</th>
            <th>Time</th>
        </tr>
    </thead>
    <tbody>
        {% for event in page.object_list %}
        <tr>
            <td><a href="{% url 'inspector:cache-detail' event.pk %}">{{ event.metadata.operation }}</a></td>
            <td class="inspector-code-inline">{% if event.metadata.key_count %}{{ event.metadata.key_count }} keys{% else %}{{ event.metadata.key|truncatechars:80 }}{% endif %}</td>
            <td>
                {% if event.metadata.hit is True %}<span class="inspector-indicator inspector-indicator--hit">HIT</span>
                {% elif event.metadata.hit is False %}<span class="inspector-indicator inspector-indicator--miss">MISS</span>
                {% elif event.metadata.hit_count is not None %}{{ event.metadata.hit_count }} / {{ event.metadata.key_count }} hit
                {% endif %}
                {% if event.metadata.error %}<span class="inspector-indicator inspector-indicator--slow">ERROR</span>{% endif %}
            </td>
            <td>{{ event.metadata.alias|default:"-" }}</td>
            <td>{{ event.metadata.duration_ms }}ms</td>
            <td>{{ event.timestamp|timesince }} ago</td>
        </tr>
        {% empty %}
        <tr>
            <td colspan="6" style="text-align: center; padding: 40px; color: var(--ins-text-secondary);">No cache operations found.</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

Create `django_inspector/templates/inspector/cache/detail.html`:

```html
{% extends "inspector/base.html" %}
{% block title %}Cache Operation — Inspector{% endblock %}
{% block content %}
<a href="{% url 'inspector:cache-list' %}" class="inspector-back-link">&#8592; Back to Cache</a>

<div class="inspector-page-header">
    <h1>cache.{{ event.metadata.operation }}</h1>
</div>

{% if event.metadata.error %}
<div class="inspector-card">
    <h3 style="margin-bottom: 12px; font-size: 14px; color: var(--ins-text-secondary); text-transform: uppercase;">Error</h3>
    <pre class="inspector-code">{{ event.metadata.error }}</pre>
</div>
{% endif %}

<div class="inspector-card">
    <table class="inspector-detail-table">
        {% if event.metadata.key_count %}
        <tr>
            <th>Keys ({{ event.metadata.key_count }})</th>
            <td>
                <pre class="inspector-code" style="margin: 0;">{% for k in event.metadata.keys %}{{ k }}
{% endfor %}</pre>
                {% if event.metadata.key_count > event.metadata.keys|length %}
                <div style="font-size: 12px; color: var(--ins-text-secondary);">Showing the first {{ event.metadata.keys|length }}.</div>
                {% endif %}
            </td>
        </tr>
        {% else %}
        <tr><th>Key</th><td class="inspector-code-inline">{{ event.metadata.key }}</td></tr>
        {% endif %}
        <tr>
            <th>Result</th>
            <td>
                {% if event.metadata.hit is True %}<span class="inspector-indicator inspector-indicator--hit">HIT</span>
                {% elif event.metadata.hit is False %}<span class="inspector-indicator inspector-indicator--miss">MISS</span>
                {% elif event.metadata.hit_count is not None %}{{ event.metadata.hit_count }} / {{ event.metadata.key_count }} hit
                {% else %}-{% endif %}
            </td>
        </tr>
        {% if "ttl_seconds" in event.metadata %}
        <tr>
            <th>TTL</th>
            <td>{% if event.metadata.ttl_seconds is None %}Never expires{% else %}{{ event.metadata.ttl_seconds }}s{% endif %}</td>
        </tr>
        {% endif %}
        {% if "value_type" in event.metadata %}
        <tr>
            <th>Value</th>
            <td>{{ event.metadata.value_type }}{% if event.metadata.value_size_bytes is not None %}, {{ event.metadata.value_size_bytes }} bytes{% endif %}</td>
        </tr>
        {% endif %}
        {% if "stored" in event.metadata %}
        <tr><th>Stored</th><td>{{ event.metadata.stored|yesno:"yes,no" }}</td></tr>
        {% endif %}
        {% if "deleted" in event.metadata %}
        <tr><th>Deleted</th><td>{{ event.metadata.deleted|yesno:"yes,no" }}</td></tr>
        {% endif %}
        {% if event.metadata.failed_keys %}
        <tr><th>Failed keys</th><td>{{ event.metadata.failed_keys|join:", " }}</td></tr>
        {% endif %}
        <tr><th>Cache</th><td>{{ event.metadata.alias|default:"-" }}</td></tr>
        <tr><th>Backend</th><td class="inspector-code-inline">{{ event.metadata.backend }}</td></tr>
        <tr><th>Duration</th><td>{{ event.metadata.duration_ms }}ms</td></tr>
        <tr><th>Origin File</th><td>{{ event.metadata.origin_file|default:"-" }}</td></tr>
        <tr><th>Origin Line</th><td>{{ event.metadata.origin_line|default:"-" }}</td></tr>
        <tr><th>Origin Function</th><td>{{ event.metadata.origin_function|default:"-" }}</td></tr>
    </table>
</div>

{% if parent_request %}
<div class="inspector-card">
    <a href="{% url 'inspector:request-detail' parent_request.pk %}" style="color: var(--ins-accent); text-decoration: none; font-weight: 500;">
        View Request &#8594; {{ parent_request.metadata.method }} {{ parent_request.metadata.path }}
    </a>
</div>
{% endif %}

{% endblock %}
```

- [ ] **Step 6: Sidebar and CSS.** In `_sidebar.html`, insert after the Queries link:

```html
        <a href="{% url 'inspector:cache-list' %}"
           class="inspector-sidebar-link {% if request.resolver_match.url_name == 'cache-list' or request.resolver_match.url_name == 'cache-detail' %}inspector-sidebar-link--active{% endif %}">
            Cache
        </a>
```

In `base.html`, after the `.inspector-indicator--duplicate { … }` rule, add:

```css
        .inspector-indicator--hit {
            background: rgba(46, 204, 113, 0.12);
            color: #27ae60;
        }

        .inspector-indicator--miss {
            background: rgba(243, 156, 18, 0.12);
            color: #d68910;
        }
```

- [ ] **Step 7: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_dashboard_pages.py -q`
Expected: all PASS.

- [ ] **Step 8: Run the full suite**

Run: `uv run --extra dev python -m pytest -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add tests/settings.py tests/urls.py tests/test_dashboard_pages.py django_inspector/dashboard django_inspector/templates/inspector/cache django_inspector/templates/inspector/_sidebar.html django_inspector/templates/inspector/base.html
git commit -m "feat(dashboard): add Cache list and detail pages

Filters by operation, alias, hit/miss and key; HTMX partials; sidebar link.
Adds a test URLconf and TEMPLATES so dashboard pages are rendered in tests."
```

---

### Task 6: Dashboard — Template pages

**Files:**
- Modify: `django_inspector/dashboard/views.py`, `django_inspector/dashboard/urls.py`
- Create: `django_inspector/templates/inspector/template_renders/list.html`, `_table.html`, `detail.html`
- Modify: `django_inspector/templates/inspector/_sidebar.html`
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `DashboardTestCase` and `TRACE` from Task 5, plus the `template.rendered` metadata keys from Task 4
- Produces:
  - URL names `inspector:templates-list` and `inspector:template-detail` (takes `pk`)
  - Test helper `template_event(...)`, which Task 7 reuses

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_dashboard_pages.py`:

```python
def template_event(render_id, name, parent_id=None, depth=0, relation=None, trace_id=TRACE, **extra):
    data = {
        "render_id": render_id,
        "parent_id": parent_id,
        "depth": depth,
        "relation": relation,
        "name": name,
        "origin_path": "/app/templates/%s" % name,
        "loader": "django.template.loaders.filesystem.Loader",
        "duration_ms": 1.5,
        "context_key_count": 2,
        "context_keys": ["title", "user"],
    }
    data.update(extra)
    return Event.objects.create(trace_id=trace_id, event_type="template.rendered", metadata=data)


class TestTemplatePages(DashboardTestCase):
    def setUp(self):
        self.page = template_event(1, "shop/page.html")
        self.base = template_event(2, "shop/base.html", parent_id=1, depth=1, relation="extends")
        self.card = template_event(3, "shop/card.html", parent_id=2, depth=2, relation="include")
        # Same render ids in another trace: must never be linked to this trace's tree.
        self.foreign = template_event(
            3, "elsewhere/card.html", parent_id=2, depth=2, relation="include", trace_id="b" * 32
        )

    def test_list_shows_renders(self):
        body = self.body("templates-list")
        assert "shop/page.html" in body
        assert "shop/card.html" in body

    def test_filter_by_name_is_case_insensitive(self):
        body = self.body("templates-list", params={"name": "CARD"})
        assert "shop/card.html" in body
        assert "shop/page.html" not in body

    def test_top_level_only(self):
        body = self.body("templates-list", params={"top_level": "on"})
        assert "shop/page.html" in body
        assert "shop/card.html" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("templates-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_links_parent_and_children_in_same_trace_only(self):
        body = self.body("template-detail", self.base.pk)
        assert reverse("inspector:template-detail", args=[self.page.pk]) in body
        assert reverse("inspector:template-detail", args=[self.card.pk]) in body
        assert reverse("inspector:template-detail", args=[self.foreign.pk]) not in body
        assert "/app/templates/shop/base.html" in body
        assert "title" in body

    def test_detail_of_top_level_render(self):
        body = self.body("template-detail", self.page.pk)
        assert "Top-level render" in body
        assert reverse("inspector:template-detail", args=[self.base.pk]) in body

    def test_detail_shows_error(self):
        failed = template_event(9, "shop/broken.html", error="ValueError: boom")
        assert "ValueError: boom" in self.body("template-detail", failed.pk)

    def test_sidebar_links_to_templates(self):
        assert reverse("inspector:templates-list") in self.body("live-feed")
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_dashboard_pages.py -q -k TemplatePages`
Expected: FAIL with `NoReverseMatch: Reverse for 'templates-list' not found`.

- [ ] **Step 3: Implement the views.** Append to `django_inspector/dashboard/views.py`:

```python
def templates_list(request):
    qs = Event.objects.filter(event_type="template.rendered").order_by("-timestamp")

    name = request.GET.get("name", "")
    if name:
        qs = qs.filter(metadata__name__icontains=name)
    if request.GET.get("top_level") == "on":
        qs = qs.filter(metadata__depth=0)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {"page": page, "filters": request.GET}

    if request.headers.get("HX-Request"):
        return render(request, "inspector/template_renders/_table.html", context)
    return render(request, "inspector/template_renders/list.html", context)


def template_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="template.rendered")
    same_trace = Event.objects.filter(trace_id=event.trace_id, event_type="template.rendered")
    render_id = event.metadata.get("render_id")
    parent_id = event.metadata.get("parent_id")

    parent_render = None
    if parent_id is not None:
        parent_render = same_trace.filter(metadata__render_id=parent_id).first()
    child_renders = []
    if render_id is not None:
        child_renders = sorted(
            same_trace.filter(metadata__parent_id=render_id),
            key=lambda e: e.metadata.get("render_id") or 0,
        )
    parent_request = Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()

    context = {
        "event": event,
        "parent_render": parent_render,
        "child_renders": child_renders,
        "parent_request": parent_request,
    }
    return render(request, "inspector/template_renders/detail.html", context)
```

In `django_inspector/dashboard/urls.py`, add after the cache routes:

```python
    path("templates/", inspector_required(views.templates_list), name="templates-list"),
    path("templates/<int:pk>/", inspector_required(views.template_detail), name="template-detail"),
```

- [ ] **Step 4: Create the page templates.** Create `django_inspector/templates/inspector/template_renders/list.html`:

```html
{% extends "inspector/base.html" %}
{% block title %}Templates — Inspector{% endblock %}
{% block content %}
<div class="inspector-page-header">
    <h1>Templates</h1>
</div>

<form class="inspector-filter-bar"
      hx-get="{% url 'inspector:templates-list' %}"
      hx-target="#results-table"
      hx-push-url="true"
      hx-trigger="change, keyup changed delay:300ms from:input[name=name]">
    <label>
        Name
        <input type="search" name="name" value="{{ filters.name|default:'' }}" placeholder="contains…">
    </label>
    <label>
        <input type="checkbox" name="top_level" {% if filters.top_level == "on" %}checked{% endif %}> Top-level only
    </label>
</form>

<div id="results-table">
    {% include "inspector/template_renders/_table.html" %}
</div>

{% include "inspector/_pagination.html" %}
{% endblock %}
```

Create `django_inspector/templates/inspector/template_renders/_table.html`:

```html
<table class="inspector-table">
    <thead>
        <tr>
            <th>Template</th>
            <th>Relation</th>
            <th>Depth</th>
            <th>Duration</th>
            <th>Context keys</th>
            <th>Time</th>
        </tr>
    </thead>
    <tbody>
        {% for event in page.object_list %}
        <tr>
            <td>
                <a href="{% url 'inspector:template-detail' event.pk %}" class="inspector-code-inline">{{ event.metadata.name|default:"(from string)" }}</a>
                {% if event.metadata.error %}<span class="inspector-indicator inspector-indicator--slow">ERROR</span>{% endif %}
            </td>
            <td>{{ event.metadata.relation|default:"top-level" }}</td>
            <td>{{ event.metadata.depth }}</td>
            <td>{{ event.metadata.duration_ms }}ms</td>
            <td>{{ event.metadata.context_key_count }}</td>
            <td>{{ event.timestamp|timesince }} ago</td>
        </tr>
        {% empty %}
        <tr>
            <td colspan="6" style="text-align: center; padding: 40px; color: var(--ins-text-secondary);">No template renders found.</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

Create `django_inspector/templates/inspector/template_renders/detail.html`:

```html
{% extends "inspector/base.html" %}
{% block title %}Template Render — Inspector{% endblock %}
{% block content %}
<a href="{% url 'inspector:templates-list' %}" class="inspector-back-link">&#8592; Back to Templates</a>

<div class="inspector-page-header">
    <h1>{{ event.metadata.name|default:"(from string)" }}</h1>
</div>

{% if event.metadata.error %}
<div class="inspector-card">
    <h3 style="margin-bottom: 12px; font-size: 14px; color: var(--ins-text-secondary); text-transform: uppercase;">Error</h3>
    <pre class="inspector-code">{{ event.metadata.error }}</pre>
</div>
{% endif %}

<div class="inspector-card">
    <table class="inspector-detail-table">
        <tr><th>Source</th><td class="inspector-code-inline">{{ event.metadata.origin_path|default:"-" }}</td></tr>
        <tr><th>Loader</th><td class="inspector-code-inline">{{ event.metadata.loader|default:"-" }}</td></tr>
        <tr><th>Duration</th><td>{{ event.metadata.duration_ms }}ms (includes nested renders)</td></tr>
        <tr><th>Relation</th><td>{{ event.metadata.relation|default:"top-level" }}</td></tr>
        <tr><th>Depth</th><td>{{ event.metadata.depth }}</td></tr>
        <tr>
            <th>Context keys ({{ event.metadata.context_key_count }})</th>
            <td>
                {{ event.metadata.context_keys|join:", "|default:"-" }}
                {% if event.metadata.context_key_count > event.metadata.context_keys|length %}
                <div style="font-size: 12px; color: var(--ins-text-secondary);">Showing the first {{ event.metadata.context_keys|length }}.</div>
                {% endif %}
            </td>
        </tr>
    </table>
</div>

<div class="inspector-card">
    <h3 style="margin-bottom: 12px; font-size: 14px; color: var(--ins-text-secondary); text-transform: uppercase;">Render tree</h3>
    <table class="inspector-detail-table">
        <tr>
            <th>Rendered inside</th>
            <td>
                {% if parent_render %}
                <a href="{% url 'inspector:template-detail' parent_render.pk %}" class="inspector-code-inline">{{ parent_render.metadata.name|default:"(from string)" }}</a>
                {% else %}Top-level render{% endif %}
            </td>
        </tr>
        <tr>
            <th>Renders inside it</th>
            <td>
                {% for child in child_renders %}
                <div><a href="{% url 'inspector:template-detail' child.pk %}" class="inspector-code-inline">{{ child.metadata.name|default:"(from string)" }}</a> — {{ child.metadata.relation }}, {{ child.metadata.duration_ms }}ms</div>
                {% empty %}-{% endfor %}
            </td>
        </tr>
    </table>
</div>

{% if parent_request %}
<div class="inspector-card">
    <a href="{% url 'inspector:request-detail' parent_request.pk %}" style="color: var(--ins-accent); text-decoration: none; font-weight: 500;">
        View Request &#8594; {{ parent_request.metadata.method }} {{ parent_request.metadata.path }}
    </a>
</div>
{% endif %}

{% endblock %}
```

- [ ] **Step 5: Sidebar.** In `_sidebar.html`, insert after the Cache link:

```html
        <a href="{% url 'inspector:templates-list' %}"
           class="inspector-sidebar-link {% if request.resolver_match.url_name == 'templates-list' or request.resolver_match.url_name == 'template-detail' %}inspector-sidebar-link--active{% endif %}">
            Templates
        </a>
```

- [ ] **Step 6: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_dashboard_pages.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_dashboard_pages.py django_inspector/dashboard django_inspector/templates/inspector/template_renders django_inspector/templates/inspector/_sidebar.html
git commit -m "feat(dashboard): add Templates list and detail pages

Filters by name and top-level; detail links the parent render and child
renders within the same trace; sidebar link."
```

---

### Task 7: Dashboard — request detail shows cache and template events

**Files:**
- Modify: `django_inspector/dashboard/views.py` (`request_detail`)
- Modify: `django_inspector/templates/inspector/requests/detail.html`
- Modify: `django_inspector/templates/inspector/base.html` (timeline and waterfall colours)
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes:
  - `CACHE_EVENT_TYPES`
  - the `cache-detail` and `template-detail` URL names
  - the test helpers `cache_event` and `template_event`
- Produces:
  - `request_detail` context gains `cache_events`, `template_renders` and `cache_event_types`
  - `summary` gains `cache_ops`, `cache_hits`, `cache_misses` and `template_renders`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_dashboard_pages.py`:

```python
class TestRequestDetailIntegration(DashboardTestCase):
    def setUp(self):
        self.request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "GET", "path": "/shop/", "status_code": 200, "latency_ms": 12.0},
        )
        cache_event("get", key="profile:alpha", hit=True)
        cache_event("get", key="profile:beta", hit=False)
        cache_event("get_many", key=None, keys=["k-one", "k-two", "k-three"],
                    key_count=3, hit_count=2, miss_count=1)
        template_event(1, "shop/page.html")
        template_event(2, "shop/card.html", parent_id=1, depth=1, relation="include")

    def test_summary_counts_cache_and_templates(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "Cache Ops" in body
        assert ">3 / 2<" in body  # hits 1 + 2 from get_many, misses 1 + 1
        assert "Template Renders" in body

    def test_timeline_labels_cache_and_template_events(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "inspector-timeline-item--cache" in body
        assert "inspector-timeline-item--template" in body
        assert "profile:alpha" in body
        assert "shop/card.html" in body

    def test_tabs_view_has_cache_and_templates_tabs(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "tabs"})
        assert "Cache (3)" in body
        assert "Templates (2)" in body

    def test_waterfall_colours_cache_and_template_bars(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "waterfall"})
        assert "inspector-waterfall-bar--cache" in body
        assert "inspector-waterfall-bar--template" in body
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `uv run --extra dev python -m pytest tests/test_dashboard_pages.py -q -k RequestDetailIntegration`
Expected: FAIL. `Cache Ops` and the new CSS classes aren't rendered yet.

- [ ] **Step 3: Update the view.** In `django_inspector/dashboard/views.py` `request_detail`:

After the line `exceptions = [e for e in trace_events if e.event_type == "exception.raised"]`, add:

```python
    cache_events = [e for e in trace_events if e.event_type in CACHE_EVENT_TYPES]
    template_renders = sorted(
        (e for e in trace_events if e.event_type == "template.rendered"),
        key=lambda e: e.metadata.get("render_id") or 0,
    )
    cache_hits, cache_misses = _cache_hits_and_misses(cache_events)
```

Add these four entries to the `summary` dict:

```python
        "cache_ops": len(cache_events),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "template_renders": len(template_renders),
```

Add these three entries to the `context` dict:

```python
        "cache_events": cache_events,
        "template_renders": template_renders,
        "cache_event_types": CACHE_EVENT_TYPES,
```

Append to the module:

```python
def _cache_hits_and_misses(cache_events):
    """Total hits and misses across get (hit flag) and get_many (hit/miss counts)."""
    hits = misses = 0
    for e in cache_events:
        meta = e.metadata
        if meta.get("operation") == "get":
            if meta.get("hit") is True:
                hits += 1
            elif meta.get("hit") is False:
                misses += 1
        elif meta.get("operation") == "get_many":
            hits += meta.get("hit_count") or 0
            misses += meta.get("miss_count") or 0
    return hits, misses
```

- [ ] **Step 4: Update `requests/detail.html`.** Make these six edits.

(a) Summary. Replace:

```html
    <div class="inspector-summary-card">
        <div class="inspector-summary-card__value">{{ summary.slow_queries }}</div>
        <div class="inspector-summary-card__label">Slow Queries</div>
    </div>
</div>
```

with:

```html
    <div class="inspector-summary-card">
        <div class="inspector-summary-card__value">{{ summary.slow_queries }}</div>
        <div class="inspector-summary-card__label">Slow Queries</div>
    </div>
    <div class="inspector-summary-card">
        <div class="inspector-summary-card__value">{{ summary.cache_ops }}</div>
        <div class="inspector-summary-card__label">Cache Ops</div>
    </div>
    <div class="inspector-summary-card">
        <div class="inspector-summary-card__value">{{ summary.cache_hits }} / {{ summary.cache_misses }}</div>
        <div class="inspector-summary-card__label">Cache Hits / Misses</div>
    </div>
    <div class="inspector-summary-card">
        <div class="inspector-summary-card__value">{{ summary.template_renders }}</div>
        <div class="inspector-summary-card__label">Template Renders</div>
    </div>
</div>
```

(b) Timeline item class. Replace:

```html
        <div class="inspector-timeline-item {% if e.event_type == 'sql.query' %}inspector-timeline-item--sql{% elif e.event_type == 'exception.raised' %}inspector-timeline-item--exception{% else %}inspector-timeline-item--request{% endif %}">
```

with:

```html
        <div class="inspector-timeline-item {% if e.event_type == 'sql.query' %}inspector-timeline-item--sql{% elif e.event_type == 'exception.raised' %}inspector-timeline-item--exception{% elif e.event_type in cache_event_types %}inspector-timeline-item--cache{% elif e.event_type == 'template.rendered' %}inspector-timeline-item--template{% else %}inspector-timeline-item--request{% endif %}">
```

(c) Timeline label. Replace:

```html
                    {% elif e.event_type == "exception.raised" %}Exception
                    {% else %}{{ e.event_type }}{% endif %}
```

with:

```html
                    {% elif e.event_type == "exception.raised" %}Exception
                    {% elif e.event_type in cache_event_types %}Cache
                    {% elif e.event_type == "template.rendered" %}Template
                    {% else %}{{ e.event_type }}{% endif %}
```

(d) Timeline body. Replace:

```html
                <div style="font-size: 13px;">{{ e.metadata.exception_message|truncatechars:120 }}</div>
            {% endif %}
```

with:

```html
                <div style="font-size: 13px;">{{ e.metadata.exception_message|truncatechars:120 }}</div>
            {% elif e.event_type in cache_event_types %}
                <div><span class="inspector-code-inline">{{ e.metadata.operation }}</span> {% if e.metadata.key_count %}{{ e.metadata.key_count }} keys{% else %}{{ e.metadata.key|truncatechars:80 }}{% endif %}</div>
                <div style="font-size: 12px; color: var(--ins-text-secondary);">
                    {{ e.metadata.duration_ms }}ms · {{ e.metadata.alias|default:"-" }}
                    {% if e.metadata.hit is True %}<span class="inspector-indicator inspector-indicator--hit">HIT</span>{% elif e.metadata.hit is False %}<span class="inspector-indicator inspector-indicator--miss">MISS</span>{% endif %}
                </div>
            {% elif e.event_type == "template.rendered" %}
                <div class="inspector-code-inline">{{ e.metadata.name|default:"(from string)" }}</div>
                <div style="font-size: 12px; color: var(--ins-text-secondary);">
                    {{ e.metadata.duration_ms }}ms{% if e.metadata.relation %} · {{ e.metadata.relation }}{% endif %} · depth {{ e.metadata.depth }}
                </div>
            {% endif %}
```

(e) Tabs. Replace:

```html
        <button class="inspector-tab" onclick="switchTab('exceptions-tab')">Exceptions ({{ exceptions|length }})</button>
```

with:

```html
        <button class="inspector-tab" onclick="switchTab('exceptions-tab')">Exceptions ({{ exceptions|length }})</button>
        <button class="inspector-tab" onclick="switchTab('cache-tab')">Cache ({{ cache_events|length }})</button>
        <button class="inspector-tab" onclick="switchTab('templates-tab')">Templates ({{ template_renders|length }})</button>
```

Then replace the end of the exceptions panel:

```html
                <tr><td colspan="2" style="text-align: center; padding: 20px; color: var(--ins-text-secondary);">No exceptions in this trace.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
```

with:

```html
                <tr><td colspan="2" style="text-align: center; padding: 20px; color: var(--ins-text-secondary);">No exceptions in this trace.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div id="cache-tab" class="inspector-tab-panel">
        <table class="inspector-table">
            <thead>
                <tr>
                    <th>Operation</th>
                    <th>Key</th>
                    <th>Result</th>
                    <th>Cache</th>
                    <th>Duration</th>
                </tr>
            </thead>
            <tbody>
                {% for c in cache_events %}
                <tr>
                    <td><a href="{% url 'inspector:cache-detail' c.pk %}">{{ c.metadata.operation }}</a></td>
                    <td class="inspector-code-inline">{% if c.metadata.key_count %}{{ c.metadata.key_count }} keys{% else %}{{ c.metadata.key|truncatechars:80 }}{% endif %}</td>
                    <td>
                        {% if c.metadata.hit is True %}<span class="inspector-indicator inspector-indicator--hit">HIT</span>
                        {% elif c.metadata.hit is False %}<span class="inspector-indicator inspector-indicator--miss">MISS</span>
                        {% elif c.metadata.hit_count is not None %}{{ c.metadata.hit_count }} / {{ c.metadata.key_count }} hit
                        {% endif %}
                    </td>
                    <td>{{ c.metadata.alias|default:"-" }}</td>
                    <td>{{ c.metadata.duration_ms }}ms</td>
                </tr>
                {% empty %}
                <tr><td colspan="5" style="text-align: center; padding: 20px; color: var(--ins-text-secondary);">No cache operations in this trace.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div id="templates-tab" class="inspector-tab-panel">
        <table class="inspector-table">
            <thead>
                <tr>
                    <th>Template</th>
                    <th>Relation</th>
                    <th>Duration</th>
                    <th>Context keys</th>
                </tr>
            </thead>
            <tbody>
                {% for t in template_renders %}
                <tr>
                    <td style="padding-left: calc(12px + {% widthratio t.metadata.depth 1 16 %}px);"><a href="{% url 'inspector:template-detail' t.pk %}" class="inspector-code-inline">{{ t.metadata.name|default:"(from string)" }}</a></td>
                    <td>{{ t.metadata.relation|default:"top-level" }}</td>
                    <td>{{ t.metadata.duration_ms }}ms</td>
                    <td>{{ t.metadata.context_key_count }}</td>
                </tr>
                {% empty %}
                <tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--ins-text-secondary);">No template renders in this trace.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
```

(f) Waterfall. Replace:

```html
                {% elif wf.event.event_type == "exception.raised" %}Exception
                {% else %}{{ wf.event.event_type }}{% endif %}
```

with:

```html
                {% elif wf.event.event_type == "exception.raised" %}Exception
                {% elif wf.event.event_type in cache_event_types %}Cache
                {% elif wf.event.event_type == "template.rendered" %}Template
                {% else %}{{ wf.event.event_type }}{% endif %}
```

and replace:

```html
{% elif wf.event.event_type == 'exception.raised' %}inspector-waterfall-bar--exception{% endif %}"
```

with:

```html
{% elif wf.event.event_type == 'exception.raised' %}inspector-waterfall-bar--exception{% elif wf.event.event_type in cache_event_types %}inspector-waterfall-bar--cache{% elif wf.event.event_type == 'template.rendered' %}inspector-waterfall-bar--template{% endif %}"
```

- [ ] **Step 5: Add the CSS.** In `base.html`, after `.inspector-timeline-item--request::before { background: #3498db; }`, add:

```css
        .inspector-timeline-item--cache::before { background: #f39c12; }
        .inspector-timeline-item--template::before { background: #9b59b6; }
```

After `.inspector-waterfall-bar--exception { background: #e74c3c; }`, add:

```css
        .inspector-waterfall-bar--cache { background: #f39c12; }
        .inspector-waterfall-bar--template { background: #9b59b6; }
```

- [ ] **Step 6: Run the tests and check they pass**

Run: `uv run --extra dev python -m pytest tests/test_dashboard_pages.py -q`
Expected: all PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run --extra dev python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add django_inspector/dashboard/views.py django_inspector/templates/inspector/requests/detail.html django_inspector/templates/inspector/base.html tests/test_dashboard_pages.py
git commit -m "feat(dashboard): show cache and template events on request detail

Summary cards for cache ops, hits/misses and template renders; labelled,
coloured timeline and waterfall entries; Cache and Templates tabs."
```

---

### Task 8: Update the project docs for Phase 5

**Files:**
- Modify: `docs/roadmap.md`, `docs/requirements.md`, `docs/project.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: the finished Tasks 1–7

- [ ] **Step 1: Check the suite is green and get the total**

Run: `uv run --extra dev python -m pytest -q`
Expected: all PASS. Note the total test count (call it `N`) for the edits below.

- [ ] **Step 2: Update `docs/requirements.md`.**
- Change `- [ ]` to `- [x]` on the CACHE-01..07 and TMPL-01..05 lines.
- Append these notes to the lines they apply to:
  - CACHE-04: `— one event per call with keys (capped at 100) and key_count`
  - CACHE-07: `— off by default ("cache": False)`
  - TMPL-03: `— key count and key names; serialized byte size deferred`
- In the Traceability table, change both Phase 5 rows' status from `Pending` to `Done`.

- [ ] **Step 3: Update `docs/roadmap.md`.**
- Under Phase 5:
  - Replace the `**Spec**: not started …` line with `**Spec**: [2026-09-30-cache-template-watchers-design.md](superpowers/specs/2026-09-30-cache-template-watchers-design.md) · **Plan**: [2026-09-30-cache-template-watchers.md](superpowers/plans/2026-09-30-cache-template-watchers.md)`.
  - Tick both work items.
  - Add a work item: `- [x] Dashboard requests no longer open a trace (orphan-event fix)`.
- In the Progress table, change the Phase 5 row to `| 5. Cache & Template Watchers | v1.1 | 1/1 | Complete | <today> |` and the Phase 6 row status to `Not started (next)`.
- Append these rows to the Deferred Items table:

```markdown
| Schema | Capture-time event timestamps (`auto_now_add` is set at flush, so waterfall offsets are meaningless; needs a migration) | Deferred | Phase 5 |
| Watchers | Jinja2 template renders | Deferred | Phase 5 |
| Watchers | Cache `has_key` / `incr` / `decr` / `touch`; value size for non-str/bytes values | Deferred | Phase 5 |
```

- [ ] **Step 4: Update `docs/project.md`.**
- Move the Cache watcher and Template watcher lines from **Active** to **Validated**, with the suffix `— v1.1 (Phase 5)`.
- In **Context → Current state**, add a Phase 5 bullet listing the branch commits (`git log --oneline master..HEAD`) and `N tests passing`.
- Update the `*Last updated:` footer to today, `— Phase 5 complete`.

- [ ] **Step 5: Update `CLAUDE.md`.**
- Replace the "Next up" line with: `Next up: **Phase 6 — Signal & Logging Watchers** (\`SIGL-01..06\`, \`LOG-01..06\`).`
- In the first paragraph, change `(and soon cache, templates, signals, logs)` to `cache operations and template renders (and soon signals and logs)`.

- [ ] **Step 6: Commit**

```bash
git add docs/roadmap.md docs/requirements.md docs/project.md CLAUDE.md
git commit -m "docs: mark Phase 5 cache & template watchers complete

Ticks CACHE-01..07 and TMPL-01..05, links the spec and plan from the
roadmap, records deferred items, and points CLAUDE.md at Phase 6."
```
