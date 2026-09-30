# Roadmap: django-inspector

## Overview

`django-inspector` shipped its v1.0 MVP across three phases (foundation, three watchers, dashboard) tagged at commit `0675c3a`. The next milestone — **v1.1** — closes the remaining PRD Phase-1 watchers (Cache, Template, Signal, Logging) and lands the production-safety primitives (masking, sampling, dashboard auth, async-safe buffers, ignore lists, loud-by-default logging) needed before any sane production rollout. Granularity is **coarse**: each new phase is a coherent ship-able slice.

## Milestones

- ✅ **v1.0 MVP** — Phases 1–3 (shipped 2026-05-03, tag `v1.0`)
- ✅ **v1.1 Phase-1 Completion + Production Safety** — Phases 4–7 (complete 2026-09-30; releases as package version **0.2.0**, tag and PyPI publish pending)
- 🚧 **v1.2 Beyond the Request** — Phases 8–13 (in progress on `feat/v1.2-beyond-the-request`; releases as package version **0.3.0**)
- 📋 **v1.3 Outgoing Calls & Tasks** — Phases 14–15 (HTTP client and Celery watchers)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3, …) are planned milestone work.
- Decimal phases (e.g., 4.1) are urgent insertions and marked `INSERTED`.

<details>
<summary>✅ v1.0 MVP (Phases 1–3) — SHIPPED 2026-05-03</summary>

### Phase 1: Foundation
**Goal**: Package scaffold, tracing core, base watcher framework, event storage.
**Plans**: 3 plans

Plans:
- [x] 01-01: Package scaffold — pyproject, AppConfig, settings, registry, test settings (`64c19ca`)
- [x] 01-02: Tracing core — ContextVar trace_id + InspectorMiddleware (`cbd5cf9`)
- [x] 01-03: BaseWatcher framework, Event model, storage flush, migrations (`176ad8f`)

### Phase 2: Watchers (Request / SQL / Exception)
**Goal**: Ship the three foundational watchers required by PRD Phase 1.
**Plans**: 1 combined plan (`22aaf7a`)

Plans:
- [x] 02-01: Request, SQL, Exception watchers + 45 new tests

### Phase 3: Dashboard
**Goal**: Make captured events visible through a built-in dashboard + provide a manual retention command.
**Plans**: 3 plans

Plans:
- [x] 03-01: Dashboard foundation — URLs, base template, sidebar, pagination, CSS (`75af996`)
- [x] 03-02: Management command — `inspector_cleanup` (`2b58322`)
- [x] 03-03: Dashboard pages — views, list/detail templates, HTMX partials, filters, timeline/waterfall (`8b90e3c`)

</details>

### ✅ v1.1 Phase-1 Completion + Production Safety (Complete — release 0.2.0 pending tag/publish)

**Milestone Goal:** Close PRD §11.4–§11.7 watchers and make `django-inspector` safe to run in production behind a load balancer with real users.

---

<details>
<summary>✅ Phase 4: Safety & Hardening — COMPLETE 2026-05-23 (128 tests)</summary>

#### Phase 4: Safety & Hardening
**Goal**: Land the production-safety primitives BEFORE adding more event sources. Masking, sampling, dashboard auth, async-safe buffers, ignore lists, loud-by-default logging.
**Depends on**: Phase 3 (v1.0)
**Requirements**: MASK-01..06, SAMP-01..05, AUTH-01..05, ASYNC-01, ASYNC-02, IGN-01..04, OBS-01..03
**Success Criteria** (what must be TRUE):
  1. Default `SENSITIVE_KEYS` redacts passwords, tokens, cookies, Authorization in every captured event (request body/headers, exception locals).
  2. Anonymous and non-staff users get 403 on every dashboard URL by default; staff users get 200.
  3. Setting `SAMPLING_RATE=0.1` captures roughly 10% of successful requests, 100% of error requests, and 100% of requests over the slow threshold.
  4. Running two `async def` views concurrently does not cross-contaminate SQL logs or event buffers.
  5. Requests matching `IGNORE_PATHS` produce zero events; exceptions matching `IGNORE_EXCEPTIONS` are not recorded.
  6. Every previously silent `except Exception: pass` now logs at `WARNING` (with `exc_info`), and the host request still succeeds.
