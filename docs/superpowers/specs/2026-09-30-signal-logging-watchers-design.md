# Phase 6 — Signal & Logging Watchers

**Status:** Approved in chat · **Date:** 2026-09-30 · **Branch:** `feat/phase-6-signal-logging-watchers` (based on `feat/phase-5-cache-template-watchers`)
**Covers:** SIGL-01..06, LOG-01..06 (`docs/requirements.md`), PRD §11.7 and §11.4
**Roadmap:** Phase 6 (`docs/roadmap.md`)

## Goal

Capture two kinds of in-request activity as trace-correlated events with their own dashboard pages:
- Django signal dispatches, with timing for each receiver
- Python log records

## Decisions

| Decision | Choice |
|---|---|
| Signal hook | Per watched signal *instance*: wrap `send`/`send_robust`/`asend`/`asend_robust` and `_live_receivers`. `_live_receivers` hands Django timed receiver wrappers, so Django's own dispatch code runs unchanged. |
| Which signals | `SIGNAL_WATCH_LIST` (new setting), a list of dotted paths. The default is the model signals `pre_save`, `post_save`, `pre_delete`, `post_delete`, `m2m_changed`. |
| Logging hook | A `logging.Handler` on the root logger, with its level set from `LOG_LEVEL_THRESHOLD` (new setting, default `"WARNING"`). Host logger levels are never changed. |
| Defaults | `"signal": False` (opt in), `"log": True` (WARNING+ is low-volume) |
| Dashboard | Full Logs and Signals list and detail pages, plus request-detail integration, following the Phase 5 pattern |

Alternatives rejected:
- **Reimplementing `Signal.send` loops:** the loops differ between Django 4.x and 5.x (async receivers), and `send_robust` has its own failure logging.
- **Wrapping receivers at `connect()`:** this breaks `disconnect()` and weakref semantics.
- **Lowering logger levels so INFO records reach us:** this would change the host's logging behaviour.

## Signal watcher (`django_inspector/watchers/signal.py`)

### Hooking

- On `install_hooks()`, each dotted path in `SIGNAL_WATCH_LIST` is resolved with `import_string`. Entries that fail to import, or that aren't `django.dispatch.Signal` instances, are logged at warning and skipped. The same signal instance is never patched twice. Patched signals carry an `_inspector_signal_name` marker in their `__dict__`.
- For each signal, instance attributes shadow `send`, `send_robust`, `_live_receivers`, and `asend`/`asend_robust` when the class has them (Django ≥ 5.0).
- `remove_hooks()` deletes those instance attributes, which restores the class methods.

### Per-receiver timing

- A `ContextVar` holds the current dispatch, a `_Dispatch` object. The outer `send*` wrapper sets it on entry and resets it on exit, so nested dispatches (e.g. a `post_save` receiver that saves another model) are tracked separately.
- The wrapped `_live_receivers(sender)` calls the original. If no dispatch is active (for example `has_listeners()`), it returns the result unchanged. Otherwise it:
  - Returns timed wrappers for each receiver: a sync wrapper for sync receivers, an `async def` wrapper for async ones.
  - Handles both return shapes: a plain list (Django 4.x) and a `(sync, async)` pair (Django 5.x).
  - Records `receiver_count`.
- **Receivers whose `__module__` starts with `django_inspector` are passed through untouched, and are neither counted nor listed (SIGL-05).**
- Each timed wrapper adds an entry `{receiver, duration_ms, error?}` when it is **called**, so the list follows the real call order (SIGL-03). Receivers that never ran because an earlier one raised in `send()` don't appear.
- After the original `send*` returns, each wrapper in the `(receiver, response)` pairs is replaced with the original receiver, so callers see exactly what they would have without the watcher.

### Event `signal.dispatched`

| Field | Value |
|---|---|
| `signal` | Dotted path from `SIGNAL_WATCH_LIST` |
| `sender` | `module.qualname` for a class sender, the string itself for a `str` sender, the type's dotted path for other objects, `None` for `None` |
| `receiver_count` | Live receivers, excluding the inspector's own |
| `receivers` | Called receivers in order: `{"receiver": "module.qualname", "duration_ms": float, "error": "Type: msg"?}` |
| `duration_ms` | Whole dispatch |
| `method` | `"send"`, `"send_robust"`, `"asend"` or `"asend_robust"` |
| `error` | Only when the dispatch itself raised (`send` propagates receiver errors) |

- A dispatch with `receiver_count == 0` is **not** recorded. Otherwise every save in a request would produce an event.
- With no active trace, the wrapper calls the original directly.

## Logging watcher (`django_inspector/watchers/log.py`)

