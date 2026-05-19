# django-inspector

## What This Is

A unified runtime observability and debugging package for Django applications. It captures requests, SQL queries, and exceptions into a single correlated trace, and presents them through a Telescope-style dashboard served by Django itself. Built for local development use, with a path toward production-safe operation in future versions.

## Core Value

One trace, full story — see everything that happened for a single request (queries, exceptions, cache, signals, tasks) in one place, correlated and sequenced.

## Requirements

### Validated

- ✓ Developer can install via pip, add to INSTALLED_APPS, run migrate, and start using inspector — v1.0
- ✓ Watchers auto-discover by default; individual watchers can be disabled via DJANGO_INSPECTOR settings dict — v1.0
- ✓ Global INSPECTOR_ENABLED setting to toggle the entire package on/off — v1.0
- ✓ Request Watcher captures method, path, headers, status code, latency, user, and full request/response details — v1.0
- ✓ SQL Watcher captures every query with SQL, params, execution time, db alias, origin file/line — v1.0
- ✓ SQL Watcher detects N+1 query patterns and duplicate queries — v1.0
- ✓ Exception Watcher captures stack traces, locals, chained exceptions, and request context — v1.0
- ✓ All events belong to a trace — every watcher event is correlated to the originating request via trace_id — v1.0
- ✓ Events are stored in the application's database via Django ORM — v1.0
- ✓ Dashboard served at a configurable URL path using Django templates + HTMX — v1.0
- ✓ Dashboard shows a live feed of recent requests as the entry point — v1.0
- ✓ Clicking a request opens a timeline view showing request → queries → exceptions in sequence — v1.0
- ✓ Dedicated web pages for each watcher type: Requests, SQL Queries, Exceptions — v1.0
- ✓ Dashboard supports filtering by status code, method, path, and time range — v1.0
- ✓ Slow query highlighting based on configurable threshold — v1.0
- ✓ Management command `inspector_cleanup` to purge old events — v1.0

### Active

- [ ] Cache Watcher — captures get/set/delete/clear with hit/miss tracking
- [ ] Template Watcher — captures template hierarchy, render time, context size
- [ ] Signal Watcher — captures signal name, sender, receivers, execution time
- [ ] Logging Watcher — captures logger, level, message, traceback with trace correlation
- [ ] Dashboard authentication gate — prevent unauthorized access in shared environments
- [ ] Production safety: sampling, sensitive data masking, retention policies

### Out of Scope

- Async persistence / background event writes — not needed for dev mode (revisit for production use)
- Model Watcher, Email Watcher — deferred to v1.2+
- Celery Watcher, Scheduler Watcher, HTTP Client Watcher, Redis Watcher — deferred to v1.2+
- DRF, Channels, Admin integrations — deferred to v1.2+
- OpenTelemetry export, distributed tracing, browser RUM, frontend instrumentation — non-goals
- CPU flame graphs, memory profiling, Kubernetes integration — non-goals
- React/Vue SPA dashboard — using Django templates + HTMX (working well, staying)
- Plugin system — deferred until core watcher set is stable

## Context

- **Shipped v1.0:** Fully functional installable package with ~1,267 Python LOC, 65 tests, 55 files. Django 4.0+ / Python 3.8+.
- **Tech stack:** Django ORM, contextvars, Django DB instrumentation API (execute_wrappers), Django templates + HTMX 2.0.4, self-contained CSS.
- **Ecosystem gap:** Django lacks a unified observability tool. Developers currently use django-debug-toolbar (request-scoped, no persistence), django-silk (profiling only), Flower (Celery only), Sentry (exceptions only), and custom logging — none provide correlated traces.
- **Inspiration:** Laravel Telescope provides the reference UX — a single dashboard with section pages per event type, click-to-drill-down, and correlated timelines.
- **Architecture:** Event-based with trace correlation. Watchers generate normalized events → stored via Django ORM → rendered in dashboard. Each watcher is a self-contained module.
- **Package structure:** `django_inspector/` with sub-packages for `core/`, `watchers/`, `storage/`, `dashboard/`, and `tracing/`.
- **Known gaps from v1.0:** No dashboard auth gate (dev-only by design). No test coverage for dashboard views. `inspector_cleanup` requires manual scheduling.

## Constraints

- **Django version**: Django 4.0+ — no support for older versions
- **Python version**: Python 3.8+ — broad compatibility
- **Dashboard tech**: Django templates + HTMX — no JS build step, stays in Django ecosystem
- **Storage**: Django ORM only for v1 — SQLite, PostgreSQL, MySQL supported via Django's DB abstraction
- **Scope**: Dev-only for this milestone — no production safety features yet
- **Package type**: Installable Python package (pip install django-inspector)
- **Tooling**: Use `uv` + `venv` for all Python development, dependency management, and running commands
- **License**: MIT or BSD-3-Clause (open source)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|----------|
| Dev-only for v1 | Eliminates sampling/masking/retention complexity. Focus on core value first. | ✓ Good — zero-friction setup validated |
| Django templates + HTMX for dashboard | No JS build step, stays in Django ecosystem, simpler to ship and maintain | ✓ Good — self-contained CSS, no build step |
| Same DB default with multi-db support later | Zero-config setup for devs. Separate DB can be added via Django's database routing. | ✓ Good — SQLite/PostgreSQL/MySQL all work |
| Request + SQL + Exception as v1 watchers | Core debugging trio. Covers the most common debugging scenarios. | ✓ Good — covers the most-used debugging paths |
| Auto-discover watchers with opt-out config | Minimal setup friction. Devs can disable watchers they don't need via settings. | ✓ Good — AppConfig.ready() works cleanly |
| Trace-based event correlation | All events tied to a trace_id. Foundation for cross-watcher visibility. | ✓ Good — enables timeline/tabs/waterfall views |
| Middleware-driven request watcher (not signals) | Request/response data only available in middleware layer | ✓ Good — clean, no signal timing issues |
| Django DB instrumentation API for SQL watcher | execute_wrappers preferred over monkey-patching CursorDebugWrapper | ✓ Good — future-proof, supported API |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-03 after v1.0 milestone*