**Plans**: 3 plans

Plans:
- [x] 04-01: Sensitive-data masking + sampling (`d4dc5db`)
- [x] 04-02: Dashboard access control + ignore lists (`ed5daa5`)
- [x] 04-03: Async-safe buffers (ContextVar) + loud-by-default logging cleanup (`8b483ac`)

Known gaps carried forward: MASK-04 (no Luhn check), MASK-05 (unmasked fallback on masking error), IGN-04 (docs). All resolved: IGN-04 in Phase 7, MASK-04/05 on `fix/masking-fail-closed-luhn`.

</details>

---

#### Phase 5: Cache & Template Watchers
**Goal**: Implement PRD §11.5 Cache Watcher and §11.6 Template Watcher with masking and sampling already in place.
**Depends on**: Phase 4
**Requirements**: CACHE-01..07, TMPL-01..05
**Success Criteria** (what must be TRUE):
  1. Calling `cache.get(...)`, `cache.set(...)`, `cache.delete(...)`, `cache.clear()` within a request produces correlated `cache.*` events.
  2. `cache.get_many` / `set_many` / `delete_many` are captured as multi-key events.
  3. Cache events expose hit/miss status, key, backend alias, TTL (for `set`), and duration.
  4. Rendering a Django template within a request produces a `template.rendered` event with name, path, duration, and context size.
  5. Nested template includes/extends record parent-child relationships.
  6. Both watchers are async-safe (work under ASGI) and respect `SAMPLING_RATE`.
**Spec**: [2026-09-30-cache-template-watchers-design.md](superpowers/specs/2026-09-30-cache-template-watchers-design.md) · **Plan**: [2026-09-30-cache-template-watchers.md](superpowers/plans/2026-09-30-cache-template-watchers.md)

Work items:
- [x] Cache watcher
- [x] Template watcher
- [x] Cache and Templates dashboard pages + request-detail integration
- [x] Dashboard requests no longer open a trace (orphan-event fix)

---

#### Phase 6: Signal & Logging Watchers
**Goal**: Implement PRD §11.7 Signal Watcher and §11.4 Logging Watcher.
**Depends on**: Phase 5
**Requirements**: SIGL-01..06, LOG-01..06
**Success Criteria** (what must be TRUE):
  1. Dispatching a signal listed in `SIGNAL_WATCH_LIST` during a request produces a `signal.dispatched` event with name, sender, receiver count, and per-receiver timing.
  2. Inspector's own signal handlers do not feed the signal watcher (no self-recursion).
  3. Calling `logging.warning(...)` during a request produces a `log.record` event with logger name, level, message, file, line, and (if present) traceback.
  4. The `django_inspector` logger's own output is excluded from capture.
  5. Configurable level threshold (`LOG_LEVEL_THRESHOLD`) gates which records are captured.
**Spec**: [2026-09-30-signal-logging-watchers-design.md](superpowers/specs/2026-09-30-signal-logging-watchers-design.md) · **Plan**: [2026-09-30-signal-logging-watchers.md](superpowers/plans/2026-09-30-signal-logging-watchers.md)

Work items:
- [x] Signal watcher
- [x] Logging watcher
- [x] Logs and Signals dashboard pages + request-detail integration

---

#### Phase 7: ASGI Verification & v1.1 Release Prep
**Goal**: Prove all v1.1 watchers work under ASGI end-to-end, finalize docs/license, cut v1.1.
**Depends on**: Phase 6
**Requirements**: ASYNC-03 + release hygiene (README, CHANGELOG, license reconciliation)
**Success Criteria** (what must be TRUE):
  1. A small ASGI test app exercising async views + cache + template + signal + log emits correctly correlated events with no cross-trace bleed.
  2. License consistency: `pyproject.toml`, `LICENSE`, and any docs all state the same license.
  3. `README.md` exists and documents install, settings, mounting, and dashboard auth.
  4. `CHANGELOG.md` lists every v1.1 change with the requirement IDs they satisfy.
  5. `python -m build` produces clean sdist + wheel; smoke install in a fresh venv works.
