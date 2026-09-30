# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/). Requirement IDs refer to `docs/requirements.md`.

## [Unreleased]

## [0.3.0] - Unreleased

The "v1.2 Phase 8" milestone: real `Event` columns for the fields later phases filter and group on.

### Added

- **Event schema v2** (migration `0003`, `0001`/`0002` untouched; schema-only, no backfill):
  - New columns `method`, `status_code`, `path` and `duration_ms`, derived at flush time from each event's already-masked metadata by per-event-type extractors in `storage/columns.py`. Values that fail coercion (wrong type, out of range, non-finite) are dropped, leaving the column NULL; a failing extractor logs a warning and still stores the event with NULL columns (or raises under `INSPECTOR_RAISE_ERRORS`). (SCHEMA-01)
  - A `fingerprint` column, indexed with `event_type`, groups like events: SQL events by normalised query shape and database alias, exceptions by type plus the innermost application frame's file and function (no line number, so it survives unrelated edits). (SCHEMA-02)
  - A `parent_trace_id` column to link a trace to the trace that started it; only a column in this phase — a later phase fills it. (SCHEMA-03)
  - A `pinned` flag, set by a staff-only dashboard action, keeps a trace's events out of `RETENTION_HOURS` pruning and `inspector_cleanup` (including its `--dry-run` count). (SCHEMA-04)
