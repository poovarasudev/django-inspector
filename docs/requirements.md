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

- [ ] **SIGL-01**: Each signal dispatch is captured with signal name (dotted path), sender (class/model name), and number of receivers
- [ ] **SIGL-02**: Per-receiver execution time is captured (start → finish), including failures
- [ ] **SIGL-03**: Receiver ordering is preserved in event metadata (matches dispatch order)
- [ ] **SIGL-04**: Signal events are correlated to the originating request via `trace_id` when dispatched during a request
- [ ] **SIGL-05**: Inspector's own signal handlers (e.g., `got_request_exception` in exception watcher) are excluded from capture to prevent self-feeding
- [ ] **SIGL-06**: User can configure which signals to watch via `SIGNAL_WATCH_LIST` (default: opt-in subset, not "all signals")

### Logging Watcher (PRD §11.4)

- [ ] **LOG-01**: A logging handler installed at startup captures every record routed through Python `logging` at configured level threshold or above
- [ ] **LOG-02**: Each log event captures logger name, level (numeric + name), message, file, line, and traceback (if exc_info present)
- [ ] **LOG-03**: Log events are correlated to the originating request via `trace_id` when emitted during a request
- [ ] **LOG-04**: The inspector's own `django_inspector` logger output is excluded from capture (no self-feeding)
- [ ] **LOG-05**: Configurable level threshold (`LOG_LEVEL_THRESHOLD` setting; default `WARNING` to avoid flooding)
- [ ] **LOG-06**: Logging watcher uninstalls cleanly on `disable()`; idempotent

### Sensitive-Data Masking (PRD §14)

- [x] **MASK-01**: A configurable `SENSITIVE_KEYS` list redacts matching keys (case-insensitive) in request headers, request body, response headers, response body, cookies, query params, and exception locals
- [x] **MASK-02**: Default `SENSITIVE_KEYS` includes: `password`, `passwd`, `token`, `secret`, `authorization`, `cookie`, `set-cookie`, `csrfmiddlewaretoken`, `api_key`, `api-key`, `x-api-key`, `access_token`, `refresh_token`, `session`, `sessionid`
- [x] **MASK-03**: Redaction replaces values with `"***REDACTED***"`; keys remain visible
- [ ] **MASK-04**: A regex-based value matcher catches credit-card-shaped (Luhn-valid 13–19 digits) and JWT-shaped strings regardless of key
  - ⚠ Partial (Phase 4): Card matcher is shape-only; Luhn validation not implemented.
- [ ] **MASK-05**: Masking is applied **before** `buffer_event` so unmasked data never reaches the DB
  - ⚠ Partial (Phase 4): If `mask_metadata` raises, `BaseWatcher.record()` falls back to buffering the **unmasked** event.
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
- [ ] **ASYNC-03**: All v1.1 watchers (Cache, Template, Signal, Logging) are tested under ASGI middleware as well as WSGI

### Ignore Lists

- [x] **IGN-01**: `IGNORE_PATHS` setting (list of regex strings, default `[]`) — matched against `request.path`; matching requests are skipped entirely (no trace id, no events)
- [x] **IGN-02**: `IGNORE_EXCEPTIONS` setting (list of dotted-path exception class names) — matching exceptions are not recorded by the exception watcher
- [x] **IGN-03**: Both lists are checked once per request (compiled regex cached at first use)
- [ ] **IGN-04**: Documented examples for common ignores: `/healthz`, `/metrics`, `django.http.Http404`
  - ⚠ Partial (Phase 4): Examples not yet documented — lands with the README in Phase 7.

### Quiet-by-Default Logging

- [x] **OBS-01**: Every `try/except Exception: pass` block in the package is replaced with `logger.warning("inspector: ...", exc_info=True)` (or `logger.debug` where appropriate)
- [x] **OBS-02**: A new `INSPECTOR_RAISE_ERRORS` setting (default `False`) lets developers opt into raising inspector errors during local development
- [x] **OBS-03**: Existing "never break the host request" guarantee is preserved when `INSPECTOR_RAISE_ERRORS=False`

## v2 (Deferred) Requirements

Acknowledged but not in the v1.1 roadmap. Tracked here so they aren't forgotten.