**Spec**: [2026-09-30-asgi-release-prep-design.md](superpowers/specs/2026-09-30-asgi-release-prep-design.md) · **Plan**: [2026-09-30-asgi-release-prep.md](superpowers/plans/2026-09-30-asgi-release-prep.md)

Work items:
- [x] ASGI end-to-end test app + async coverage for v1.1 watchers (and natively async-capable middleware)
- [x] Release hygiene — README, CHANGELOG, license reconciled to MIT, version 0.2.0, build smoke test
- [x] Fixed release blockers found on the way: Python 3.8/3.9 import crash; templates missing from the wheel; events lost under concurrent ASGI requests (per-request buffers)
- [x] CI — GitHub Actions test matrix (Python 3.8–3.13 × Django 4.2/5.2) + distribution check

---

### 🚧 v1.2 Beyond the Request (In progress)

**Milestone Goal:** Trace work that isn't an HTTP request, and turn stored events into answers. Comes from the product discovery in [`discovery/2026-09-30-next-features.md`](discovery/2026-09-30-next-features.md). The Model and Middleware watchers the PRD lists for this stage are deferred (discovery §4): both cost more per request than they give back.

**Decisions taken for the milestone:**
- No spans yet. Nothing planned needs them; `parent_trace_id` links traces instead.
- Celery tasks get their own trace, linked to the request that sent them (a task is sent before the request's keep-or-drop decision, so writing into the request's trace would orphan events).
- Management commands are traced only on opt-in (`COMMAND_WATCH_LIST`).

---

#### Phase 8: Event Schema v2
**Goal**: Give `Event` the real columns later phases group and filter on, before more watchers multiply the rows.
**Depends on**: Phase 7
**Requirements**: SCHEMA-01..06
**Spec**: [2026-09-30-event-schema-v2-design.md](superpowers/specs/2026-09-30-event-schema-v2-design.md)
**Success Criteria** (what must be TRUE):
  1. A new migration adds `method`, `status_code`, `path`, `duration_ms`, `fingerprint`, `parent_trace_id` and `pinned`; `0001`/`0002` are unchanged and old rows still display.
  2. Two SQL events with the same query shape share a fingerprint; so do two exceptions of the same type raised from the same function, even after the line moves.
  3. A pinned trace survives `RETENTION_HOURS` pruning and `inspector_cleanup`.
  4. `Event` shows in the Django admin and cannot be edited there.
  5. Filtering requests by method, status or path uses the columns (no JSON lookup in the query).

Work items:
- [x] Migration and write-time column filling
- [x] SQL and exception fingerprints
- [x] Pinning (model, retention, dashboard toggle)
- [x] Read-only admin
- [x] Dashboard filters on columns

---

#### Phase 9: Trace API
**Goal**: A public way to start a trace anywhere, which the middleware itself uses; plus the ways back from an app to its trace.
**Depends on**: Phase 8
**Requirements**: TRACE-01..07, CMD-04 (moved from Phase 10)
**Spec**: [2026-09-30-trace-api-design.md](superpowers/specs/2026-09-30-trace-api-design.md)
**Success Criteria** (what must be TRUE):
  1. `with django_inspector.trace("backfill"):` and `@django_inspector.trace("job")` record SQL, logs and exceptions into one trace, on sync and async code, with no middleware involved.
  2. The existing middleware test suite passes unchanged on top of the trace API, and the benchmark stays within budget.
  3. Nested `trace()` calls join the outer trace.
  4. A failing traced block is always kept; a successful one is kept at `SAMPLING_RATE`.
  5. With `DEBUG` on, responses carry `X-Inspector-Trace`; with it off, they don't (unless the setting says so); unkept traces never get the header.
  6. Adding the filter to a logging handler makes `%(inspector_trace_id)s` usable in its format.
  7. `/trace/<id>/` renders a `trace()` trace, request traces still open the request page, and a Traces page lists non-request traces.

Work items:
- [ ] `trace()` context manager / decorator and lifecycle extraction
- [ ] Middleware rebuilt on `trace()`
- [ ] `X-Inspector-Trace` header
- [ ] Trace-id logging filter
- [ ] General trace page and Traces list (CMD-04)

---