- **Pin a trace** from its request detail page: a "Pin trace"/"Unpin trace" button and a "Pinned" badge, also shown in the requests list. `POST trace/<trace_id>/pin/` updates every event of the trace, protected by `csrf_protect` (so CSRF is enforced even without `CsrfViewMiddleware`) and `require_POST`. (SCHEMA-04)
- **Read-only Django admin** for `Event`: list, filter by type and pinned, search by trace id and fingerprint; add, change and delete are all denied. Only loaded when the host has `django.contrib.admin` installed. (SCHEMA-05)
- The requests-list `method`, status-class and `path` filters now query the real columns instead of JSON metadata lookups. (SCHEMA-06)
- **A public `trace()`**, exported as `django_inspector.trace`: starts a trace from any code as `with`, `async with`, or a decorator on a sync or async function. Records SQL, log and exception events into one trace, the same as a request. A `trace()` opened while a trace is already active joins it instead of starting a new one — no extra flush, no extra anchor event. Sampled like a request: kept when it fails or runs at or above `SLOW_REQUEST_THRESHOLD_MS`, otherwise at `SAMPLING_RATE`. Its exception is recorded (through the exception watcher's new public `capture()`) and then re-raised unchanged; an inspector failure while finishing up is logged and swallowed (or raised under `INSPECTOR_RAISE_ERRORS`) and never replaces the user's exception. Importing `django_inspector` no longer imports the storage models — every storage/watcher import in `trace()` is deferred to call time. (TRACE-01, TRACE-03, TRACE-04)
- `InspectorMiddleware` is rebuilt on the same `TraceScope` lifecycle `trace()` uses (`tracing/scope.py`), with no behaviour change: the existing middleware, sampling, ASGI and async-isolation test suites pass unchanged. `sampling.should_keep(failed, duration_ms, early_pick)` is the sampling decision itself, shared by requests and `trace()`; `compute_sampling_decision()` is now a thin wrapper over it. (TRACE-02)
- **`X-Inspector-Trace` response header** on a kept trace, controlled by the new `TRACE_HEADER` setting (`"debug"` default: only when `DEBUG` is on; `True` always; `False` never). An unkept trace never gets it. (TRACE-05)
- **`django_inspector.tracing.TraceIdFilter`**, a `logging.Filter` that sets `record.inspector_trace_id` to the current trace id (or `"-"` outside one), for wiring trace ids into your own log format. (TRACE-06)
- **Traces page**: `/trace/<id>/` now renders any trace, not only a request's — a request trace still redirects to its request page, and a `trace()` run gets its own page (timeline, waterfall, pin button, JSON export) anchored on its `trace.completed` event. `/traces/` lists non-request traces with name/status/date filters. (CMD-04)
- **Command watcher**, on by default and opt-in per command via the new `COMMAND_WATCH_LIST` setting (empty by default): traces a listed management command's run — `manage.py`, `django-admin` and `call_command()` alike, by patching `BaseCommand.execute` on the class — with a `command.completed` anchor (name, args, options, the first 8192 characters of stdout and stderr, duration, exit code) plus its SQL, log and exception events, exactly like a request. `runserver`, `runserver_plus`, `testserver`, `test`, `shell`, `shell_plus`, `dbshell`, `migrate` and `inspector_cleanup` are never traced even when listed (system check `django_inspector.W009` warns if one is). A command run inside an already-active trace (another `trace()`, a request, or another traced command) joins it instead of starting its own, recording its anchor there without an early flush. Captured args, options and output are masked the same way any other event is; the command's real stdout/stderr still receive everything, and its exception always propagates unchanged. (CMD-01, CMD-02)
- **Chunked flush for long commands**: a traced command's event buffer flushes every 500 events instead of holding everything in memory until the end, so memory stays flat and a killed command keeps whatever it already flushed (`storage/flush.py`'s `_EventBuffer` gains `chunk_size`/`written`/`force_keep`; `MAX_EVENTS_PER_TRACE` now counts the whole trace, not just the current chunk). `watchers/sql.py`'s `_QueryStats` stops tracking new query shapes (and, separately, new exact queries) past `MAX_TRACKED_QUERIES` (10,000, each), so a command running a huge batch doesn't grow its N+1/duplicate-detection state without bound. (CMD-03)
- `tracing/api.py`'s `_Run` is generalised behind the same `trace()` defaults: an `anchor_type`, a `describe(exc_type, exc_value) -> (failed, extra)` hook, and `record_when_joined`/`chunk_size`, so the command watcher shares `trace()`'s one lifecycle instead of a second one. A trace anchor now also carries `events_dropped` past `MAX_EVENTS_PER_TRACE`, like the request anchor. (TRACE-02)
- **`django_inspector.testing.assert_queries()`**: a context manager, function/async-function decorator and `TestCase` class decorator that fails a test breaking a query budget (`max`, `no_n_plus_one`, `no_duplicates`, `using`). It installs Django's `connection.execute_wrapper()` directly on the guarded aliases — no trace, middleware, buffer or database write — so it works whether or not the inspector's watchers are enabled. Detection is shared with the SQL watcher (`normalize_sql` shapes, the "3+ = N+1" rule, exact `(sql, params)` duplicates); savepoint statements and the inspector's own queries are ignored. A failing budget raises `QueryBudgetExceeded` (an `AssertionError`) naming each offending shape, its count and up to three `file:line (function)` origins — bound parameters are never printed, though `guard.queries` (a list of `CapturedQuery`) carries them. (TEST-01, TEST-02)
- **pytest plugin**, registered through a `pytest11` entry point so it only ever loads under pytest: an `assert_queries` fixture (the same factory) and an `@pytest.mark.inspector_queries(...)` marker, implemented as a `pytest_runtest_call` hookwrapper. The plugin module imports nothing from Django or the rest of django_inspector at import time, since pytest loads plugins before settings are configured. (TEST-03)
- **`N_PLUS_ONE` dev mode** (`"off"` default, `"warn"` or `"raise"`): the SQL watcher reports a query shape the moment it hits its third run in a trace, while `settings.DEBUG` is on. `"warn"` logs once per shape; `"raise"` raises `NPlusOneDetected` outside the watcher's usual catch-and-log handling — the query has already run by then. Off by default, and its only added cost when off is one string comparison per query. (TEST-04)

### Changed

- `watchers.utils._is_library_file` is now the public `is_library_file` (the old name is kept as an alias). `watchers.sql._normalize_sql` now re-exports `storage.columns.normalize_sql`.
- `SLOW_REQUEST_THRESHOLD_MS` now applies to every trace, not only requests.

## [0.2.0] - Unreleased

The "v1.1" milestone: finishes the PRD Phase-1 watcher set and makes the package safe to run in production.

### Added

- **Cache watcher**, off by default:
  - Records `get`/`set`/`add`/`delete`/`clear` and `get_many`/`set_many`/`delete_many`.
  - For each call: key, alias, backend, hit/miss, TTL, value size (never the value), where in your code the call came from, and errors.
  - Covers sync and async cache calls. (CACHE-01..07)
- **Template watcher**, off by default:
  - Records each Django template render: name, source path, loader, render time, and context key count and names (never values).
  - Records the extends/include tree through `render_id`, `parent_id`, `depth` and `relation`. (TMPL-01..05)
- **Signal watcher**, off by default:
  - Records dispatches of the signals in the new `SIGNAL_WATCH_LIST` setting (default: the five model signals).
  - For each dispatch: sender, receiver count, and every receiver in call order with its time and any error.
  - Works with `send`, `send_robust`, `asend` and `asend_robust`. The inspector's own receivers are excluded. (SIGL-01..06)
