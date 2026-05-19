# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — MVP

**Shipped:** 2026-05-03
**Phases:** 3 | **Plans:** 9 | **Sessions:** 1

### What Was Built

- Installable Django package with AppConfig, lazy settings layer, and watcher auto-discovery registry
- Async-safe UUID trace_id via contextvars propagated across the full request lifecycle
- Request watcher: full HTTP metadata (method, path, headers, body, status, latency, user, session, IP)
- SQL watcher: query capture with origin tracking, slow query flagging, N+1 detection, duplicate detection
- Exception watcher: full stack traces, per-frame locals, chained exceptions
- Telescope-style dashboard: live HTMX-polled feed, 3-view request detail (timeline/tabs/waterfall), per-section filtering, `inspector_cleanup` command

### What Worked

- Django DB instrumentation API (`execute_wrappers`) for SQL watcher — clean hook, no monkey-patching needed
- Contextvars for trace propagation — async-safe, no thread-local issues, accessible anywhere during request
- Self-contained CSS with `inspector-` prefixed classes — zero external dependencies, easy to scope
- HTMX for live feed polling and partial table updates — no JS build step, works seamlessly with Django templates
- Wave-based plan execution: 03a + 03b in parallel (no file overlap), then 03c depending on 03a

### What Was Inefficient

- REQUIREMENTS.md checkboxes were never updated during execution (all stayed as `Pending`) — needed manual reconciliation at milestone close
- No dashboard view tests written — relied on syntax checks only; a lightweight test client suite would catch template errors earlier

### Patterns Established

- Watcher instances stored on AppConfig (`_watcher_instances`) for middleware access — avoids global state
- Middleware-driven watcher hooks (on_request/on_response) rather than signals — request/response data not available in signals
- HTMX partial detection via `request.headers.get("HX-Request")` in views — clean routing without separate URLs
- `inspector-` CSS prefix on all classes — prevents style leakage into host app
- Per-plan SUMMARY.md with frontmatter (phase, plan, key-files, key-decisions, requirements-completed, duration)

### Key Lessons

1. **Build foundation phases thin** — Phase 1 stub views (7 lines each) + full CSS in 03a made 03c straightforward to implement against known contracts
2. **Metadata key names matter** — reading watcher source files before building views prevented mismatched field names (e.g., `is_slow` vs `slow`, `n_plus_one` vs `nplus1`)
3. **Self-contained CSS pays off** — the `inspector-` prefix strategy means dashboard styles can't conflict with host app styles, and the whole UI is portable
4. **HTMX push-url for all filter forms** — makes filtered views shareable/bookmarkable without extra effort

### Cost Observations

- Model mix: Cascade (Windsurf)
- Sessions: 2 (planning + execution)
- Notable: All 9 plans executed sequentially without worktrees; 65 tests passing after all 3 phases

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v1.0 | 2 | 3 | First milestone — baseline established |

### Cumulative Quality

| Milestone | Tests | Coverage | Zero-Dep Additions |
|-----------|-------|----------|--------------------|
| v1.0 | 65 | ~80% (watchers/core) | HTMX via CDN only |

### Top Lessons (Verified Across Milestones)

1. Read watcher source files before building views that consume their metadata
2. Self-contained CSS with app-specific prefix prevents style conflicts
