# Phase 3: Dashboard - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a Telescope-style web dashboard for django-inspector served at a configurable URL prefix (`/inspector/`). The dashboard provides a live feed of recent requests, per-watcher section pages (Requests, Queries, Exceptions), detail views with trace timelines, filtering with HTMX interactions, and a management command for event cleanup. After this phase, the package is fully usable end-to-end.

Requirements: STOR-02, DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-06, DASH-07, DASH-08, DASH-09, DASH-10

</domain>

<decisions>
## Implementation Decisions

### Dashboard Layout & Navigation
- **D-01:** Left sidebar navigation (Telescope-style) — fixed sidebar with section links (Live Feed, Requests, Queries, Exceptions), content area on right
- **D-02:** Dark sidebar with light content area — dark-colored sidebar for navigation identity/contrast, white/light-gray content area for readability
- **D-03:** 7 distinct pages: Live Feed (landing), Requests list, Request detail (trace timeline), Queries list, Query detail, Exceptions list, Exception detail

### Live Feed Behavior
- **D-04:** Auto-polling with pause/play toggle — HTMX polling (every 3 seconds) by default, user can pause the feed to examine entries without them refreshing away
- **D-05:** 25 items per page with standard pagination (not infinite scroll)
- **D-06:** Live feed is the landing page at `/inspector/`

### Trace Timeline Design
- **D-07:** Three switchable views on the request detail page: Timeline (chronological vertical cards), Tabbed (grouped by type: request/queries/exceptions), and Waterfall (horizontal timing bars like browser DevTools)
- **D-08:** Summary header always visible in all views — shows total queries count, total query time, exception count, request latency
- **D-09:** View switching via query parameter (`?view=timeline|tabs|waterfall`) with full page reload — simple, bookmarkable, no extra HTMX complexity for view switching
- **D-10:** Default view is Timeline (chronological vertical cards)

### Filtering & Search UX
- **D-11:** Inline filter bar above the results table on all list pages — always visible, horizontal row of filter inputs
- **D-12:** Requests page filters: method dropdown, status code dropdown, path text input, time range
- **D-13:** Queries page filters: slow flag, N+1 flag, duplicate flag, database alias
- **D-14:** Exceptions page filters: exception type, time range
- **D-15:** HTMX live filtering with URL push (`hx-push-url`) — instant results on filter change, URL updates for shareable/bookmarkable filtered views

### Claude's Discretion
- CSS color palette specifics (dark sidebar color, accent colors) — follow a dev-tool aesthetic similar to Telescope
- Table column choices for list pages — include the most useful columns per section
- HTMX include strategy (CDN link in base template vs bundled) — simplest approach that works
- Status badge colors (green for 2xx, yellow for 3xx, red for 4xx/5xx)
- Management command argument names and defaults for `inspector_cleanup`
- Pagination controls style

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Foundation
- `.planning/PROJECT.md` — Core value, constraints (Django templates + HTMX, no external CSS frameworks, self-contained CSS)
- `.planning/REQUIREMENTS.md` — Full requirement definitions for STOR-02, DASH-01 through DASH-10
- `.planning/ROADMAP.md` — Phase 3 goal and success criteria

### Existing Code
- `django_inspector/conf.py` — Settings system including `DASHBOARD_URL_PREFIX` (already configurable, default: `inspector/`)
- `django_inspector/storage/models.py` — `Event` model with `trace_id`, `event_type`, `timestamp`, `metadata` (JSONField)
- `django_inspector/storage/flush.py` — Event buffer/flush system (how events get written)
- `django_inspector/middleware.py` — InspectorMiddleware (trace_id generation, watcher notification, flush)
- `django_inspector/watchers/base.py` — BaseWatcher with `record()` method using event_type conventions
- `django_inspector/apps.py` — AppConfig with watcher auto-discovery
- `django_inspector/dashboard/__init__.py` — Empty package, ready for dashboard code

### Inspiration
- Laravel Telescope — reference UX for dashboard layout, section pages, and drill-down patterns

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Event` model with JSONField metadata — all dashboard queries use `Event.objects.filter(event_type=...)` with JSON lookups on metadata
- `inspector_settings.DASHBOARD_URL_PREFIX` — URL prefix already configurable, dashboard URLs should use this
- `inspector_settings.is_enabled` — dashboard views should check this (though middleware already gates)
- Watcher registry in `watchers/registry.py` — could be used to dynamically build sidebar sections

### Established Patterns
- Event types follow `{watcher_name}.{event_kind}` convention: `request.started`, `request.completed`, `sql.query`, `exception.raised`
- All event data lives in `metadata` JSONField — no separate tables per watcher type
- `trace_id` is a 32-char hex UUID linking all events from one request
- Timestamps use `auto_now_add=True` with `ordering = ["timestamp"]`
- Composite index on `(trace_id, timestamp)` already exists for efficient trace queries

### Integration Points
- Dashboard URL config (`urls.py`) needs to be includable via `path("inspector/", include("django_inspector.dashboard.urls"))`
- The dashboard should exclude its own requests from the live feed (middleware already handles `REQ-06`)
- Management command `inspector_cleanup` goes in `django_inspector/management/commands/`

</code_context>

<specifics>
## Specific Ideas

- User wants the request detail page to offer **all three views** (Timeline, Tabs, Waterfall) via a view switcher — not just one layout
- Telescope-style visual identity with dark sidebar is the reference aesthetic
- Auto-polling with pause toggle gives users control over the live feed without losing the "live" feeling
- HTMX filtering with URL push provides the best UX: instant feedback + shareable URLs

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 3-Dashboard*
*Context gathered: 2026-05-03*
