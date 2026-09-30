# Phase 9 — Trace API

**Status:** Approved in chat · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** TRACE-01..07, CMD-04 (moved here from Phase 10) (`docs/requirements.md`)
**Roadmap:** Phase 9 (`docs/roadmap.md`) · **Builds on:** Phase 8 (`2026-09-30-event-schema-v2-design.md`)

## Goal

Traces can start anywhere, not only inside an HTTP request:
- A public `trace()` starts a trace from any code.
- The middleware is rebuilt on the same lifecycle.
- Two ways lead from an app back to its trace: an `X-Inspector-Trace` response header and a logging filter.
- A trace page shows traces that aren't requests.

## Decisions

| Decision | Choice |
|---|---|
| Shared lifecycle | Internal `TraceScope` in `tracing/scope.py`, used by the middleware and by `trace()` |
| Public API | `trace(name)` as `with`, `async with`, and a decorator on sync and async functions; exported as `django_inspector.trace` |
| Nesting | A `trace()` opened inside an active trace joins it: no new trace, no flush, no anchor event |
| Anchor event | `trace.completed` (`name`, `duration_ms`, `status`, `error_type`); never dropped by `MAX_EVENTS_PER_TRACE` |
| Exceptions in `trace()` | Recorded through the exception watcher's new public `capture()`, then re-raised unchanged |
| Sampling | `should_keep(failed, duration_ms, early_pick)` shared by requests and `trace()` |
| Header setting | `TRACE_HEADER`: `"debug"` (default), `True` or `False`; the header is only added to kept traces |
| Log filter | `django_inspector.tracing.TraceIdFilter` |
| General trace page | CMD-04 moves into this phase, because `trace()` is where traces that aren't requests first appear |

Alternatives rejected:
- **The middleware calls the public `trace()`:** request specifics (request watcher, response-based sampling, header) would leak into the public API.
- **`trace()` gets its own copy of the lifecycle:** it breaks TRACE-02 and leaves two lifecycles to keep in sync.

## `TraceScope` (`django_inspector/tracing/scope.py`)

```python
class TraceScope:
    trace_id: str
    pick: Optional[bool]        # early_pick() result
    started: float              # time.monotonic()

    @classmethod
    def start(cls, trace_id: Optional[str] = None) -> "TraceScope": ...
    def elapsed_ms(self) -> float: ...
    def flush(self, keep: bool) -> None: ...
    def close(self) -> None: ...
```

- **`start()`:** generates the trace id when none is given, sets it, and starts a fresh event buffer (`start_buffer`), fresh query stats (`start_query_log`) and the early-sampling detail flag (`start_detail(pick is not False)`). It keeps every token.
- **`flush(keep)`:** does `clear_buffer()` if not `keep`, then `flush_events()`, `maybe_prune()` and `clear_query_log()`. Each step keeps today's try/log/raise-on-`INSPECTOR_RAISE_ERRORS` handling, moved as-is from `InspectorMiddleware._flush`. It is sync and touches the database: async callers run it with `sync_to_async`.
- **`close()`:** resets the detail flag, query log, buffer and trace id tokens, in reverse order. It must run in the caller's own context, never inside `sync_to_async`, because a ContextVar token can only be reset in the context that set it.
- **`is_active()`:** a module-level helper that returns `get_current_trace_id() is not None`.

## Sampling (`django_inspector/sampling.py`)

```python
def should_keep(failed: bool, duration_ms: Optional[float], early_pick=None) -> bool: ...
```

- It returns True when `SAMPLING_RATE >= 1`, `early_pick is True`, `failed`, or `duration_ms >= SLOW_REQUEST_THRESHOLD_MS`.
- Otherwise it returns False if `early_pick is False`, and `random.random() < rate` if not.
- `compute_sampling_decision(request, response, early_pick)` keeps its signature. It becomes: `failed = status >= 500`, `duration_ms` from `request._inspector_start_time`, then `should_keep(...)`. SAMP-01..05 behaviour is unchanged.
- The `SLOW_REQUEST_THRESHOLD_MS` setting now applies to every trace. The README row wording changes accordingly; the setting name stays.

## Public `trace()` (`django_inspector/tracing/api.py`)

```python
from django_inspector import trace

with trace("nightly-report"):
    ...

async with trace("sync-prices"):
    ...

@trace("send-digest")
def send_digest(): ...

@trace("refresh-cache")
async def refresh_cache(): ...
```

- **Export.** `trace` is a class exported from `django_inspector/__init__.py` and from `django_inspector.tracing`. Importing it must not import Django models: every storage or watcher import inside it is deferred to call time.
- **One-shot.** A `trace` instance can't be entered twice at once. Each `with`, and each decorated call, creates its own internal run state, so one decorator object can be used by concurrent calls.

### Behaviour of one run