- **Log watcher**, on by default:
  - A root-logger handler records log records at the new `LOG_LEVEL_THRESHOLD` setting (default `WARNING`): logger, level, message, file, line and traceback.
  - The inspector's own logger is excluded. (LOG-01..06)
- **Sensitive-data masking** before storage:
  - Built-in sensitive keys, plus extra ones from `SENSITIVE_KEYS`.
  - Values holding a Luhn-valid card number or a JWT are redacted, whatever their key.
  - Fails closed: if masking raises, a placeholder (`masking_failed`) is stored instead of the data. (MASK-01..06)
- **Sampling:**
  - `SAMPLING_RATE` controls what fraction of successful requests is kept.
  - 5xx responses and requests at or above `SLOW_REQUEST_THRESHOLD_MS` are always kept. (SAMP-01..05)
- **Dashboard access control:**
  - Staff-only by default.
  - `INSPECTOR_DASHBOARD_PERMISSION` (a callable) and `INSPECTOR_DASHBOARD_IP_ALLOWLIST` tighten or replace the check.
  - A system check (`django_inspector.W008`) warns in production when only the default staff check is in place. (AUTH-01..05)
  - `TRUSTED_PROXY_COUNT` sets how many `X-Forwarded-For` hops come from your own proxies; by default the header is not trusted.
