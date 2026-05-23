# Summary: Plan 04-03 — Async-Safe Buffers + Loud-by-Default Logging

**Phase:** 4 — Safety & Hardening
**Plan:** 3 of 3
**Status:** Complete
**Completed:** 2026-05-23

## What Was Built

### Modified Files
- **`django_inspector/storage/flush.py`** — Replaced `threading.local` with `ContextVar[List[dict]]`. `_get_buffer()` lazy-initializes per-context list. `flush_events()` and `clear_buffer()` work correctly with the new ContextVar.
- **`django_inspector/watchers/sql.py`** — Replaced `threading.local` with `ContextVar` for `_query_log_var`. `_get_query_log()` and `clear_query_log()` updated accordingly.
- **`django_inspector/middleware.py`** — Added `logger = logging.getLogger("django_inspector")`. Silent `except Exception: pass` in `_notify_request_start`, `_notify_request_end`, flush, and clear_query_log now log at `DEBUG` or `WARNING`. All guarded blocks check `INSPECTOR_RAISE_ERRORS` for re-raise. **Critical fix**: `clear_trace_id(token)` moved to an inner `finally` so it always runs even if `_flush()` raises.
- **`django_inspector/watchers/sql.py`** — `except Exception: pass` around `inst.record("sql.query", ...)` upgraded to `logger.warning` with `INSPECTOR_RAISE_ERRORS` support.
- **`django_inspector/watchers/request.py`** — `except Exception: return ""` in `_safe_body` and `_safe_response_body` upgraded to `logger.debug`.

### New Tests
- **`tests/test_async_isolation.py`** — 6 tests: buffer and query log start empty in fresh context, clear empties context, two concurrent coroutines get isolated buffers (no cross-contamination).
- **`tests/test_observability.py`** — 5 tests: flush error doesn't propagate by default, flush error propagates with `INSPECTOR_RAISE_ERRORS=True`, flush failure logged at WARNING, `INSPECTOR_RAISE_ERRORS` accessible from settings.

## Commits
- `b1c9d30` — feat(04-03): migrate flush.py + sql.py threading.local → ContextVar (ASYNC-01, ASYNC-02)
- `c707365` — feat(04-03): replace silent except blocks with logger.warning/debug + INSPECTOR_RAISE_ERRORS (OBS-01..03)
- `8b483ac` — test(04-03): async isolation tests + observability tests; fix clear_trace_id always-runs guard

## Verification

- `uv run python -m pytest tests/` → **128 passed, 0 failed**
- `asyncio.gather(task_a, task_b)` each writing to `_buffer_var` → cross-contamination: None ✓
- `asyncio.gather(task_a, task_b)` each writing to `_query_log_var` → cross-contamination: None ✓
- Flush failure with `INSPECTOR_RAISE_ERRORS=False` → response 200 + WARNING log ✓
- Flush failure with `INSPECTOR_RAISE_ERRORS=True` → RuntimeError propagated ✓

## Self-Check: PASSED

All 8 plan tasks executed. All acceptance criteria met. 128/128 tests pass.

## Deviations from Plan

**[Rule 1 - Bug Fix] `clear_trace_id` not guaranteed after `_flush()` raises.**
Found during: Task 8 (test_observability.py) | Issue: when `INSPECTOR_RAISE_ERRORS=True` and `flush_events` raises, `_flush()` propagates the exception inside the outer `finally` block. In Python, a `raise` inside a `finally` block exits the block before subsequent statements run — so `clear_trace_id(token)` was never called, leaking the trace ID into the test process ContextVar, corrupting 4 subsequent tests (`test_set_and_get_trace_id`, `test_middleware_clears_trace_id_after_response`, `test_middleware_skips_when_disabled`, `test_does_not_record_without_trace`) | Fix: nested `_flush()` inside `try/finally: clear_trace_id(token)` — guarantees cleanup regardless of flush outcome | Files modified: `middleware.py` | Verification: 128 tests pass | Commit: `8b483ac`

**Total deviations:** 1 auto-fixed. **Impact:** Critical correctness fix — async context cleanup is now guaranteed even when inspector errors propagate. This is a general correctness improvement beyond the immediate test case.

## Requirements Satisfied

- ASYNC-01: `sql.py` query log migrated from `threading.local` to `ContextVar` ✓
- ASYNC-02: `flush.py` event buffer migrated from `threading.local` to `ContextVar` ✓
- OBS-01: All previously silent `except Exception: pass` blocks now log at WARNING/DEBUG ✓
- OBS-02: `INSPECTOR_RAISE_ERRORS` setting enables error propagation for dev/test ✓
- OBS-03: Host request still returns 200 when inspector errors occur (default mode) ✓
