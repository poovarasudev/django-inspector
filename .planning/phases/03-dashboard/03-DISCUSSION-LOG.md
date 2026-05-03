# Phase 3: Dashboard - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-03
**Phase:** 03-dashboard
**Areas discussed:** Dashboard layout & navigation, Live feed behavior, Trace timeline design, Filtering & search UX

---

## Dashboard Layout & Navigation

| Option | Description | Selected |
|--------|-------------|----------|
| Left sidebar (Telescope-style) | Fixed left sidebar with section links, content area on right | ✓ |
| Top navbar with tabs | Horizontal nav bar with section tabs, full-width content | |
| Top navbar + section tabs | Top bar with branding, tab sub-navigation within sections | |

**User's choice:** Left sidebar (Telescope-style)
**Notes:** Classic dashboard pattern, works well with the 3-5 sections django-inspector has.

### Color Scheme

| Option | Description | Selected |
|--------|-------------|----------|
| Dark theme (Telescope-style) | Full dark background, light text | |
| Light theme | White/light gray, dark text | |
| Dark sidebar, light content | Dark sidebar for nav contrast, light content for readability | ✓ |

**User's choice:** Dark sidebar, light content
**Notes:** Hybrid approach balances tool identity with content readability.

---

## Live Feed Behavior

### Auto-refresh Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| HTMX polling every 2-3s | Automatic refresh, no JS needed | |
| Manual refresh button | User-controlled, lower server load | |
| Auto-poll with pause toggle | HTMX polling + pause/play toggle | ✓ |

**User's choice:** Auto-poll with pause toggle
**Notes:** Best of both worlds — live feeling with user control.

### Items Per Page

| Option | Description | Selected |
|--------|-------------|----------|
| 25 per page | Compact, loads fast, standard for dev tools | ✓ |
| 50 per page | More visible at a glance | |
| Infinite scroll (HTMX) | Load more on scroll, modern feel | |

**User's choice:** 25 per page
**Notes:** Standard pagination, no infinite scroll complexity.

---

## Trace Timeline Design

### View Options

| Option | Description | Selected |
|--------|-------------|----------|
| Vertical timeline with cards | Chronological events top-to-bottom | |
| Tabbed sections | Grouped by type (request/queries/exceptions) | |
| Waterfall chart | Horizontal timing bars like DevTools | |
| All three: Timeline + Tabs + Waterfall | Three views via toggle, summary header in all | ✓ |

**User's choice:** All three views with a toggle switcher
**Notes:** User specifically requested multiple view options so users can select their preferred view. Summary header (total queries, total time, exception count) visible in all views.

### View Switcher Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| HTMX swap (no page reload) | hx-get swaps content area, URL stays same | |
| Query parameter (?view=timeline) | HTMX swap + URL update, bookmarkable | |
| Full page reload with query param | Simple links with ?view=waterfall | ✓ |

**User's choice:** Full page reload with query parameter
**Notes:** Simplest implementation, bookmarkable views.

---

## Filtering & Search UX

### Filter Presentation

| Option | Description | Selected |
|--------|-------------|----------|
| Inline filter bar above table | Horizontal row of inputs, always visible | ✓ |
| Collapsible filter panel | "Filters" button expands panel | |
| Sidebar filters | Filter controls in left sidebar | |

**User's choice:** Inline filter bar above table
**Notes:** Always visible, matches Telescope pattern.

### Filter Interaction

| Option | Description | Selected |
|--------|-------------|----------|
| HTMX live filtering | Instant swap on change, no page reload | |
| Standard form GET submission | "Apply" button, full reload, shareable URL | |
| HTMX + URL push | Instant filtering + URL update for shareability | ✓ |

**User's choice:** HTMX + URL push
**Notes:** Best UX: instant feedback with shareable/bookmarkable URLs.

---

## Claude's Discretion

- CSS color palette specifics, status badge colors
- Table column choices per section page
- HTMX include strategy
- Pagination controls style
- Management command argument names/defaults

## Deferred Ideas

None — discussion stayed within phase scope.