1. **Disabled inspector** (`inspector_settings.is_enabled` is False): the block runs and nothing else happens.
2. **A trace is already active:** the run joins it. The block runs and nothing is recorded, flushed or reset. `__enter__` returns the outer trace id.
3. **Otherwise:**
   1. `scope = TraceScope.start()`; `__enter__` returns `scope.trace_id`.
   2. The block runs.
   3. If the block raised, the exception watcher's `capture((type, value, tb))` records it.
   4. The run records the anchor `trace.completed` with metadata `{"name": name, "duration_ms": scope.elapsed_ms(), "status": "error" | "ok", "error_type": "<module.qualname>" | None}`. The anchor goes through `mask_metadata` and `buffer_event`, the same path `record()` uses. It has to be recorded directly because `trace()` isn't a watcher.
   5. `keep = should_keep(failed, duration_ms, scope.pick)`.
   6. `scope.flush(keep)`, through `sync_to_async` on the async path.
   7. `scope.close()`.
   8. The user's exception, if any, propagates unchanged: same object, same traceback. `__exit__` returns False.
- **Inspector failures.** Any exception from the inspector's own steps (start, capture, anchor, flush) is logged with `logger.warning(..., exc_info=True)` and swallowed, unless `INSPECTOR_RAISE_ERRORS` is on. Such a failure must never replace or hide the user's exception.
- **`close()` always runs** once `start()` has succeeded (`finally`).
- **Plain `with` in async code.** The flush would hit Django's `SynchronousOnlyOperation`, which is caught and logged like any inspector failure. The docstring and README say to use `async with` or the decorator in async code.
- **Decorator.** `__call__(func)` returns a `functools.wraps` wrapper. It's an `async def` wrapper when `asyncio.iscoroutinefunction(func)` is true, otherwise a plain one. Each call opens a fresh run with the same name.

### Watcher changes

- **Exception watcher.** `ExceptionWatcher._on_exception(sender, request=None, **kwargs)` splits out a public `capture(exc_info, request=None)`, which holds the current body minus the `sys.exc_info()` call. The signal handler calls `capture(sys.exc_info(), request)`.
  - `capture()` does nothing when the watcher is disabled. Its existing `record()` checks cover that.
  - `IGNORE_EXCEPTIONS` still applies.
  - It is looked up through `registry.get_instance("exception")`. When the watcher isn't registered or is disabled, the exception simply isn't recorded, and the `trace.completed` anchor still says `status: "error"`.
- **Flush.** `storage/flush.py`: `_NEVER_DROPPED` becomes `{"request.completed", "trace.completed"}`.

## Middleware (`django_inspector/middleware.py`)

- `__call__` and `__acall__` keep their structure but use `TraceScope`.
- `start()` replaces the manual `set_trace_id` / `_start_request_state` calls.
- `flush(keep)` replaces the clear-buffer-if-not-sampled plus `_flush()` sequence. It runs through `sync_to_async` on the async path.
- `close()` goes in the `finally`.
- `request.inspector_trace_id` and `request.inspector_sampled` stay exactly as today.
- `_start_request_state`, `_reset_request_state` and `_flush` are removed; tests that patch them are updated to patch `TraceScope`.

**TRACE-02 is proven by the unchanged middleware, sampling, ASGI and async-isolation test modules.** Only tests that reach into the removed private helpers may change.

### `X-Inspector-Trace` header (TRACE-05)

- After the sampling decision, when `sampled` is True and the header is enabled, the middleware sets `response["X-Inspector-Trace"] = trace_id`.
- "Enabled" means `TRACE_HEADER is True`, or `TRACE_HEADER == "debug"` and `settings.DEBUG`.
- An unkept trace never gets the header: it would point at a trace that doesn't exist.

## Settings

| Key | Default | Rule (`checks.py`) |
|---|---|---|
| `TRACE_HEADER` | `"debug"` | `True`, `False` or `"debug"`; anything else is an invalid-setting error like the existing ones |

It gets a `conf.DEFAULTS` entry and a README settings-table row (`tests/test_docs.py` enforces the row).

## Log filter (TRACE-06)

`django_inspector/tracing/logging.py` holds `TraceIdFilter(logging.Filter)`:
- `filter(record)` sets `record.inspector_trace_id = get_current_trace_id() or "-"` and returns True.
- The module is named `logging.py` inside the `tracing` package. The package's modules use absolute imports, so it doesn't shadow the standard library's `logging`, but the module itself must `import logging` absolutely (it's Python 3 only anyway).
- It is re-exported from `django_inspector.tracing`.

The README shows the wiring:

```python
LOGGING = {
    "filters": {"trace_id": {"()": "django_inspector.tracing.TraceIdFilter"}},
    "formatters": {"plain": {"format": "%(levelname)s [%(inspector_trace_id)s] %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["trace_id"], "formatter": "plain"}},
    ...
}
```

## Dashboard: traces that aren't requests (CMD-04)

**Anchor types.** A module constant `TRACE_ANCHOR_TYPES = ("trace.completed",)` lives in `dashboard/views.py`. Phases 10 and 15 add `command.completed` and `task.completed` to it.