- `install_hooks()` adds one `InspectorLogHandler` to the root logger, at the level `LOG_LEVEL_THRESHOLD` resolves to. The setting takes a level name or an integer. An unknown value falls back to `WARNING`, with a warning logged. Adding the handler is idempotent.
- `remove_hooks()` removes the handler (LOG-06).
- **Scope, stated in docs:** the handler only sees records that reach the root logger. Loggers with `propagate=False` are skipped, and so are records below a logger's own level.
- `emit()` returns immediately in any of these cases:
  - there is no active trace
  - the record's logger is `django_inspector` or a child of it (LOG-04)
  - the handler is already emitting in this context (a `ContextVar` re-entrancy guard)
- `emit()` never raises. Failures are logged through `django_inspector`, which is itself excluded from capture.

### Event `log.record`

| Field | Value |
|---|---|
| `logger` | `record.name` |
| `level` | `record.levelno` |
| `level_name` | `record.levelname` |
| `message` | `record.getMessage()`, falling back to `str(record.msg)` if formatting fails. Capped at 8192 characters, with `message_truncated: true` when cut. |
| `file`, `line`, `function` | `record.pathname`, `record.lineno`, `record.funcName` |
| `exception_type` | Class name when `exc_info` is present |
| `traceback` | `logging.Formatter().formatException(exc_info)` when `exc_info` is present |

Masking applies through `record()`.

## Settings (additive)

```python
"WATCHERS": {..., "signal": False, "log": True},
"SIGNAL_WATCH_LIST": [
    "django.db.models.signals.pre_save",
    "django.db.models.signals.post_save",
    "django.db.models.signals.pre_delete",
    "django.db.models.signals.post_delete",
    "django.db.models.signals.m2m_changed",
],
"LOG_LEVEL_THRESHOLD": "WARNING",
```

## Dashboard

These follow the Phase 5 pattern: function views, `Paginator(qs, 25)`, HTMX partials, and `inspector_required`.

| Route | Name | Filters |
|---|---|---|
| `logs/` | `logs-list` | `level`: minimum level (`metadata__level__gte`); `logger` (icontains); `message` (icontains) |
| `logs/<pk>/` | `log-detail` | — (full message, source location, traceback, request link) |
| `signals/` | `signals-list` | `signal` (exact, choices from `SIGNAL_WATCH_LIST`); `sender` (icontains) |
| `signals/<pk>/` | `signal-detail` | — (receiver table in call order with durations and errors, request link) |

- HTML templates go in `inspector/logs/*` and `inspector/signals/*`.
- The sidebar gains **Logs** and **Signals** links.

**Request detail:**
- Summary adds `log_records` and `signal_dispatches`.
- Timeline and waterfall get labels and colours for `log.record` and `signal.dispatched`.
- The tabs view adds **Logs (n)** and **Signals (n)**.

## Error handling

The same three-stage pattern as Phase 5:
1. Guarded pre-work.
2. The original call, outside any inspector `try`, so the host's return values and exceptions are unchanged.
3. Guarded recording, which logs at `logger.warning(..., exc_info=True)` unless `INSPECTOR_RAISE_ERRORS` is on.

A receiver's error is recorded on its entry and then re-raised exactly as Django would. `send_robust` still catches it and still logs it through Django.

## Testing

- **`tests/test_signal_watcher.py`**
  - Lifecycle: patch and restore, idempotent, bad watch-list entries skipped with a warning, off by default.
  - Capture: sender, count, and receiver order and timing.
  - Errors: under `send` (recorded and re-raised), under `send_robust` (recorded on the receiver entry, and the response still holds the exception).
  - Responses hold the original receivers.
  - The inspector's own receivers are excluded, and a dispatch left with no receivers isn't recorded.
  - Nested dispatch, unwatched signals, the no-trace case, and `asend` with an async receiver.
  - A real `post_save` from `Model.save()`.
- **`tests/test_log_watcher.py`**
  - Lifecycle: the handler is added and removed, idempotent, on by default.
  - Threshold: the default drops INFO; `"INFO"` and an int both work; an invalid value falls back.
  - Capture: fields, traceback, formatting fallback, truncation.
  - Excluded: the `django_inspector` logger and its children, and the no-trace case.
  - Masking of a card-shaped message.
- **`tests/test_dashboard_pages.py`**
  - Logs and Signals list, filter, partial and detail pages.
  - Request-detail counts and tabs, and the sidebar links.
- **Existing suite stays green** with the logging watcher on by default.

## Out of scope (deferred)

- Signals not in `SIGNAL_WATCH_LIST`. There is no "watch all signals" option.
- Capturing INFO-level records from loggers whose level is above INFO. The host's logging config decides.
- Event timestamps (deferred in Phase 5).
