# Requirements: django-inspector

**Defined:** 2026-05-23
**Core Value:** One dashboard, one trace, complete runtime visibility — every request's full story is reconstructable from a single trace id.

## v1.1 Requirements (Active Milestone)

Requirements for the v1.1 milestone: close PRD Phase 1 watchers (Cache, Template, Signal, Logging) and add production-safety primitives (masking, sampling, dashboard auth, async-safe buffers, ignore lists). Each maps to a roadmap phase.

### Cache Watcher (PRD §11.5)

- [x] **CACHE-01**: Cache `get` operations are captured with key, backend alias, hit/miss flag, and duration
- [x] **CACHE-02**: Cache `set` operations are captured with key, backend alias, TTL, value size (not value itself), and duration
- [x] **CACHE-03**: Cache `delete` and `clear` operations are captured with key (or "*" for clear) and backend alias
- [x] **CACHE-04**: Multi-key cache ops (`get_many`, `set_many`, `delete_many`) are captured with per-key entries or a single event with key count — one event per call with keys (capped at 100) and key_count
- [x] **CACHE-05**: Cache events are correlated to the originating request via `trace_id`
- [x] **CACHE-06**: Cache watcher installs hooks at startup and uninstalls cleanly on `disable()`; idempotent
- [x] **CACHE-07**: Cache watcher is disabled by default in `WATCHERS` until host opts in (consistency with PRD §16 defaults TBD) — off by default ("cache": False)

### Template Watcher (PRD §11.6)

- [x] **TMPL-01**: Each template render is captured with template name, source path, and render duration
- [x] **TMPL-02**: Nested template renders (includes/extends) record parent-child relationship in metadata
- [x] **TMPL-03**: Context size (number of top-level keys, optionally serialized size in bytes) is captured per render — key count and key names; serialized byte size deferred
- [x] **TMPL-04**: Template events are correlated to the originating request via `trace_id`
- [x] **TMPL-05**: Template watcher works with Django's built-in engine; DTL only for v1.1 (Jinja2 deferred)

### Signal Watcher (PRD §11.7)

- [x] **SIGL-01**: Each signal dispatch is captured with signal name (dotted path), sender (class/model name), and number of receivers
- [x] **SIGL-02**: Per-receiver execution time is captured (start → finish), including failures
- [x] **SIGL-03**: Receiver ordering is preserved in event metadata (matches dispatch order)
- [x] **SIGL-04**: Signal events are correlated to the originating request via `trace_id` when dispatched during a request
- [x] **SIGL-05**: Inspector's own signal handlers (e.g., `got_request_exception` in exception watcher) are excluded from capture to prevent self-feeding
- [x] **SIGL-06**: User can configure which signals to watch via `SIGNAL_WATCH_LIST` (default: opt-in subset, not "all signals") — default is the five model signals; the signal watcher itself is off by default

### Logging Watcher (PRD §11.4)

- [x] **LOG-01**: A logging handler installed at startup captures every record routed through Python `logging` at configured level threshold or above — records that reach the root logger (host logger levels and `propagate=False` are respected); on by default
- [x] **LOG-02**: Each log event captures logger name, level (numeric + name), message, file, line, and traceback (if exc_info present)
- [x] **LOG-03**: Log events are correlated to the originating request via `trace_id` when emitted during a request
- [x] **LOG-04**: The inspector's own `django_inspector` logger output is excluded from capture (no self-feeding)
- [x] **LOG-05**: Configurable level threshold (`LOG_LEVEL_THRESHOLD` setting; default `WARNING` to avoid flooding)
- [x] **LOG-06**: Logging watcher uninstalls cleanly on `disable()`; idempotent

### Sensitive-Data Masking (PRD §14)

- [x] **MASK-01**: A configurable `SENSITIVE_KEYS` list redacts matching keys (case-insensitive) in request headers, request body, response headers, response body, cookies, query params, and exception locals
- [x] **MASK-02**: Default `SENSITIVE_KEYS` includes: `password`, `passwd`, `token`, `secret`, `authorization`, `cookie`, `set-cookie`, `csrfmiddlewaretoken`, `api_key`, `api-key`, `x-api-key`, `access_token`, `refresh_token`, `session`, `sessionid`
- [x] **MASK-03**: Redaction replaces values with `"***REDACTED***"`; keys remain visible
- [x] **MASK-04**: A regex-based value matcher catches credit-card-shaped (Luhn-valid 13–19 digits) and JWT-shaped strings regardless of key — Luhn check added after Phase 7 (`fix/masking-fail-closed-luhn`)
- [x] **MASK-05**: Masking is applied **before** `buffer_event` so unmasked data never reaches the DB — fails closed since `fix/masking-fail-closed-luhn`: if masking raises, a `{"masking_failed": true, "error_type": …}` placeholder is stored instead
- [x] **MASK-06**: Nested dicts and lists in metadata are walked recursively (with depth cap to prevent runaway)

