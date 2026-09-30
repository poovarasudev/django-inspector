# Roadmap: django-inspector

## Overview

`django-inspector` shipped its v1.0 MVP across three phases (foundation, three watchers, dashboard) tagged at commit `0675c3a`. The next milestone — **v1.1** — closes the remaining PRD Phase-1 watchers (Cache, Template, Signal, Logging) and lands the production-safety primitives (masking, sampling, dashboard auth, async-safe buffers, ignore lists, loud-by-default logging) needed before any sane production rollout. Granularity is **coarse**: each new phase is a coherent ship-able slice.

## Milestones

- ✅ **v1.0 MVP** — Phases 1–3 (shipped 2026-05-03, tag `v1.0`)
- 🚧 **v1.1 Phase-1 Completion + Production Safety** — Phases 4–7 (in progress)
- 📋 **v1.2 Phase-2 PRD Watchers** — Model, Email, Management Command, Middleware (planned)
- 📋 **v1.3 Phase-3 PRD Watchers** — Celery, Scheduler, HTTP Client, Redis (planned)

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

### 🚧 v1.1 Phase-1 Completion + Production Safety (In Progress)

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

Known gaps carried forward: MASK-04 (no Luhn check), MASK-05 (unmasked fallback on masking error), IGN-04 (docs) — see `docs/requirements.md`.

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
**Spec**: not started (`docs/superpowers/specs/`)

Work items:
- [ ] ASGI end-to-end test app + async coverage for v1.1 watchers
- [ ] Release hygiene — README, CHANGELOG, license reconciliation, build smoke test

---

### 📋 v1.2 Phase-2 PRD Watchers (Planned)

**Milestone Goal:** Implement PRD §11.8–§11.11 (Model, Email, Management Command, Middleware watchers) on top of the v1.1 safety foundation. Also: introduce optional async persistence and denormalized hot-path columns if v1.1 telemetry shows they are needed.

Phases not yet broken down.

### 📋 v1.3 Phase-3 PRD Watchers (Planned)

**Milestone Goal:** Implement PRD §11.12–§11.15 (Celery, Scheduler, HTTP Client, Redis watchers). Introduces optional dependencies (gated behind extras).

Phases not yet broken down.

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation | v1.0 | 3/3 | Complete | 2026-05-03 |
| 2. Watchers (Request/SQL/Exception) | v1.0 | 1/1 | Complete | 2026-05-03 |
| 3. Dashboard | v1.0 | 3/3 | Complete | 2026-05-03 |
| 4. Safety & Hardening | v1.1 | 3/3 | Complete | 2026-05-23 |
| 5. Cache & Template Watchers | v1.1 | 1/1 | Complete | 2026-09-30 |
| 6. Signal & Logging Watchers | v1.1 | 1/1 | Complete | 2026-09-30 |
| 7. ASGI Verification & Release Prep | v1.1 | — | Not started (next) | - |

## How work proceeds

Each phase is worked with the Superpowers flow: brainstorm → spec in `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` → plan in `docs/superpowers/plans/` → execute on a feature branch → finish the branch. When a phase ships, tick its work items here, update the progress table, and update requirement statuses in `docs/requirements.md`. Phases 1–4 were planned with GSD; their plan files live in git history (last present at commit `2afc3ee`, under `.planning/`).

## Open Concerns

Carried over from the GSD state file on 2026-09-30:

- **License inconsistency** (CONCERNS C-11): `pyproject.toml` says MIT, `LICENSE` is Apache-2.0. Reconcile in Phase 7 at the latest.
- **No README**: `pyproject.toml` references one. Address in Phase 7.
- **No CI**: nothing runs tests on push. Not blocking v1.1, but add before tagging.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Storage | `AbstractStorageBackend` | Deferred to first non-ORM backend | v1.1 init |
| Persistence | Async write offload (thread pool / Celery) | Deferred to v1.2 | v1.1 init |
| Schema | Denormalized event columns (`method`, `status_code`) | Deferred to v1.2 | v1.1 init |
| Plugins | `setuptools` entry_points for external watchers | Deferred | v1.1 init |
| Dashboard | Search, comparison, JSON/CSV export | Deferred | v1.1 init |
| Schema | Capture-time event timestamps (`auto_now_add` is set at flush, so waterfall offsets are meaningless; needs a migration) | Deferred | Phase 5 |
| Watchers | Jinja2 template renders | Deferred | Phase 5 |
| Watchers | Watching all signals (only `SIGNAL_WATCH_LIST` is supported) | Deferred | Phase 6 |
| Watchers | Cache `has_key` / `incr` / `decr` / `touch`; value size for non-str/bytes values | Deferred | Phase 5 |
