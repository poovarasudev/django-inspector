# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/). Requirement IDs refer to `docs/requirements.md`.

## [Unreleased]

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