#### Phase 10: Management Command Watcher
**Goal**: Each chosen management command run becomes a trace with its SQL, logs and exceptions.
**Depends on**: Phase 9
**Requirements**: CMD-01..03 (CMD-04 shipped in Phase 9)
**Success Criteria** (what must be TRUE):
  1. Running a command listed in `COMMAND_WATCH_LIST` produces a trace with a `command.completed` event (name, args, options, bounded stdout/stderr, duration, exit code) and its SQL/log/exception events.
  2. Unlisted commands, and `runserver`, `test`, `shell`, `migrate` and `inspector_cleanup` even when listed, record nothing.
  3. A command running 100k queries keeps flat memory, and killing it midway leaves the already-flushed events in the database.
  4. Command traces appear on the Traces page and render on `/trace/<id>/`.

Work items:
- [ ] Command watcher (hook, allowlist, denylist)
- [ ] Chunked flush and bounded query stats
- [ ] Command traces on the Traces page

---

#### Phase 11: Query Guard for Tests
**Goal**: Catch N+1 and duplicate queries in CI with a message that says where they came from.
**Depends on**: Phase 7 (independent of 8–10)
**Requirements**: TEST-01..04
**Success Criteria** (what must be TRUE):
  1. `with assert_queries(max=5, no_n_plus_one=True):` fails when a block runs an N+1, with no middleware and no events written.
  2. The failure message names each offending query shape, its count, and the file and line it ran from.
  3. The pytest fixture works when pytest is installed and the package imports cleanly when it isn't; the `TestCase` decorator works on methods and classes.
  4. `N_PLUS_ONE="raise"` raises on an N+1 in a `DEBUG` request and does nothing with `DEBUG` off.

Work items:
- [ ] `assert_queries` context manager
- [ ] pytest fixture and `TestCase` decorator
- [ ] `N_PLUS_ONE` development mode

---

#### Phase 12: Issues Page & CSV Export
**Goal**: A landing page that answers "what should I fix first?".
**Depends on**: Phase 8
**Requirements**: DASH-04, DASH-03
**Success Criteria** (what must be TRUE):
  1. The Issues page lists exceptions grouped by fingerprint, query shapes and endpoints with the numbers in DASH-04, each linking to example traces.
  2. The page responds in bounded time at the `CappedPaginator` limits on SQLite and PostgreSQL.
  3. With `SAMPLING_RATE` below 1, counts are labelled as estimates.
  4. Dashboard lists export to CSV, masked like the JSON export.

Work items:
- [ ] Aggregates (exceptions, query shapes, endpoints)
- [ ] Issues page
- [ ] CSV export

---

#### Phase 13: Email Watcher
**Goal**: See every email the app sends, as it would arrive.
**Depends on**: Phase 9
**Requirements**: MAIL-01..03
**Success Criteria** (what must be TRUE):
  1. Sending mail during a trace records a `mail.sent` event with recipients, subject, bodies, backend and attachment names/sizes.
  2. The dashboard previews the HTML body with scripts and remote content blocked, and downloads the message as `.eml`.
  3. Bodies are masked before they are stored.

Work items:
- [ ] Email watcher
- [ ] Mail dashboard page, preview and `.eml` export

---

### 📋 v1.3 Outgoing Calls & Tasks (Planned)

**Milestone Goal:** Show time spent calling other services, and follow a request into the Celery tasks it sends. Both libraries stay optional: imported lazily, never dependencies.

#### Phase 14: HTTP Client Watcher
**Goal**: Outgoing `requests`/`httpx` calls appear in the trace.
**Depends on**: Phase 12
**Requirements**: HTTP-01..03
**Success Criteria** (what must be TRUE):
  1. A `requests` or `httpx` (sync or async) call during a trace records an `http.request` event with method, masked URL, status, duration and error.
  2. Without `requests`/`httpx` installed, the package imports and the watcher does nothing.
  3. The Issues page ranks slow hosts.

Work items:
- [ ] `requests` instrumentation
- [ ] `httpx` sync and async instrumentation
- [ ] Dashboard page and Issues integration

