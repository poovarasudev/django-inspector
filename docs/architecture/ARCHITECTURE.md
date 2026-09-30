# Architecture

> **Snapshot:** written at v1.0 (2026-05-23), before Phase 4 added `masking.py`, `sampling.py`, `ignores.py`, `dashboard/auth.py` and the ContextVar buffers. Check the code before relying on details here, and refresh this file when you touch the area it describes.

## One-Line Summary

`django-inspector` is a pluggable Django app that instruments incoming requests, intercepts SQL queries and exceptions, buffers normalized **Event** records under a per-request `trace_id`, bulk-writes them via the Django ORM at end-of-request, and exposes a server-rendered HTMX dashboard for live feed, request detail (with timeline/waterfall), query inspection, and exception inspection.

## High-Level Flow

```
HTTP request
   ↓
InspectorMiddleware (django_inspector/middleware.py)
   │  • generate UUID4 trace_id, set ContextVar
   │  • notify RequestWatcher.on_request()
   ↓
View executes (user code)
   │
   ├── ORM queries → connection.execute_wrappers → SQLWatcher._query_wrapper
   │                    → record("sql.query", metadata) → buffer
   ├── Unhandled exception → got_request_exception signal → ExceptionWatcher._on_exception
   │                    → record("exception.raised", metadata) → buffer
   ↓
InspectorMiddleware (response phase)
   │  • RequestWatcher.on_response() → record("request.completed", metadata) → buffer
   │  • flush_events() → Event.objects.bulk_create(...)
   │  • clear_query_log(), clear ContextVar
   ↓
Response returned to client

(separately)
Dashboard request → dashboard/views.py → Event.objects.filter(...) → render template
```

## Module Map

| Module | Responsibility | Key Symbols |
|--------|---------------|-------------|
| `django_inspector/apps.py` | App config; autodiscover and instantiate watchers in `ready()` | `DjangoInspectorConfig`, `_autodiscover_watchers` |
| `django_inspector/conf.py` | Lazy settings accessor with defaults merge | `InspectorSettings`, `inspector_settings`, `DEFAULTS` |
| `django_inspector/middleware.py` | Per-request trace lifecycle + watcher notification + flush | `InspectorMiddleware` |
| `django_inspector/tracing/context.py` | Async-safe trace propagation | `generate_trace_id`, `set_trace_id`, `get_current_trace_id`, `clear_trace_id`, `_trace_id_var` (ContextVar) |
| `django_inspector/watchers/registry.py` | Process-global dict of watcher classes | `register`, `get`, `all_watchers` |
| `django_inspector/watchers/base.py` | ABC for watchers with enable/disable/record lifecycle | `BaseWatcher` |
| `django_inspector/watchers/request.py` | HTTP request/response capture | `RequestWatcher` |
| `django_inspector/watchers/sql.py` | DB instrumentation, slow/N+1/duplicate detection | `SQLWatcher`, `_query_wrapper`, `_detect_n_plus_one`, `_detect_duplicates` |
| `django_inspector/watchers/exception.py` | Unhandled exception capture via `got_request_exception` | `ExceptionWatcher` |
| `django_inspector/storage/models.py` | Single Event model | `Event` |
| `django_inspector/storage/flush.py` | Thread-local event buffer + bulk_create | `buffer_event`, `flush_events`, `clear_buffer` |
| `django_inspector/dashboard/urls.py` | 7 routes for dashboard | `urlpatterns` |
| `django_inspector/dashboard/views.py` | List/detail views with HTMX partials + timeline/waterfall | `live_feed`, `requests_list`, `request_detail`, `queries_list`, `query_detail`, `exceptions_list`, `exception_detail` |
| `django_inspector/management/commands/inspector_cleanup.py` | Retention command (`--hours N --dry-run`) | `Command` |

## Architectural Principles (as implemented)

1. **Watcher registry pattern** — concrete watchers self-register on import; `AppConfig.ready()` imports them then instantiates and enables based on `WATCHERS` settings dict. Adding a new watcher = subclass `BaseWatcher`, set `watcher_name`, call `register(name, cls)`.

2. **`ContextVar`-based trace propagation** — `_trace_id_var: ContextVar[Optional[str]]` ensures the trace id propagates correctly across both threads and asyncio tasks. The trace id is the only field that uses `ContextVar`; the SQL query log and event buffer still use `threading.local()` — see CONCERNS.md.

3. **Buffered writes** — watchers append to an in-memory buffer; bulk_create runs once at end-of-request. Reduces DB round-trips and keeps watcher overhead low on the hot path.

4. **Self-recursion guard** — `_is_inspector_query()` skips queries that touch `django_inspector_event`, and `RequestWatcher.should_ignore_request()` skips the dashboard URL prefix, so the inspector does not feed itself.

5. **Defensive middleware** — every notification path is wrapped in `try/except Exception: pass`. Inspector failures must never break user requests. (Cost: bugs are silent — see CONCERNS.md.)

6. **Event = (trace_id, event_type, timestamp, JSON metadata)** — single flat table; all event-kind heterogeneity goes into `metadata`. Avoids per-watcher tables at the cost of weaker schema.

## What's Missing vs. PRD's Stated Architecture

The PRD describes a `watcher → collector → masking → sampling → storage → dashboard` pipeline. Currently built:

- ✅ watcher
- ❌ collector (watchers write directly to the buffer; no transform stage)
- ❌ masking (PII / secrets persist raw)
- ❌ sampling (every event is recorded)
- ✅ storage (ORM only — no abstract backend interface)
- ✅ dashboard (live feed, lists, detail, timeline/waterfall, HTMX; missing search/comparison/export)

`AbstractStorageBackend`, plugin API, retention policies (only manual `inspector_cleanup` exists), and access control are all unimplemented.

## Async Posture

- Trace id is async-safe (`ContextVar`).
- SQL query log uses `threading.local()` → **not** async-safe; queries from coroutines sharing a thread will pollute each other's logs.
- Event buffer uses `threading.local()` → same caveat.
- Middleware is sync-style (`__call__(request)`). No `async def __call__`. Project ships sync middleware only.