### Model Watcher (PRD §11.8)
- **MODEL-01**: Capture create/update/delete on tracked models with old/new field values, bulk operations
- **MODEL-02**: Configurable per-model opt-in to avoid recording every save

### Email Watcher (PRD §11.9)
- **MAIL-01**: Capture recipients, subject, HTML/text body, backend, attachments
- **MAIL-02**: `.eml` export

### Management Command Watcher (PRD §11.10)
- **CMD-01**: Capture command name, args, options, stdout, stderr, duration, exit code

### Middleware Watcher (PRD §11.11)
- **MW-01**: Capture middleware order, per-middleware timing, header mutations

### Async Persistence (PRD §15)
- **PERSIST-01**: Optional thread-pool or Celery offload of `bulk_create` behind a setting

### Retention Policies (PRD §15)
- **RET-01**: Age-based, count-based, and size-based retention policies enforced automatically (today only manual `inspector_cleanup`)

### Dashboard Search / Comparison / Export (PRD §12)
- **DASH-01**: Full-text search across event metadata
- **DASH-02**: Compare two traces side-by-side
- **DASH-03**: JSON and CSV export of a trace

### Plugin Entry Points (PRD §17)
- **PLUG-01**: `setuptools` `entry_points` group `django_inspector.watchers` so external packages can register watchers without modifying the inspector

### Storage Abstraction (PRD §13)
- **STOR-01**: `AbstractStorageBackend` introduced when a second backend (Redis / file / object store) is actually added

## Out of Scope

Explicitly excluded from v1.1. Documented to prevent scope creep mid-milestone.

| Feature | Reason |
|---------|--------|
| Celery / scheduler / HTTP-client / Redis watchers (PRD Phase 3) | Deferred to v1.3 milestone — too much surface for v1.1 |
| DRF / Channels / Admin integrations (PRD Phase 4) | Deferred to v1.4 milestone |
| OpenTelemetry exporter, distributed tracing | Explicit PRD §5 non-goal for early versions |
| CPU flame graphs, memory profiling | Explicit PRD §5 non-goal |
| Frontend / browser RUM | Explicit PRD §5 non-goal |
| Kubernetes / infra metrics | Explicit PRD §5 non-goal |
| Non-ORM storage backends | No second backend in scope; introducing the abstraction now is premature |
| Denormalized event columns (`method`, `status_code`) | Deferred to v1.2; JSON filters are adequate at v1.1 volumes |
| New runtime dependencies | Package must stay leaf — Django only |
| Jinja2 template instrumentation | DTL only for v1.1; Jinja2 deferred |
| Distributed sampling decision propagation (sampling across services) | Single-service v1.1; cross-service is v2.x |

## Traceability

Update the status when a spec for that phase ships. Specs and plans in `docs/superpowers/` cite these IDs.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ASYNC-01, ASYNC-02 | Phase 4 (Safety & Hardening) | Done |
| MASK-01..06 | Phase 4 (Safety & Hardening) | Done — MASK-04, MASK-05 partial |
| SAMP-01..05 | Phase 4 (Safety & Hardening) | Done |
| AUTH-01..05 | Phase 4 (Safety & Hardening) | Done |
| IGN-01..04 | Phase 4 (Safety & Hardening) | Done — IGN-04 partial |
| OBS-01..03 | Phase 4 (Safety & Hardening) | Done |
| CACHE-01..07 | Phase 5 (Cache & Template Watchers) | Done |
| TMPL-01..05 | Phase 5 (Cache & Template Watchers) | Done |
| SIGL-01..06 | Phase 6 (Signal & Logging Watchers) | Pending |
| LOG-01..06 | Phase 6 (Signal & Logging Watchers) | Pending |
| ASYNC-03 | Phase 7 (Async / ASGI verification) | Pending |

**Coverage:**
- v1.1 requirements: **53** total (Cache 7 + Template 5 + Signal 6 + Logging 6 + Mask 6 + Samp 5 + Auth 5 + Async 3 + Ignore 4 + Obs 3 + ASYNC-03 already counted)
- Mapped to phases: **53**
- Unmapped: **0** ✓

---
*Requirements defined: 2026-05-23*
*Last updated: 2026-09-30 — Phase 4 statuses reconciled against the code during the GSD → Superpowers migration*
