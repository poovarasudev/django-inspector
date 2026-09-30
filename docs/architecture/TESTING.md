# Testing

> **Snapshot:** written at v1.0 (2026-05-23), before Phase 4 added `masking.py`, `sampling.py`, `ignores.py`, `dashboard/auth.py` and the ContextVar buffers. Check the code before relying on details here, and refresh this file when you touch the area it describes.

## Tooling

- **Runner**: `pytest` (`>=7.0`).
- **Django adapter**: `pytest-django` (`>=4.5`).
- **Coverage**: `coverage` (`>=7.0`) — installed, but no coverage config in `pyproject.toml` and no CI gating reports today.
- **Settings**: `tests/settings.py` configures an in-memory SQLite DB, installs `django_inspector`, mounts `InspectorMiddleware`, and turns the inspector ON.
- **Configured** in `[tool.pytest.ini_options]`: `DJANGO_SETTINGS_MODULE = "tests.settings"`, file pattern `test_*.py`, class pattern `Test*`, function pattern `test_*`.

## How to Run

```bash
# all tests
uv run pytest

# verbose
uv run pytest -v

# single file
uv run pytest tests/test_sql_watcher.py

# single test by node id
uv run pytest tests/test_sql_watcher.py::test_records_duration

# with coverage
uv run coverage run -m pytest && uv run coverage report
```

## Layout

| File | Tests | Subject |
|------|-------|---------|
| `tests/test_tracing.py` | 8 | `tracing.context` (set/get/clear, ContextVar semantics, isolation between threads/contexts) |
| `tests/test_watcher_base.py` | 6 | `watchers.base.BaseWatcher` lifecycle, `watchers.registry` registration, enable/disable idempotency |
| `tests/test_storage.py` | 6 | `storage.flush` buffer mechanics + `Event` model bulk_create |
| `tests/test_request_watcher.py` | 16 | REQ-01..REQ-06: method/path/headers/body/status/latency/user/session/IP, ignore-self-prefix |
| `tests/test_sql_watcher.py` | 16 | SQL-01..SQL-07: capture/timing/db_alias/origin/slow/N+1/duplicates |
| `tests/test_exception_watcher.py` | 13 | EXC-01..EXC-04: type/message/stack/locals/chained/request-context |
| **Total** | **65** | |

## Patterns

### Fixtures

Tests do not currently use shared `conftest.py` fixtures. Each test sets up what it needs:

```python
from django_inspector.tracing.context import set_trace_id, clear_trace_id

@pytest.fixture
def trace():
    token = set_trace_id("test-trace-id")
    yield "test-trace-id"
    clear_trace_id(token)
```

`pytest_mark.django_db` (or its decorator form) is applied liberally because most assertions look at `Event` rows.

### Watcher unit tests bypass middleware

Tests typically set the trace id manually with `set_trace_id()`, instantiate the watcher, call `.enable()`, exercise it, then read either the in-memory buffer or persisted `Event` rows.

### Integration with `flush_events()`

For tests that need to see persisted `Event` rows, the test calls `flush_events()` directly rather than running middleware. For tests asserting against the buffer, no flush is needed.

## Coverage Gaps

| Area | Status |
|------|--------|
| `dashboard/views.py` | **No tests** |
| `dashboard/urls.py` | **No tests** |
| `templates/inspector/*.html` | **No tests** |
| `management/commands/inspector_cleanup.py` | **No tests** |
| `middleware.py` | Exercised indirectly via watcher tests' setup; no direct integration test of the middleware flow end-to-end |
| `apps.py` `_autodiscover_watchers` | No explicit test |
| Async path (ASGI) | No async tests; project ships sync middleware only |
| Multi-DB alias scenarios | SQL watcher tested on default alias only |

## Conventions for New Tests (v1.1+)

When adding watchers in v1.1 (Cache, Template, Signal, Logging), each should ship with a `tests/test_<name>_watcher.py` mirroring the existing pattern:

1. **Lifecycle test** — `enable()` / `disable()` is idempotent; hooks installed/removed correctly.
2. **Capture test** — recorded event has expected `event_type` and metadata keys.
3. **Trace correlation test** — `trace_id` matches the active `ContextVar` value.
4. **No-trace test** — calling the hook with no active trace id should not raise and should not buffer.
5. **Self-recursion test** (where applicable) — internal inspector operations don't feed the watcher.
6. **Edge cases** — empty inputs, unserializable values, very large payloads.

For features that need to be covered going forward:

- **Masking** — assert sensitive keys are redacted in persisted metadata.
- **Sampling** — given a sample rate of 0, no events are buffered for success requests; given an error request, events are always captured.
- **Dashboard auth** — anonymous access returns 403/redirect; staff user gets 200.
- **Retention** — `inspector_cleanup --hours N` deletes only events older than N hours; `--dry-run` reports without deleting.

## Test Performance

- In-memory SQLite + small tests → suite should run in well under 5 seconds.
- No parallel test runners configured. Adding `pytest-xdist` becomes worthwhile when the suite grows past ~30s.