**`/trace/<id>/` (`trace_detail`):**
- A request trace (`request.completed` exists) redirects to the request page, as today.
- Otherwise, the trace's anchor is the first event whose type is in `TRACE_ANCHOR_TYPES`. With no anchor and no events, the view returns 404.
- With events but no anchor (e.g. an anchor dropped by a crash), it still renders, with an "incomplete trace" notice.
- It renders `inspector/traces/detail.html`, which reuses the request page's pieces where they fit:
  - Header: the anchor's name, status badge and duration.
  - The trace id, the pin button (`_pin_button.html`) and an Export JSON link.
  - The event timeline and waterfall (the `_waterfall` helper, with the anchor as the time origin), capped at `MAX_DETAIL_EVENTS` like the request page.
- The JSON export uses a new `trace/<id>/export.json` (`trace-export`) with the same shape as `request_export`. `request_export` and `trace_export` share a helper.

**`/traces/` (`traces_list`, sidebar entry "Traces"):**
- It pages the anchor events of `TRACE_ANCHOR_TYPES`, newest first.
- Columns: name, status, duration, time, and a pinned badge.
- Filters: `name` (icontains on `metadata__name`), `status` (ok or error) and the date range, reusing `_filter_dates`.
- It follows the existing list-page pattern: full page, plus an HTMX table partial on `HX-Request`.

## Error handling

- `trace()`: inspector failures are logged and swallowed (or raised with `INSPECTOR_RAISE_ERRORS`). The user's exception always propagates unchanged, and `close()` always runs after a successful `start()`.
- Middleware: unchanged semantics. A header failure can't happen in practice, but header code sits inside the existing guarded block.
- Trace page: 404 for an unknown trace; it renders safely when the anchor is missing.

## Testing

| File | Covers |
|---|---|
| `tests/test_trace_api.py` (new) | `with`, `async with`, sync decorator and async decorator all produce one kept trace with a `trace.completed` anchor; `__enter__` returns the trace id; SQL, `log.record` and exception events inside the block share it; nested `trace()` joins (one anchor, one flush, the outer id returned); the exception is recorded, then re-raised as the same object with its traceback; `status`/`error_type` in the anchor; `SAMPLING_RATE=0` drops an OK run but keeps a failing run and a slow run (patched clock or threshold); disabled inspector does nothing; a failing `flush_events` is logged and doesn't break the block, while `INSPECTOR_RAISE_ERRORS` raises; a failing inspector step during an exception still re-raises the user's exception; context is fully reset afterwards (no trace id, a fresh buffer next time); concurrent `async with` runs under `asyncio.gather` stay isolated; one decorated function called concurrently gets separate traces; importing `django_inspector` doesn't import the storage models |
| `tests/test_tracing.py` | `TraceScope` start/flush/close; `close()` after an async flush restores the context; `flush(keep=False)` writes nothing |
| `tests/test_sampling.py` | `should_keep` truth table; `compute_sampling_decision` behaviour unchanged |
| `tests/test_exception_watcher.py` | `capture()` records outside a request; honours `IGNORE_EXCEPTIONS`; the signal path unchanged |
| `tests/test_middleware_header.py` (new) | `TRACE_HEADER` `"debug"` with `DEBUG` on or off, `True`, `False`; no header on an unkept trace; sync and async paths |
| `tests/test_log_filter.py` (new) | Inside a trace the record gets the id, outside it gets `"-"`; always returns True; works through `logging.config.dictConfig` |
| `tests/test_checks.py` | `TRACE_HEADER` accepted values and an invalid value |
| `tests/test_dashboard_pages.py` / `test_dashboard_features.py` | Trace page for a `trace.completed` trace; request traces still redirect; 404 for an unknown id; missing-anchor notice; trace export JSON; Traces list, filters and HTMX partial; sidebar link; access control on the new views |
| `tests/test_storage.py` | `trace.completed` is never dropped past `MAX_EVENTS_PER_TRACE` |
| Existing middleware, sampling, ASGI, async-isolation modules | Pass unchanged (TRACE-02) |

Then:
- the full suite, `ruff check`, `mypy`, `uv build && python scripts/check_dist.py dist/`
- `scripts/benchmark.py` (TRACE-07): p50 overhead stays within budget and near the Phase 8 figure (0.73 ms)

## Docs

- `README.md`:
  - A "Tracing code outside requests" section (`trace()` forms, joining, sampling, async note).
  - The header and `TRACE_HEADER` row.
  - The log-filter wiring.
  - The Traces page.
- `CHANGELOG.md`: 0.3.0 (unreleased) entries citing TRACE-01..07 and CMD-04.
- `docs/requirements.md`:
  - CMD-04 moves under Phase 9 in the traceability table, with a note.
  - On finishing, tick TRACE-01..07 and CMD-04.
- `docs/roadmap.md`:
  - Add the general trace page to Phase 9's work items and success criteria.
  - Remove it from Phase 10.
  - On finishing, tick Phase 9.