- **Ignore lists:** `IGNORE_PATHS` (regexes; matching requests aren't traced at all) and `IGNORE_EXCEPTIONS`, with the common cases (`/healthz`, `/metrics`, `Http404`) shown in the README. (IGN-01..04)
- **`INSPECTOR_RAISE_ERRORS`** re-raises the inspector's own internal errors, for development and tests. (OBS-02)
- **Dashboard:**
  - New Cache, Templates, Logs and Signals list and detail pages, with filters.
  - The request detail page shows the new events in its summary, timeline, waterfall and tabs.
  - The sidebar shows the installed version.
- **Native ASGI support.** `InspectorMiddleware` handles both sync and async requests. In async mode, database writes and the lazy `request.user` stay in a sync context, and concurrent requests never share trace state. (ASYNC-03)
- A `README.md` covering installation, settings, dashboard access and production notes, plus this changelog.
- **Configuration checks.** `manage.py check` validates `DJANGO_INSPECTOR`: unknown keys (with a "did you mean" hint), wrong types and ranges, invalid regexes and IP ranges, unimportable dotted paths, a missing or misplaced middleware, and a `DASHBOARD_URL_PREFIX` that doesn't match the dashboard's mount point (`django_inspector.E001`–`E005`, `W001`–`W008`).
- **Retention.** `RETENTION_HOURS` prunes old events automatically (throttled and batched). `inspector_cleanup` defaults to it, deletes in batches (`--batch-size`) and rejects non-positive `--hours`.
- **Early sampling.** With `EARLY_SAMPLING`, requests are picked when they start; unpicked ones skip SQL, cache, template, signal and log capture, and keep only their request and exception events if they fail or are slow. (SAMP-01..05)
- **Limits.** `MAX_EVENTS_PER_TRACE` (default 1000) caps events per request and records `events_dropped`. SQL text, SQL parameters, exception frames (innermost 50) and chained exceptions are bounded too. `SQL_CAPTURE_PARAMS = False` stores SQL without its parameters.
- **Dashboard:** filter requests by trace id, `/inspector/trace/<id>/` jumps to a trace's request, and "Export JSON" downloads a whole trace.
- CI on GitHub Actions: tests on Python 3.8–3.14 with Django 4.2, 5.2, 6.0 and 6.1, a PostgreSQL job, a coverage gate, ruff and mypy, an overhead benchmark (`scripts/benchmark.py`), and a build job that checks the wheel and sdist contents. A release workflow publishes to PyPI (trusted publishing) when a GitHub release is published.

### Changed

- Per-request state (the event buffer and the SQL query log) moved from `threading.local` to `ContextVar`, so concurrent async requests stay isolated. (ASYNC-01, ASYNC-02)
- The inspector's own errors are logged to the `django_inspector` logger at WARNING instead of being silently swallowed. (OBS-01, OBS-03)
- **License is now MIT.** The `LICENSE` file previously contained Apache-2.0 text, while the package metadata said MIT.
- The version is defined once, in `django_inspector.__version__`.
- **Events keep the time they were captured.** Timestamps were set when the buffer was written, so every event in a request shared its end time. A request event is now stamped with the request's start, and the waterfall places each bar at its real start. Migration `0002` changes the `timestamp` default and adds an `(event_type, -timestamp)` index for the dashboard lists.
- **Lower overhead:** N+1 and duplicate detection is linear (it re-scanned every earlier query, adding about 3.8 s to a 2,000-query request); origin lookup walks frames instead of formatting the whole stack; settings and ignore regexes are cached. p50 overhead with every watcher on fell from 1.14 ms to 0.59 ms on the benchmark view.
- The dashboard's CSS and JavaScript, including a bundled copy of htmx 2.0.4, are served by the dashboard itself instead of loading htmx from unpkg.com.
- The request event records `request_body_format`/`response_body_format`; `stack_trace` no longer repeats the chained tracebacks already in `chained_exceptions`.

### Fixed

- **Python 3.8 and 3.9 support.** Three `X | None` type hints made the app crash at startup (`TypeError` in `apps.ready()`) on Python below 3.10.
- **The dashboard works after `pip install`.** The wheel didn't include the dashboard templates, so every page raised `TemplateDoesNotExist`.
- **Events could be lost under concurrent ASGI requests.** Each request now gets its own event buffer and SQL query log when it starts. Before, requests whose context already held a buffer shared one list, so one request's flush could clear another request's events before they were written. The shared query log could also mix up N+1 detection between requests. (ASYNC-01, ASYNC-02)
- **Masking now fails closed (MASK-05).** Previously, if masking raised (for example because of a bad `SENSITIVE_KEYS` entry), the event was stored **unmasked**. Now a placeholder `{"masking_failed": true, "error_type": ...}` is stored instead, and a warning is logged. With `INSPECTOR_RAISE_ERRORS` on, the error is raised.
- **Card-number masking checks Luhn (MASK-04).** Previously any 13–19 digit run was redacted, so order ids, timestamps and other long numbers were wiped. Now only numbers that pass the Luhn checksum are redacted, including grouped forms (`4111 1111 1111 1111`, `5555-5555-5555-4444`).
- **Dashboard requests are no longer traced.** Previously, their auth and session SQL was stored as events that belonged to no request, and the live feed's polling kept adding more.
- The dashboard no longer returns a 500 for an invalid date filter, and its page links URL-encode filter values.
- An invalid `IGNORE_PATHS` regex no longer disables every pattern; it is skipped and reported.
- A request whose host isn't in `ALLOWED_HOSTS` no longer loses its request event, and watcher modules that fail to import are logged instead of silently skipped.

### Security

- **Request and response bodies are masked.** They were stored as raw strings, so a login POST stored the password in clear. Form and JSON bodies are now parsed and masked before they are stored; `@sensitive_post_parameters` is honoured; multipart bodies are only summarised; binary bodies are stored as a size summary. (MASK-01, REQ-03)
- **The recorded full URL masks sensitive query parameters** (it kept `?token=...` even though `query_params` masked it).
- **The dashboard IP allowlist can no longer be bypassed** with a spoofed `X-Forwarded-For` header; see `TRUSTED_PROXY_COUNT`. The recorded client IP uses the same rule. (AUTH-03, REQ-05)
- **Exception locals are masked before they are turned into text,** `@sensitive_variables` is honoured even when `DEBUG` is on, and unevaluated QuerySets are no longer evaluated (which ran queries while handling the error). (EXC-02)
- `Proxy-Authorization`, `X-Auth-Token`, `X-CSRFToken` and `X-CSRF-Token` are built-in sensitive keys.
- Dashboard responses carry a strict Content-Security-Policy (`script-src 'self'`, `frame-ancestors 'none'`), and the dashboard has no inline scripts or event handlers.

### Known gaps

- Dashboard filters on JSON fields (status, path, method) still scan the event type's rows; moving them to real columns is planned for v1.2.
- SQL parameters are masked by value pattern only, not by column name (use `SQL_CAPTURE_PARAMS = False` if that matters).

## [0.1.0] - 2026-05-03

The first release (the "v1.0" MVP).

### Added

- Trace propagation through a `ContextVar` trace id, and `InspectorMiddleware`.
- Watcher framework (`BaseWatcher`, registry) and the `Event` model, with end-of-request `bulk_create`.
- Request, SQL (with N+1 and duplicate detection) and Exception watchers.
- HTMX dashboard: live feed; requests, queries and exceptions list and detail pages; a per-trace timeline and waterfall.
- `inspector_cleanup` management command.
