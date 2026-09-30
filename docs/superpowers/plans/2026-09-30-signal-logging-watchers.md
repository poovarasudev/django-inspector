# Signal & Logging Watchers Implementation Plan

**Goal:** Capture signal dispatches (per-receiver timing) and log records as trace-correlated events, and browse them on dashboard pages.

**Spec:** `docs/superpowers/specs/2026-09-30-signal-logging-watchers-design.md`

**Execution:** Implemented directly in-session, following the user's instruction from Phase 5 to skip the Superpowers execution skills. TDD per task, one commit per task. Test command: `uv run --extra dev python -m pytest`.

## Global Constraints

The same constraints as Phase 5 (`docs/superpowers/plans/2026-09-30-cache-template-watchers.md` § Global Constraints), in particular:
- no new runtime dependencies
- additive settings only, no migration
- `ContextVar` for per-trace state
- host calls are never altered
- the `django_inspector` logger only
- `self.record(...)` only

## Review Focus

1. **A receiver that raises under `send()`.** The exception must reach the caller unchanged, the event must record the error, and later receivers must not appear as called (Task 1).
2. **Callers that inspect `send()` responses.** Response tuples must contain the original receiver objects, not the inspector's wrappers (Task 1).
3. **A receiver that dispatches another watched signal.** The inner dispatch gets its own event and the outer receiver list isn't polluted (Task 1).
4. **The logging watcher is on by default.** Existing tests and host apps that log warnings inside a request must not break, and the inspector's own warnings must never be recaptured (Task 2).
5. **A log call whose format arguments are wrong** (`logger.warning("%s %s", 1)`) must still produce an event with the raw message, and must never raise (Task 2).

## Tasks

| # | Task | Files | Tests |
|---|---|---|---|
| 1 | Signal watcher | `watchers/signal.py` (new), `conf.py` (`"signal": False`, `SIGNAL_WATCH_LIST`), `apps.py` | `tests/test_signal_watcher.py` |
| 2 | Logging watcher | `watchers/log.py` (new), `conf.py` (`"log": True`, `LOG_LEVEL_THRESHOLD`), `apps.py` | `tests/test_log_watcher.py`, full suite with the watcher on |
| 3 | Dashboard — Logs pages | `dashboard/views.py`, `dashboard/urls.py`, `templates/inspector/logs/{list,_table,detail}.html`, `_sidebar.html`, `base.html` (level colours) | `tests/test_dashboard_pages.py` |
| 4 | Dashboard — Signals pages | `dashboard/views.py`, `dashboard/urls.py`, `templates/inspector/signals/{list,_table,detail}.html`, `_sidebar.html` | `tests/test_dashboard_pages.py` |
| 5 | Request detail integration | `dashboard/views.py` (`request_detail`), `requests/detail.html`, `base.html` (timeline/waterfall colours) | `tests/test_dashboard_pages.py` |
| 6 | Docs | `docs/roadmap.md`, `docs/requirements.md`, `docs/project.md`, `CLAUDE.md` | full suite + end-to-end smoke run |

Each task:
1. Write the failing tests.
2. Run them and see them fail.
3. Implement.
4. Run the task's tests, then the full suite.
5. Commit with a conventional message (`feat(signal-watcher): …`, `feat(log-watcher): …`, `feat(dashboard): …`, `docs: …`) that cites the requirement IDs.