### Sampling (PRD §15)

- [x] **SAMP-01**: A `SAMPLING_RATE` setting (float `0.0`–`1.0`, default `1.0`) controls fraction of successful requests captured
- [x] **SAMP-02**: All requests resulting in an unhandled exception are always captured (regardless of sample rate)
- [x] **SAMP-03**: All requests with latency above `SLOW_REQUEST_THRESHOLD_MS` (default `1000`) are always captured
- [x] **SAMP-04**: Sampling decision is made once at middleware entry and applies to **all** watcher events for that trace (no half-captured traces)
- [x] **SAMP-05**: Sampling decision is exposed via `request.inspector_sampled` for downstream code

### Dashboard Access Control (PRD §14)

- [x] **AUTH-01**: Dashboard URLs require `user.is_staff` by default; anonymous and non-staff users get 403
- [x] **AUTH-02**: An `INSPECTOR_DASHBOARD_PERMISSION` setting accepts a dotted-path callable `(request) -> bool` to override the default check
- [x] **AUTH-03**: An optional `INSPECTOR_DASHBOARD_IP_ALLOWLIST` setting (list of CIDRs/IPs) gates access by client IP; empty list = no IP restriction
- [x] **AUTH-04**: When `DEBUG=False` and no permission/IP setting is configured, dashboard refuses to mount and logs a clear warning at startup
- [x] **AUTH-05**: Permission failure responses do not leak event data (no error pages echoing trace ids, etc.)

### Async-Safe Buffers

- [x] **ASYNC-01**: SQL query log uses `ContextVar[list]` instead of `threading.local()` — verified by a test that runs two `async def` views concurrently on one thread and asserts logs don't cross
- [x] **ASYNC-02**: Event buffer uses `ContextVar[list]` instead of `threading.local()` — same concurrency test
- [x] **ASYNC-03**: All v1.1 watchers (Cache, Template, Signal, Logging) are tested under ASGI middleware as well as WSGI — `tests/test_asgi.py`: 5 concurrent requests through `AsyncClient`/`ASGIHandler` plus a WSGI twin; the middleware now also serves async requests natively

### Ignore Lists

- [x] **IGN-01**: `IGNORE_PATHS` setting (list of regex strings, default `[]`) — matched against `request.path`; matching requests are skipped entirely (no trace id, no events)
- [x] **IGN-02**: `IGNORE_EXCEPTIONS` setting (list of dotted-path exception class names) — matching exceptions are not recorded by the exception watcher
- [x] **IGN-03**: Both lists are checked once per request (compiled regex cached at first use)
- [x] **IGN-04**: Documented examples for common ignores: `/healthz`, `/metrics`, `django.http.Http404` — in the README's Configuration section (Phase 7)

### Quiet-by-Default Logging

- [x] **OBS-01**: Every `try/except Exception: pass` block in the package is replaced with `logger.warning("inspector: ...", exc_info=True)` (or `logger.debug` where appropriate)
- [x] **OBS-02**: A new `INSPECTOR_RAISE_ERRORS` setting (default `False`) lets developers opt into raising inspector errors during local development
- [x] **OBS-03**: Existing "never break the host request" guarantee is preserved when `INSPECTOR_RAISE_ERRORS=False`

## v1.2 and v1.3 Requirements ("Beyond the request")

From the product discovery in `docs/discovery/2026-09-30-next-features.md`. Phases 8–13 are v1.2 (package 0.3.0); phases 14–15 are v1.3. Each phase's spec may refine the wording of its IDs.

### Event Schema v2 (PRD §10) — Phase 8

