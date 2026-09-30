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
  - Values shaped like card numbers or JWTs are redacted, whatever their key. (MASK-01..03, MASK-06; MASK-04/05 partial, see below)
- **Sampling:**
  - `SAMPLING_RATE` controls what fraction of successful requests is kept.
  - 5xx responses and requests at or above `SLOW_REQUEST_THRESHOLD_MS` are always kept. (SAMP-01..05)
- **Dashboard access control:**
  - Staff-only by default.
  - `INSPECTOR_DASHBOARD_PERMISSION` (a callable) and `INSPECTOR_DASHBOARD_IP_ALLOWLIST` tighten or replace the check.
  - A startup warning appears in production when only the default staff check is in place. (AUTH-01..05)
- **Ignore lists:** `IGNORE_PATHS` (regexes; matching requests aren't traced at all) and `IGNORE_EXCEPTIONS`, with the common cases (`/healthz`, `/metrics`, `Http404`) shown in the README. (IGN-01..04)
- **`INSPECTOR_RAISE_ERRORS`** re-raises the inspector's own internal errors, for development and tests. (OBS-02)
- **Dashboard:**
  - New Cache, Templates, Logs and Signals list and detail pages, with filters.
  - The request detail page shows the new events in its summary, timeline, waterfall and tabs.
  - The sidebar shows the installed version.
- **Native ASGI support.** `InspectorMiddleware` handles both sync and async requests. In async mode, database writes and the lazy `request.user` stay in a sync context, and concurrent requests never share trace state. (ASYNC-03)
- A `README.md` covering installation, settings, dashboard access and production notes, plus this changelog.
- CI on GitHub Actions: tests on Python 3.8–3.13 with Django 4.2 and 5.2, plus a build job that checks the wheel and sdist contents.

### Changed

- Per-request state (the event buffer and the SQL query log) moved from `threading.local` to `ContextVar`, so concurrent async requests stay isolated. (ASYNC-01, ASYNC-02)
- The inspector's own errors are logged to the `django_inspector` logger at WARNING instead of being silently swallowed. (OBS-01, OBS-03)
- **License is now MIT.** The `LICENSE` file previously contained Apache-2.0 text, while the package metadata said MIT.
- The version is defined once, in `django_inspector.__version__`.

### Fixed

- **Python 3.8 and 3.9 support.** Three `X | None` type hints made the app crash at startup (`TypeError` in `apps.ready()`) on Python below 3.10.
- **The dashboard works after `pip install`.** The wheel didn't include the dashboard templates, so every page raised `TemplateDoesNotExist`.
- **Events could be lost under concurrent ASGI requests.** Each request now gets its own event buffer and SQL query log when it starts. Before, requests whose context already held a buffer shared one list, so one request's flush could clear another request's events before they were written. The shared query log could also mix up N+1 detection between requests. (ASYNC-01, ASYNC-02)
- **Dashboard requests are no longer traced.** Previously, their auth and session SQL was stored as events that belonged to no request, and the live feed's polling kept adding more.

### Known gaps

- MASK-05: if masking itself raises, the event is still recorded **unmasked** (with a warning logged). It will fail closed in a later release.
- MASK-04: card-number detection matches the shape of the number but doesn't run a Luhn check.
- Event timestamps are set when events are written at the end of the request, so waterfall offsets within a request aren't meaningful yet.

## [0.1.0] - 2026-05-03

The first release (the "v1.0" MVP).

### Added

- Trace propagation through a `ContextVar` trace id, and `InspectorMiddleware`.
- Watcher framework (`BaseWatcher`, registry) and the `Event` model, with end-of-request `bulk_create`.
- Request, SQL (with N+1 and duplicate detection) and Exception watchers.
- HTMX dashboard: live feed; requests, queries and exceptions list and detail pages; a per-trace timeline and waterfall.
- `inspector_cleanup` management command.
