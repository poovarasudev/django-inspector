---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: complete
last_updated: "2026-05-03T06:30:00.000Z"
progress:
  total_phases: 3
  completed_phases: 3
  total_plans: 9
  completed_plans: 9
---

# Project State: django-inspector

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-03)

**Core value:** One trace, full story — see everything that happened for a single request in one place, correlated and sequenced.
**Current focus:** v1.0 milestone shipped — planning v1.1 next

## Current Phase

**Milestone:** v1.0 MVP
**Status:** Complete — shipped 2026-05-03
**Phases:** 3/3 complete, Plans: 9/9 complete

## Phase History

| Phase | Name | Status | Started | Completed |
|-------|------|--------|---------|-----------|
| 1 | Package Foundation & Core Infrastructure | Complete | 2026-05-03 | 2026-05-03 |
| 2 | Watchers | Complete | 2026-05-03 | 2026-05-03 |
| 3 | Dashboard | Complete | 2026-05-03 | 2026-05-03 |

## Active Decisions

- Fixed `pyproject.toml` build-backend from `setuptools.backends.legacy:build` (invalid) to `setuptools.build_meta`
- Added `django_inspector/models.py` re-export for Django model discovery (Event model lives in `storage/models.py`)
- Request watcher driven by middleware hooks (on_request/on_response) rather than Django signals
- SQL watcher uses Django's database instrumentation API (execute_wrappers) instead of monkey-patching CursorDebugWrapper
- Exception watcher hooks into Django's `got_request_exception` signal
- Watcher instances stored on AppConfig for middleware access
- Dashboard uses self-contained CSS with inspector- prefixed classes, no external framework
- HTMX 2.0.4 for live polling, partial updates, URL push
- Request detail offers 3 switchable views: timeline, tabs, waterfall

## Blockers

None.

---
*Last updated: 2026-05-03 after v1.0 milestone completion*