- [ ] **SCHEMA-01**: New nullable `Event` columns `method`, `status_code`, `path` and `duration_ms` are filled when events are written; shipped as a new migration (`0001`/`0002` untouched); existing rows keep NULL (no `kind` column: a trace's anchor `event_type` already names its kind)
- [ ] **SCHEMA-02**: A `fingerprint` column groups like events: SQL events by normalised query shape, exceptions by type plus the innermost application frame's file and function (no line numbers, so it survives unrelated edits)
- [ ] **SCHEMA-03**: A `parent_trace_id` column links a trace to the trace that started it (e.g. a Celery task to its request)
- [ ] **SCHEMA-04**: A `pinned` flag keeps a trace's events out of `RETENTION_HOURS` pruning and `inspector_cleanup`; the dashboard can pin and unpin a trace
- [ ] **SCHEMA-05**: `Event` is registered in the Django admin as read-only (concern C-13)
- [ ] **SCHEMA-06**: Dashboard list filters on method, status and path use the new columns instead of JSON lookups

### Trace API — Phase 9

- [ ] **TRACE-01**: A public `django_inspector.trace(name, ...)` works as a context manager and a decorator, on sync and async code
- [ ] **TRACE-02**: `trace()` owns the whole trace lifecycle (trace id, event buffer, query stats, detail flag, sampling, flush, prune, reset); `InspectorMiddleware` is rebuilt on it with no behaviour change
- [ ] **TRACE-03**: A `trace()` opened while a trace is active joins the outer trace instead of starting a new one
- [ ] **TRACE-04**: Traces that aren't requests are sampled like requests: failed or slow runs are always kept, others at `SAMPLING_RATE`
- [ ] **TRACE-05**: Kept request traces carry an `X-Inspector-Trace` response header; a setting controls it and the default sends it only when `DEBUG` is on
- [ ] **TRACE-06**: A `logging.Filter` sets `record.inspector_trace_id` so application log lines can be matched to traces
- [ ] **TRACE-07**: Middleware overhead stays within the `scripts/benchmark.py` budget after the refactor

### Management Command Watcher (PRD §11.10) — Phase 10

- [ ] **CMD-01**: Capture command name, args, options, stdout, stderr (bounded), duration, exit code
- [ ] **CMD-02**: Commands are traced only when listed in `COMMAND_WATCH_LIST`; `runserver`, `test`, `shell`, `migrate` and `inspector_cleanup` are never traced
- [ ] **CMD-03**: Long commands flush events in chunks and keep bounded query stats, so memory stays flat and a killed command keeps what it already recorded
- [ ] **CMD-04**: `/trace/<id>/` shows any trace (request, command, task) instead of redirecting only to a request

### Query Guard for Tests — Phase 11

- [ ] **TEST-01**: `django_inspector.testing.assert_queries(max=, no_n_plus_one=, no_duplicates=)` works as a context manager with the middleware off and no trace, and writes nothing to the database
- [ ] **TEST-02**: A failure message lists each offending query shape with its count and where it was run from
- [ ] **TEST-03**: A pytest fixture (registered only when pytest is installed) and a `TestCase` decorator wrap the same guard
- [ ] **TEST-04**: An `N_PLUS_ONE` setting (`"off"`, `"warn"`, `"raise"`) reports N+1 queries during development; `"raise"` only takes effect when `DEBUG` is on

### Issues Overview and Export (PRD §12) — Phase 12

- [ ] **DASH-04**: An Issues page groups exceptions by fingerprint (count, first and last seen, example traces), query shapes (count, p95 duration, requests with an N+1) and endpoints (p50, p95, error rate) over a bounded window; counts are marked as estimates when sampling is on
- [ ] **DASH-03**: CSV export of dashboard lists (JSON trace export shipped in 0.2.0)

### Email Watcher (PRD §11.9) — Phase 13

- [ ] **MAIL-01**: Capture recipients, subject, HTML/text body, backend, attachments
- [ ] **MAIL-02**: `.eml` export
- [ ] **MAIL-03**: The dashboard previews an email's HTML body without running its scripts or loading remote content

### HTTP Client Watcher (PRD §11.14) — Phase 14

- [ ] **HTTP-01**: Outgoing calls made with `requests` and `httpx` (sync and async) are captured with method, masked URL, status, duration and error
- [ ] **HTTP-02**: `requests` and `httpx` are imported lazily; neither becomes a dependency, and the watcher is a no-op when they aren't installed
- [ ] **HTTP-03**: Slow hosts appear on the Issues page

### Celery Watcher (PRD §11.12) — Phase 15

- [ ] **CELERY-01**: A task sent during a trace carries that trace's id in its message headers
- [ ] **CELERY-02**: Each task run is its own trace, linked to the sending trace through `parent_trace_id`
- [ ] **CELERY-03**: Capture task name, masked args and kwargs, queue, ETA, retries, worker, result state, exception, duration
- [ ] **CELERY-04**: Celery is imported lazily and offered as an optional extra, never a runtime dependency
- [ ] **CELERY-05**: The trace page links a request to the tasks it sent, and a task back to its request

## v2 (Deferred) Requirements

Acknowledged but not in a planned milestone. Tracked here so they aren't forgotten.

### Model Watcher (PRD §11.8)
- **MODEL-01**: Capture create/update/delete on tracked models with old/new field values, bulk operations
- **MODEL-02**: Configurable per-model opt-in to avoid recording every save

### Middleware Watcher (PRD §11.11)
- **MW-01**: Capture middleware order, per-middleware timing, header mutations

### Async Persistence (PRD §15)
- **PERSIST-01**: Optional thread-pool or Celery offload of `bulk_create` behind a setting

### Retention Policies (PRD §15)
- **RET-01**: Age-based, count-based, and size-based retention policies enforced automatically (today only manual `inspector_cleanup`)

### Dashboard Search / Comparison (PRD §12)
- **DASH-01**: Full-text search across event metadata
- **DASH-02**: Compare two traces side-by-side

### Plugin Entry Points (PRD §17)
- **PLUG-01**: `setuptools` `entry_points` group `django_inspector.watchers` so external packages can register watchers without modifying the inspector

### Storage Abstraction (PRD §13)
- **STOR-01**: `AbstractStorageBackend` introduced when a second backend (Redis / file / object store) is actually added

## Out of Scope

Explicitly excluded from the planned milestones (v1.1–v1.3). Documented to prevent scope creep mid-milestone.

| Feature | Reason |
|---------|--------|
| Scheduler / Redis watchers (PRD Phase 3) | Not planned yet; Celery and HTTP-client watchers are v1.3 phases 14–15 |
| DRF / Channels integrations (PRD Phase 4) | Deferred to v1.4 milestone (a read-only `Event` admin is SCHEMA-05) |
| OpenTelemetry exporter, distributed tracing | Explicit PRD §5 non-goal for early versions; Celery tasks get *linked* traces (CELERY-02), not one trace across processes |
| CPU flame graphs, memory profiling | Explicit PRD §5 non-goal |
| Frontend / browser RUM | Explicit PRD §5 non-goal |
| Kubernetes / infra metrics | Explicit PRD §5 non-goal |
| Non-ORM storage backends | No second backend in scope; introducing the abstraction now is premature |
| New runtime dependencies | Package must stay leaf — Django only |
| Jinja2 template instrumentation | DTL only for v1.1; Jinja2 deferred |
| Distributed sampling decision propagation (sampling across services) | Cross-service is v2.x; linked Celery traces are sampled on their own |
| Spans (`span_id`, `parent_span_id`) | Nothing planned needs them; they can come later as an additive migration |
| Model and Middleware watchers, trace comparison, full-text search, plugins, background writes | Deferred by the 2026-09-30 discovery (§4) |

## Traceability

Update the status when a spec for that phase ships. Specs and plans in `docs/superpowers/` cite these IDs.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ASYNC-01, ASYNC-02 | Phase 4 (Safety & Hardening) | Done |
| MASK-01..06 | Phase 4 (Safety & Hardening) | Done — MASK-04/05 completed after Phase 7 |
| SAMP-01..05 | Phase 4 (Safety & Hardening) | Done |
| AUTH-01..05 | Phase 4 (Safety & Hardening) | Done |
| IGN-01..04 | Phase 4 (Safety & Hardening) | Done — IGN-04 documented in Phase 7 |
| OBS-01..03 | Phase 4 (Safety & Hardening) | Done |
| CACHE-01..07 | Phase 5 (Cache & Template Watchers) | Done |
| TMPL-01..05 | Phase 5 (Cache & Template Watchers) | Done |
| SIGL-01..06 | Phase 6 (Signal & Logging Watchers) | Done |
| LOG-01..06 | Phase 6 (Signal & Logging Watchers) | Done |
| ASYNC-03 | Phase 7 (Async / ASGI verification) | Done |
| SCHEMA-01..06 | Phase 8 (Event Schema v2) | Planned |
| TRACE-01..07 | Phase 9 (Trace API) | Planned |
| CMD-01..04 | Phase 10 (Management Command Watcher) | Planned |
| TEST-01..04 | Phase 11 (Query Guard for Tests) | Planned |
| DASH-03, DASH-04 | Phase 12 (Issues Page & CSV Export) | Planned |
| MAIL-01..03 | Phase 13 (Email Watcher) | Planned |
| HTTP-01..03 | Phase 14 (HTTP Client Watcher) | Planned |
| CELERY-01..05 | Phase 15 (Celery Watcher) | Planned |

**Coverage:**
- v1.1 requirements: **53** total (Cache 7 + Template 5 + Signal 6 + Logging 6 + Mask 6 + Samp 5 + Auth 5 + Async 3 + Ignore 4 + Obs 3 + ASYNC-03 already counted)
- Mapped to phases: **53**
- Unmapped: **0** ✓
- v1.2/v1.3 requirements: **34** (Schema 6 + Trace 7 + Cmd 4 + Test 4 + Dash 2 + Mail 3 + HTTP 3 + Celery 5), all mapped to phases 8–15

---
*Requirements defined: 2026-05-23*
*Last updated: 2026-09-30 — v1.2/v1.3 requirements added from the next-features discovery*
