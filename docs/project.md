# django-inspector

## What This Is

`django-inspector` is a unified runtime observability, debugging, and tracing package for Django applications — Django's answer to Laravel Telescope. It captures requests, SQL queries, exceptions, signals, cache operations, and template renders into trace-correlated events, and surfaces them through a built-in HTMX dashboard for developers, QA, and ops.

## Core Value

**One dashboard, one trace, complete runtime visibility** — every request's full story (HTTP → SQL → exceptions → background work) is reconstructable from a single trace id.

## Requirements

### Validated

<!-- Shipped — confirmed by passing tests. -->

- ✓ Trace propagation via async-safe `ContextVar` — v1.0 (Phase 1)
- ✓ `InspectorMiddleware` lifecycle (trace id → notify watchers → flush buffer) — v1.0 (Phase 1)
- ✓ `BaseWatcher` ABC + `register()` registry with self-registration — v1.0 (Phase 1)
- ✓ `Event` model `(trace_id, event_type, timestamp, JSON metadata)` + bulk_create flush — v1.0 (Phase 1)
- ✓ **Request watcher** — method, path, URL, query, headers, body, status, latency, user, session, IP — v1.0 (Phase 2)
- ✓ **SQL watcher** — SQL, params, timing, alias, origin, slow flag, N+1 + duplicate detection via `execute_wrappers` — v1.0 (Phase 2)
- ✓ **Exception watcher** — type, message, stack, locals, chained `__cause__`/`__context__` via `got_request_exception` — v1.0 (Phase 2)
- ✓ Dashboard: live feed, requests/queries/exceptions list+detail, filters, HTMX partials, per-trace timeline + waterfall — v1.0 (Phase 3)
- ✓ `inspector_cleanup --hours N --dry-run` management command — v1.0 (Phase 3)
- ✓ **Sensitive-data masking** — redact passwords, tokens, cookies, Authorization, CSRF, credit-card-shaped strings; configurable `SENSITIVE_KEYS` (PRD §14) — v1.1 (Phase 4)
- ✓ **Sampling** — `SAMPLING_RATE` + always-on for errors / slow requests (PRD §15) — v1.1 (Phase 4)
- ✓ **Dashboard access control** — staff-only by default, optional IP allowlist (PRD §14) — v1.1 (Phase 4)
- ✓ **Async-safe buffers** — replace `threading.local` with `ContextVar` in SQL log + event buffer — v1.1 (Phase 4)
- ✓ **`IGNORE_PATHS` / `IGNORE_EXCEPTIONS`** — config-driven filtering at watcher entry — v1.1 (Phase 4)
- ✓ **Quiet-by-default logging** — replace silent `except Exception: pass` with `logger.warning(..., exc_info=True)` everywhere — v1.1 (Phase 4)
- ✓ **Cache watcher** — get/set/add/delete/clear + `*_many`, key, TTL, backend, alias, hit/miss, value size; off by default (PRD §11.5) — v1.1 (Phase 5)
- ✓ **Template watcher** — render tree (extends/include), render time, context size, source path; off by default (PRD §11.6) — v1.1 (Phase 5)
- ✓ Dashboard: Cache and Templates pages; request detail shows cache/template events — v1.1 (Phase 5)
- ✓ **Signal watcher** — signal, sender, receivers in call order with per-receiver timing and errors; `SIGNAL_WATCH_LIST`; off by default (PRD §11.7) — v1.1 (Phase 6)
- ✓ **Logging watcher** — logger, level, message, file, line, traceback; `LOG_LEVEL_THRESHOLD` (default WARNING); on by default (PRD §11.4) — v1.1 (Phase 6)
- ✓ Dashboard: Logs and Signals pages; request detail shows log/signal events — v1.1 (Phase 6)
- ✓ **Native ASGI** — async-capable middleware; every v1.1 watcher verified under concurrent ASGI requests and WSGI (ASYNC-03) — v1.1 (Phase 7)
- ✓ **Releasable package** — Python 3.8+ imports fixed, templates packaged, MIT license, version 0.2.0, README, CHANGELOG, CI — v1.1 (Phase 7)

### Active


<!-- None: v1.1 is complete. Next: tag and publish 0.2.0, then break down v1.2 in docs/roadmap.md. -->

### Out of Scope

<!-- Explicitly deferred — kept here so we don't re-add them by accident. -->

- **Phase 2 PRD watchers** (Model, Email, Management Command, Middleware) — deferred to v1.2 / v0.3 milestone.
- **Phase 3 PRD watchers** (Celery, Scheduler, HTTP Client, Redis) — deferred to v1.3 / v0.5.
- **DRF / Channels / Admin integrations** — deferred to v1.4 / v1.0 PRD label.
- **OpenTelemetry / distributed tracing / cross-service correlation** — deferred to v2.0 (PRD §5 non-goals).
- **Plugin entry points** (`setuptools` `entry_points` group) — deferred; internal `register()` covers in-tree watchers for now.
- **Non-ORM storage backends** (Redis, file, object) — `AbstractStorageBackend` not introduced until a second backend is actually needed (premature abstraction).
- **Async write offload** (thread pool / Celery handoff) — deferred to v1.2; synchronous bulk_create is acceptable while sampling caps load.
- **Denormalized hot-path columns** (`method`, `status_code` as real columns) — deferred to v1.2; JSON path filters are good enough for SQLite/PostgreSQL workloads at v1.1 volume.
- **CPU flame graphs / memory profiling / RUM / frontend instrumentation** — out of scope per PRD §5.

## Context

### Current state (brownfield)

This is **not** a greenfield project. v1.0 MVP was completed and tagged on 2026-05-03 across three phases; v1.1 Phase 4 landed on 2026-05-23:

- **Phase 1 — Foundation** (3 plans, commits `64c19ca`, `cbd5cf9`, `176ad8f`): pyproject, AppConfig, settings, registry, tracing ContextVar, middleware, BaseWatcher, Event model, storage flush, migrations. 20 tests passing.
- **Phase 2 — Watchers** (commit `22aaf7a`): Request, SQL, Exception watchers landed together. Net +1,918 lines.
- **Phase 3 — Dashboard + ops** (3 plans, commits `75af996`, `2b58322`, `8b90e3c`): dashboard foundation, `inspector_cleanup`, dashboard pages with HTMX.
- **Phase 4 — Safety & Hardening** (3 plans, commits `d4dc5db`, `ed5daa5`, `8b483ac`): masking, sampling, dashboard auth, ignore lists, ContextVar buffers, loud-by-default logging. 128 tests passing.
- **Phase 5 — Cache & Template Watchers** (branch `feat/phase-5-cache-template-watchers`, commits `2ff720f`, `4ef85e6`, `6a7b81a`, `95de13f`, `8012502`, `52ff915`, `6594587`): cache and template watchers, their dashboard pages, and a fix so dashboard requests are no longer traced. 205 tests passing.
- **Phase 6 — Signal & Logging Watchers** (branch `feat/phase-6-signal-logging-watchers`, commits `0af7e47`, `ffc85fd`, `091f2dd`, `9615f6c`): signal and logging watchers and their dashboard pages. 261 tests passing.
- **Phase 7 — ASGI Verification & Release Prep** (branch `feat/phase-7-asgi-release-prep`, commits `845a8f6`, `1dba327`, `ed7213e`, `78e0ed5`, `d48fef2`): async-capable middleware and ASGI end-to-end tests; fixed the Python 3.8/3.9 import crash and the missing templates in the wheel; MIT license, version 0.2.0, README, CHANGELOG, CI. 275 tests passing (273 + 2 skipped on Django 4.2).

Full brownfield map lives under `docs/architecture/` (STACK, ARCHITECTURE, STRUCTURE, INTEGRATIONS, CONVENTIONS, TESTING, CONCERNS).

### Technical environment

- Python ≥ 3.8, Django ≥ 4.0, sole runtime dep. `uv` + `venv` for tooling. `pytest` + `pytest-django` for tests. 65 tests passing as of v1.0.
- Server-rendered Django templates + HTMX in the dashboard. No JS build step.
- SQLite (tests), PostgreSQL/MySQL targeted for prod.

### Why v1.1 next

PRD §11 Phase 1 lists 7 watchers; v1.0 shipped 3 of them. Closing the Phase-1 set (Cache/Template/Signal/Logging) is the natural next step. We bundle production-safety items (masking, sampling, dashboard auth, async-safe buffers, ignore lists) in the same milestone because the existing watchers already capture sensitive data in the clear and the dashboard is publicly reachable — running v1.0 in production is currently unsafe.

## Constraints

- **Tech stack**: Python ≥ 3.8, Django ≥ 4.0. No new runtime dependencies in v1.1 — the package must stay leaf.
- **Backward compatibility**: existing `DJANGO_INSPECTOR` settings keys and the `Event` model schema are public. New keys are additive; schema additions must ship migrations and default to non-destructive.
- **Performance budget**: middleware overhead per request stays under ~2 ms p50 with all v1.1 watchers enabled at default sampling.
- **Security**: no v1.1 release goes out without masking + dashboard auth.
- **Async-readiness**: every new watcher must work under both WSGI and ASGI; no `threading.local` for per-trace state.
- **Test discipline**: every new watcher ships with a `tests/test_<name>_watcher.py` covering lifecycle, capture, trace correlation, no-trace, edge cases (see `TESTING.md`).
- **Single named logger**: `logging.getLogger("django_inspector")` only. No print, no ad-hoc loggers.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Bundle production-safety into v1.1 alongside new watchers | Existing watchers already capture sensitive data; shipping more watchers before masking/auth would increase blast radius | ✓ Done (Phase 4) |
| Stay on ORM-only storage; do not introduce `AbstractStorageBackend` yet | YAGNI — no second backend in scope. Premature abstraction costs more than it saves | — Pending |
| Keep synchronous bulk_create flush; defer async offload | Sampling will cap volume well below where flush latency matters; async offload is non-trivial to do safely | — Pending |
| Use `ContextVar` everywhere for per-trace state (not just trace id) | Sync-only today, ASGI tomorrow. Cheaper to do it once now than migrate twice | ✓ Done (Phase 4) |
| Log instead of swallow inspector errors | Operators need to know when the inspector is misbehaving; "never break the host request" stays but becomes loud-by-default | ✓ Done (Phase 4) |
| License: MIT | `pyproject.toml` already said MIT; the Apache-2.0 `LICENSE` file was replaced | ✓ Done (Phase 7) |
| Release v1.1 as package version 0.2.0 | Stay pre-1.0 while settings and the `Event` schema may still change; "v1.1" remains the milestone name | ✓ Done (Phase 7) |
| Plan and execute with the Superpowers workflow (brainstorm → spec → plan → subagent-driven execution) | Replaced the earlier GSD `.planning/` flow on 2026-09-30; specs and plans live in `docs/superpowers/` | ✓ Adopted |

## Evolution

Update this document when a spec ships or scope changes:

1. Requirements validated? → Move to Validated with the phase reference.
2. Requirements invalidated? → Move to Out of Scope with the reason.
3. New requirements emerged? → Add to Active and to `docs/requirements.md`.
4. Decisions to log? → Add to Key Decisions.

---
*Last updated: 2026-09-30 — Phase 7 complete; v1.1 milestone done (package 0.2.0)*
