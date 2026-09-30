# Phase 12 — Issues Page & CSV Export

**Status:** Written under the owner's standing approval (2026-09-30); assumptions are listed at the end for review · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** DASH-04, DASH-03 (`docs/requirements.md`), PRD §12
**Roadmap:** Phase 12 · **Builds on:** Phase 8 columns and fingerprints

## Goal

- An **Issues** page that answers "what should I fix first?". It groups what was captured into three tables:
  - exceptions by fingerprint
  - SQL by query shape
  - endpoints by route, ranked by latency and error rate

  Each row links to example traces.
- **CSV export** of every dashboard list.

## Decisions

| Decision | Choice |
|---|---|
| Window | A `window` query parameter: `1h`, `24h` (default) or `7d`. Each section also scans at most `ISSUES_SCAN_LIMIT = 10000` of its most recent events inside the window. When the cap cuts the window short, the page says "based on the latest 10,000 …". |
| Exceptions and query shapes | SQL `GROUP BY fingerprint` on the Phase 8 column: `Count`, `Min`/`Max(timestamp)`, `Max(id)` as the example |
| Percentiles | Computed in Python over the bounded rows (SQLite and MySQL have no percentile aggregate), with nearest-rank p50/p95 |
| Endpoint grouping | By `(method, route)`. The request watcher starts storing `route` (the URL pattern, e.g. `orders/<int:pk>/`) and `view` (the resolved view's dotted name); unresolved requests fall back to `path`. |
| Sampling | When `SAMPLING_RATE < 1`, a notice says counts come from sampled data and are relative, not totals. Counts aren't scaled. |
| Placement | New `issues/` page, first in the sidebar. The live feed stays at the dashboard root. |
| CSV | `?format=csv` on every list view exports the current filtered queryset (up to 10,000 rows), with an "Export CSV" link on each list page. Formula injection is neutralised. |

Alternatives rejected:
- **Percentiles in SQL:** only PostgreSQL has `percentile_cont`, and CI covers SQLite too.
- **Grouping endpoints by raw path:** IDs in paths split one endpoint into thousands of rows.
- **Adding a `route` column:** grouping happens in Python over bounded rows anyway. The column can come later if the page needs an index.
- **Scaling counts by 1/rate:** estimates look more precise than they are, and errors and slow requests are always kept, which skews any simple scaling.

## Request watcher: `route` and `view`

In `RequestWatcher.on_response`:
- `match = getattr(request, "resolver_match", None)`
- `metadata["route"] = match.route if match else None`
- `metadata["view"] = match._func_path if match else None`

`_func_path` is Django's dotted view name, used by the debug toolbar and others. Read it with `getattr` and fall back to `None`.

Both keys are masked like the rest of the metadata. Existing request tests keep passing; new keys only.

## Aggregates (`django_inspector/dashboard/issues.py`)

Pure functions, each taking `since` (a datetime) and returning plain dicts, so they can be tested without views:

```python
def exception_groups(since, limit=50) -> Tuple[List[dict], bool]: ...
def query_shape_groups(since, limit=50) -> Tuple[List[dict], bool]: ...
def endpoint_stats(since, limit=50) -> Tuple[List[dict], bool]: ...
```

The `bool` says whether the scan cap was hit.

**Scan bound.** `_bounded(qs)` finds the id of the `ISSUES_SCAN_LIMIT`-th newest row:
`qs.order_by("-id").values_list("id", flat=True)[LIMIT-1:LIMIT]`. If it exists, it adds `id__gte=that_id`. This is one indexed query and gives flat cost however big the table is.

**`exception_groups`:**
- It filters `event_type="exception.raised"`, `timestamp__gte=since` and non-null `fingerprint`.
- It then applies `values("fingerprint").annotate(count, first_seen, last_seen, example_id=Max("id"))` and orders by `-count`, `-last_seen`.
- The example events are fetched in one `in_bulk`. Each row shows:
  - `exception_type` and a truncated `exception_message` from the example;
  - the innermost app frame as `file:function`, the same frame the fingerprint uses (the `columns` helper made public: `innermost_app_frame`);
  - the count, first/last seen, and a link to the example's trace (`/trace/<id>/`).
- It also shows `affected_traces`: `Count("trace_id", distinct=True)`.

**`query_shape_groups`:**
- It filters `event_type="sql.query"` with a fingerprint, grouped the same way: `count`, `Max("id")` as the example, and `affected_traces`.
- It also counts `n_plus_one_traces`, from a second grouped query filtered on `metadata__n_plus_one=True` with `Count("trace_id", distinct=True)`.
- For the top `limit` shapes, `duration_ms` values are fetched (bounded by the same scan cap) to compute p50/p95 in Python.
- Each row shows the normalised SQL of the example, truncated to 300 characters, and its most common origin `file:line`.
- Order: by `n_plus_one_traces` descending, then `count` descending, so N+1s surface first.

**`endpoint_stats`:**
- It filters `event_type="request.completed"`.
- It fetches `values_list("method", "status_code", "duration_ms", "path", "metadata__route", "trace_id")` for the bounded rows.
- It groups in Python by `(method, route or path)`, producing `count`, `p50`, `p95`, `error_rate` (share of 5xx), `client_error_rate` (4xx) and a link to the slowest example's trace.
- Order: by `p95` descending.

## Issues view and template

- **Route:** `issues/` (`issues`), wrapped with `inspector_required`.
- **Template:** `inspector/issues.html` has three sections with tables in the existing list style, the window selector (links), the sampling notice and the scan-cap notices. Empty states read "No exceptions in this window", and so on.
- **Sidebar:** "Issues" comes first.
- **No HTMX polling:** the page is a snapshot.
- **Bounded cost:** at most about 8 queries, each bounded by the scan cap.

## CSV export (DASH-03, `dashboard/export.py`)

`csv_response(qs, columns, filename)` returns a streaming `text/csv` response (`StreamingHttpResponse` with `csv.writer` over an echo buffer), with at most `CSV_MAX_ROWS = 10000` rows.

**Columns.** `columns` is a list of `(header, getter)` pairs:
- **Common to every list:** `timestamp` (ISO 8601), `trace_id`, `event_type`, `duration_ms`.
- **Per list:**
  - requests: method, path, status_code, user
  - queries: sql, db_alias, is_slow, n_plus_one, is_duplicate, fingerprint
  - exceptions: exception_type, exception_message, path, fingerprint
  - cache: operation, key, hit, backend
  - templates: name, path
  - logs: level, logger, message
  - signals: signal, sender, receiver_count
  - traces: name, status, event_type

  Each getter reads the existing metadata keys that list's template shows. The worker confirms the exact key names from the templates.

**Formula injection.** A cell whose text starts with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with `'`.

**Where it runs.** Each list view checks `request.GET.get("format") == "csv"` after applying its filters and before paginating, and returns `csv_response(...)`. List templates add an "Export CSV" link that keeps the current query string plus `format=csv`.

The data is already masked, because it's read from stored masked metadata.

## Error handling

- A bad `window` value falls back to `24h`.
- Aggregates skip rows with a NULL fingerprint or duration.
- Nothing on these pages writes to the database.

## Testing

| File | Covers |
|---|---|
| `tests/test_issues.py` (new) | Each aggregate with crafted `Event` rows: grouping, counts, first/last seen, affected traces, N+1 trace counts, p50/p95 (nearest rank, including one value), error rates, route vs path fallback, NULL fingerprints skipped; the window filter; the scan cap (patched small) sets the flag and bounds the rows |
| `tests/test_request_watcher.py` | `route` and `view` captured for a resolved URL; `None` for an unresolved one |
| `tests/test_dashboard_pages.py` / `test_dashboard_features.py` | Issues page renders with data and empty; window selector; sampling notice at rate < 1; scan-cap notice; links to traces; sidebar entry first; access control; query count bounded (`django_assert_max_num_queries` or `assertNumQueries` with an upper bound) |
| `tests/test_csv_export.py` (new) | Each list's `?format=csv` returns CSV with the header and filtered rows; content type and filename; row cap (patched small); formula-injection cells prefixed; masked values stay masked; access control applies |

Then the full gates.

## Docs

- README: the Issues page (sections, window, scan cap, sampling note) and CSV export.
- CHANGELOG: the 0.3.0 section, citing DASH-03 and DASH-04, plus the new `route`/`view` request metadata.
- Roadmap and requirements: tick Phase 12, DASH-03 and DASH-04.

## Assumptions (for the owner's end-of-milestone review)

1. **The live feed stays at the dashboard root.** Issues is a new first sidebar entry, not the landing page.
2. **Default window is 24h** with a 10,000-row scan cap per section, and counts under sampling aren't scaled.
3. **Endpoints are grouped by URL route.** This adds `route` and `view` keys to new `request.completed` events.
4. **CSV exports** are capped at 10,000 rows and neutralise spreadsheet formulas.
