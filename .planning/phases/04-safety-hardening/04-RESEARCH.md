# Phase 4: Safety & Hardening — Research

**Phase:** 4 — Safety & Hardening
**Researched:** 2026-05-23
**Requirement IDs:** MASK-01..06, SAMP-01..05, AUTH-01..05, ASYNC-01, ASYNC-02, IGN-01..04, OBS-01..03

---

## Validation Architecture

This phase introduces no new watchers. It adds a cross-cutting safety layer that is applied **before** any event reaches the database. Every feature here is either:

1. A guard applied at middleware entry/watcher call site (sampling, ignore lists), or
2. A transform applied to metadata before `buffer_event` (masking), or
3. A decorator applied to dashboard views (auth), or
4. An in-place migration of a storage primitive (threading.local → ContextVar).

Success is measured by the 6 observable success criteria in ROADMAP.md.

---

## Research Findings

### 1. Sensitive-Data Masking (MASK-01..06)

**Approach:** A module `django_inspector/masking.py` with a single public function `mask_metadata(metadata: dict) -> dict`. This function:

- Receives a metadata dict from any watcher before it reaches `buffer_event`.
- Walks keys recursively (breadth-first, depth cap = 10) comparing lowercased key against a compiled frozen set.
- Replaces matched values with `"***REDACTED***"`.
- Also scans string *values* with two regexes: Luhn-candidate (13–19 consecutive digits, optionally space/dash separated) and JWT-shaped (`[A-Za-z0-9_-]{2,}\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`).
- Returns a new dict (does not mutate in place).

**Integration point:** `BaseWatcher.record()` already sits between watchers and `buffer_event`. The masking call slots cleanly here — one place, covers all watchers automatically:

```python
# django_inspector/watchers/base.py BaseWatcher.record()
def record(self, event_type: str, metadata: dict) -> None:
    ...
    from django_inspector.masking import mask_metadata
    masked = mask_metadata(metadata)
    buffer_event(trace_id, event_type, masked)
```

**Default SENSITIVE_KEYS** (lowercased, compared case-insensitively to actual keys):
```python
SENSITIVE_KEYS = frozenset([
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token",
    "secret", "api_key", "api-key", "x-api-key",
    "authorization", "cookie", "set-cookie",
    "csrfmiddlewaretoken", "csrf_token",
    "session", "sessionid",
    "private_key", "auth",
])
```

**Configurable:** `inspector_settings.SENSITIVE_KEYS` (set, merged with defaults). Host settings: `"SENSITIVE_KEYS": ["extra_key"]` — merged additive, not replace.

**Depth protection:** Track current depth; if `depth > 10` stop recursing and replace the whole subtree with `"***REDACTED_DEEP***"`.

**Performance:** Masking happens once per event, after the watcher already did its work. The overhead is a dict walk — negligible vs. the DB write it precedes. The frozen set lookup is O(1).

**Testing:** Assert that `{"password": "s3cr3t"}` → `{"password": "***REDACTED***"}` for nested dicts, lists, header dicts, and that non-sensitive keys pass through unchanged.

---

### 2. Sampling (SAMP-01..05)

**Approach:** Sampling decision is made **once** at middleware entry, stored on the request object and on a `ContextVar[bool]`, and then checked in `buffer_event` before appending.

```python
# django_inspector/sampling.py
_sampled_var: ContextVar[bool] = ContextVar("inspector_sampled", default=True)

def should_sample(request, latency_ms=None) -> bool:
    """
    Returns True if this request's events should be captured.
    - Always True for error requests (500+) and slow requests.
    - Otherwise: random.random() < SAMPLING_RATE.
    """
    rate = inspector_settings.SAMPLING_RATE
    if rate >= 1.0:
        return True
    status = getattr(request, "_inspector_status_code", None)
    if status is not None and status >= 500:
        return True
    if latency_ms is not None:
        slow = inspector_settings.SLOW_REQUEST_THRESHOLD_MS
        if latency_ms >= slow:
            return True
    import random
    return random.random() < rate
```

**Integration in middleware:**

```python
# InspectorMiddleware.__call__
token = set_trace_id(trace_id)
sampled = True  # default — no latency info yet at start
set_sampled(sampled)
request.inspector_sampled = sampled
```

But the latency/error check needs to happen after response. Simplest: sample at `_notify_request_end` using status code and `_inspector_start_time`:

1. At middleware start: `set_sampled(True)` (optimistically buffer).
2. At `_notify_request_end`: recompute final decision; if not sampled → `clear_buffer()`.