#### Phase 15: Celery Watcher
**Goal**: Every task run is a trace, linked to the request that sent it.
**Depends on**: Phases 8, 9
**Requirements**: CELERY-01..05
**Success Criteria** (what must be TRUE):
  1. A task sent from a view carries the view's trace id; the worker records the task as its own trace with `parent_trace_id` set.
  2. The task trace has the CELERY-03 fields, with args and kwargs masked.
  3. Without Celery installed, the package imports and the watcher does nothing.
  4. The request's trace page lists the tasks it sent; each task links back.

Work items:
- [ ] Header propagation and worker-side trace
- [ ] Task event capture
- [ ] Trace page links

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation | v1.0 | 3/3 | Complete | 2026-05-03 |
| 2. Watchers (Request/SQL/Exception) | v1.0 | 1/1 | Complete | 2026-05-03 |
| 3. Dashboard | v1.0 | 3/3 | Complete | 2026-05-03 |
| 4. Safety & Hardening | v1.1 | 3/3 | Complete | 2026-05-23 |
| 5. Cache & Template Watchers | v1.1 | 1/1 | Complete | 2026-09-30 |
| 6. Signal & Logging Watchers | v1.1 | 1/1 | Complete | 2026-09-30 |
| 7. ASGI Verification & Release Prep | v1.1 | 1/1 | Complete | 2026-09-30 |
| 8. Event Schema v2 | v1.2 | 1/1 | Complete | 2026-09-30 |
| 9. Trace API | v1.2 | 0/1 | Planned | - |
| 10. Management Command Watcher | v1.2 | 0/1 | Planned | - |
| 11. Query Guard for Tests | v1.2 | 0/1 | Planned | - |
| 12. Issues Page & CSV Export | v1.2 | 0/1 | Planned | - |
| 13. Email Watcher | v1.2 | 0/1 | Planned | - |
| 14. HTTP Client Watcher | v1.3 | 0/1 | Planned | - |
| 15. Celery Watcher | v1.3 | 0/1 | Planned | - |

## How work proceeds

Each phase is worked with the Superpowers flow: brainstorm → spec in `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` → plan in `docs/superpowers/plans/` → execute on a feature branch → finish the branch. When a phase ships, tick its work items here, update the progress table, and update requirement statuses in `docs/requirements.md`. Phases 1–4 were planned with GSD; their plan files live in git history (last present at commit `2afc3ee`, under `.planning/`).

## Open Concerns

Carried over from the GSD state file on 2026-09-30:

- ~~License inconsistency~~ — resolved in Phase 7: MIT everywhere.
- ~~No README~~ — resolved in Phase 7 (`README.md`, `CHANGELOG.md`).
- ~~No CI~~ — resolved in Phase 7 (`.github/workflows/ci.yml`).
- ~~MASK-05 fail-open~~ — resolved: masking fails closed (placeholder event), and card matching now requires Luhn (MASK-04).
- ~~Pre-release review findings~~ — resolved on `fix/masking-fail-closed-luhn` (see `CHANGELOG.md` 0.2.0 "Security"): unmasked bodies, URLs and exception locals; the `X-Forwarded-For` allowlist bypass; quadratic SQL detection; flush-time timestamps; no config validation; no retention setting; third-party htmx; CI without lint, types, coverage, PostgreSQL or Django 6.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Storage | `AbstractStorageBackend` | Deferred to first non-ORM backend | v1.1 init |
| Persistence | Async write offload (thread pool / Celery); measure the share of `bulk_create` in the p50 first | Deferred past v1.2 | v1.1 init |
| Masking | Mask SQL parameters by column name (0.2.0 adds `SQL_CAPTURE_PARAMS` to turn them off) | Deferred | Review |
| Support | Drop Python 3.8/3.9 and Django < 4.2 (all past end of life) | Planned for 0.3.0 | Review |
| Plugins | `setuptools` entry_points for external watchers | Deferred | v1.1 init |
| Dashboard | Comparison (trace-id search and JSON export shipped in 0.2.0; CSV export is Phase 12) | Deferred | v1.1 init |
| Watchers | Jinja2 template renders | Deferred | Phase 5 |
| Watchers | Watching all signals (only `SIGNAL_WATCH_LIST` is supported) | Deferred | Phase 6 |
| Watchers | Cache `has_key` / `incr` / `decr` / `touch`; value size for non-str/bytes values | Deferred | Phase 5 |