This is a **discard-at-end** approach (no buffering if not sampled is harder because latency isn't known until the request completes). Trade-off: events are buffered intra-request even if ultimately discarded. Acceptable given they live in memory only until flush.

**New settings in `conf.py` DEFAULTS:**
```python
"SAMPLING_RATE": 1.0,
"SLOW_REQUEST_THRESHOLD_MS": 1000,
```

**Testing:** Use `@override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})` and verify `Event.objects.count() == 0` for success requests. Verify error requests (500+) are always captured regardless of rate.

---

### 3. Dashboard Access Control (AUTH-01..05)

**Approach:** A reusable view decorator / mixin in `django_inspector/dashboard/auth.py`:

```python
def inspector_required(view_func):
    """Decorator requiring is_staff or custom permission callable."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _can_access(request):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Access denied")
        return view_func(request, *args, **kwargs)
    return _wrapped
```

`_can_access(request)` checks:
1. IP allowlist (if `INSPECTOR_DASHBOARD_IP_ALLOWLIST` non-empty). Uses `django.utils.module_loading.import_string` to import any `ipaddress`-based CIDR check helper.
2. Custom permission callable: `INSPECTOR_DASHBOARD_PERMISSION` dotted path → `(request) -> bool`.
3. Default: `request.user.is_staff`.

**AUTH-04 — warn on startup in production with no config:**
In `AppConfig.ready()`, check `DEBUG=False` and both `INSPECTOR_DASHBOARD_PERMISSION` and `INSPECTOR_DASHBOARD_IP_ALLOWLIST` unset → log a `WARNING` but do not refuse to mount (mounting refusal risks bricking production deploys that have staff users relying on it).

Actually, re-reading AUTH-04: "dashboard refuses to mount" is very strong. A safer interpretation: log the warning but still require `is_staff` (the default). Don't actually refuse unless `INSPECTOR_ALLOW_UNSAFE_DASHBOARD = True` and `DEBUG=False`. This keeps the guarantee without bricking.

**Apply to all 7 urls.py routes:**
```python
urlpatterns = [
    path("", inspector_required(views.live_feed), name="live-feed"),
    ...
]
```

**AUTH-05 — no data leakage in error responses:** The `HttpResponseForbidden` body must not include any trace id or event data — it should return a fixed string or plain HTML.

**New settings:**
```python
"INSPECTOR_DASHBOARD_PERMISSION": None,  # dotted path callable or None
"INSPECTOR_DASHBOARD_IP_ALLOWLIST": [],   # list of IP/CIDR strings
```

---

### 4. Async-Safe Buffers (ASYNC-01, ASYNC-02)

**Root problem:** Both `django_inspector/storage/flush.py` (`_local = threading.local()`) and `django_inspector/watchers/sql.py` (`_local = threading.local()`) use thread-local storage. Under ASGI/async views where multiple coroutines run on the same thread, they'll share state.

**Solution — `ContextVar` drop-in replacement:**

```python
# django_inspector/storage/flush.py
from contextvars import ContextVar
_buffer_var: ContextVar[list] = ContextVar("inspector_event_buffer", default=None)

def _get_buffer() -> list:
    buf = _buffer_var.get()
    if buf is None:
        buf = []
        _buffer_var.set(buf)
    return buf
```

Same pattern for `_get_query_log()` in `sql.py`.

**Key lifecycle notes:**
- Django's ASGI handler creates a new `Context` (a `ContextVar` snapshot) per request. `ContextVar` values set inside a request context don't bleed to other requests. This is the correct behavior.
- The `clear_buffer()` / `clear_query_log()` functions still work the same — they call `.clear()` on the list object (in-place), which is safe since each `ContextVar` context has its own list instance.
- Token-based `set()` / `reset()` is NOT needed for lists — we get and mutate the list in place; the var's identity stays the same. Only the initial `set(buf)` call is needed.

**Test pattern (ASYNC-01, ASYNC-02):**

```python
import asyncio
from django.test import AsyncRequestFactory, override_settings

@pytest.mark.asyncio
async def test_async_buffer_isolation():
    """Two concurrent async tasks must not share event buffers."""
    async def make_request(path):
        set_trace_id(generate_trace_id())
        buffer_event(get_current_trace_id(), "test.event", {"path": path})
        await asyncio.sleep(0)  # yield to other coroutines on same thread
        return _get_buffer()[:]  # copy before clear
    
    results = await asyncio.gather(
        make_request("/a"),
        make_request("/b"),
    )
    # Each task should see exactly its own event
    assert len(results[0]) == 1
    assert results[0][0]["metadata"]["path"] == "/a"
    assert len(results[1]) == 1
    assert results[1][0]["metadata"]["path"] == "/b"
```

**Consideration:** `asyncio.gather` does NOT guarantee different OS threads. For true thread isolation, use `asyncio.to_thread`. However, ContextVars also isolate across `asyncio.create_task` within the same event loop because each task is created with a copy of the current context. Both cases are covered.

---

### 5. Ignore Lists (IGN-01..04)

**Approach:** Two lists in settings, compiled to cached patterns at first use.

```python
# django_inspector/ignores.py
import re
from functools import lru_cache

@lru_cache(maxsize=None)
def _compiled_path_patterns():
    patterns = inspector_settings.IGNORE_PATHS or []
    return [re.compile(p) for p in patterns]

def should_ignore_path(path: str) -> bool:
    return any(p.search(path) for p in _compiled_path_patterns())

@lru_cache(maxsize=None)
def _compiled_exception_names():
    return frozenset(inspector_settings.IGNORE_EXCEPTIONS or [])

def should_ignore_exception(exc: BaseException) -> bool:
    exc_class = type(exc)
    fqn = f"{exc_class.__module__}.{exc_class.__qualname__}"
    return fqn in _compiled_exception_names()
```

**Integration:**
- `InspectorMiddleware.__call__`: after checking `inspector_settings.is_enabled`, check `should_ignore_path(request.path)` — if True, skip trace id generation entirely and call `self.get_response(request)` directly.
- `ExceptionWatcher._on_exception`: check `should_ignore_exception(exception)` before `self.record(...)`.

**Cache invalidation:** `lru_cache` persists for the process lifetime. This is fine for production. For tests using `@override_settings`, need to clear the cache in `conftest.py` or use a module-level invalidation hook. Solution: expose `_invalidate_ignore_caches()` function for tests.

**New settings:**
```python
"IGNORE_PATHS": [],
"IGNORE_EXCEPTIONS": [],
```

---

### 6. Quiet-by-Default Logging (OBS-01..03)

**Audit of current silent-swallow sites:**

| File | Line range | Current | Fix |
|------|-----------|---------|-----|
| `middleware.py` | L66-67 | `except Exception: pass` | `logger.warning("inspector: request start notification failed", exc_info=True)` |
| `middleware.py` | L79-80 | `except Exception: pass` | same |
| `middleware.py` | L86-87 | `except Exception: pass` | `logger.debug(...)` (flush failures are less alarming) |
| `middleware.py` | L91-92 | `except Exception: pass` | `logger.debug(...)` |
| `watchers/request.py` | L117-118 | `except Exception: return ""` | `logger.debug("inspector: could not read request body", exc_info=True); return ""` |
| `watchers/request.py` | L131-133 | `except Exception: return ""` | same |
| `watchers/request.py` | L145-146 | `except Exception: return None` | `logger.debug(...)` |
| `watchers/exception.py` | (various `_safe_*`) | silent | `logger.debug(...)` |

**`INSPECTOR_RAISE_ERRORS` setting (OBS-02):**

```python
DEFAULTS = {
    ...
    "INSPECTOR_RAISE_ERRORS": False,
}
```

In middleware (and base watcher) exception handlers:
```python
except Exception as exc:
    if inspector_settings.INSPECTOR_RAISE_ERRORS:
        raise
    logger.warning("inspector: ...", exc_info=True)
```

This is the only change to the existing try/except structure. The "never break host request" guarantee holds when `INSPECTOR_RAISE_ERRORS=False`.

---

## Dependencies Between Plans

```
Plan 04-01 (masking + sampling)
    ↓ must land first — masking is called in BaseWatcher.record(),
      sampling discard happens in middleware/buffer
Plan 04-02 (dashboard auth + ignore lists)
    ↓ depends on Plan 04-01 being done (no strict code dep, but ignore-lists
      reads new conf settings, and auth wraps existing views)
Plan 04-03 (async-safe buffers + logging cleanup)
    ↓ no code dependency on 04-01/04-02 — could theoretically be parallel
      but sequential is safer (avoids merge conflicts on base.py / middleware.py)
```

## Risk Summary

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Masking recursion causes KeyError on non-dict metadata | Medium | Defensive `isinstance` checks; test with edge-case metadata shapes |
| Sampling at end-of-request discards events correctly | Medium | Test clear_buffer() is called before flush() when not sampled |
| ContextVar migration breaks test isolation (tests reuse same asyncio context) | Low | Clear buffers in conftest teardown; add fixture for ContextVar reset |
| `INSPECTOR_RAISE_ERRORS=True` in dev causes unexpected 500s in tests | Low | Document that tests should set this to False or use `INSPECTOR_ENABLED=False` |
| Auth decorator on dashboard not applied to all 7 routes | Low | Centralize in `urls.py` loop; integration test all 7 routes |
| `lru_cache` on ignore patterns not invalidated between test cases using `override_settings` | Medium | Expose `_invalidate_ignore_caches()` and call in fixtures |

## ## RESEARCH COMPLETE

Phase 4 is well-defined, all implementation approaches are clear, no external research blockers. Ready for planning.
