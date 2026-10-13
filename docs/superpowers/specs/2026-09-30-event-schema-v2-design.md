# Phase 8 — Event Schema v2

**Status:** Approved in chat · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** SCHEMA-01..06 (`docs/requirements.md`), PRD §10, concern C-13
**Roadmap:** Phase 8 (`docs/roadmap.md`) · **Discovery:** `docs/discovery/2026-09-30-next-features.md`

## Goal

Give `Event` real columns for the fields later phases filter and group on, before more watchers multiply the rows:
- `method`, `status_code`, `path` and `duration_ms` for filtering and percentiles
- `fingerprint` for grouping SQL and exceptions (the Issues page, Phase 12)
- `parent_trace_id` for linking traces (Celery, Phase 15)
- `pinned` for keeping a trace past retention

Plus a read-only Django admin for `Event`.

## Decisions

| Decision | Choice |
|---|---|
| How columns are filled | Derived at flush time from the **masked** metadata by per-event-type extractors in `storage/columns.py`. Watchers don't change. |
| Old rows | No backfill. The package is unpublished and has no installs, so there is no old data to migrate. The migration is schema-only. |
| `kind` column | Dropped (SCHEMA-01 amended). Each trace has one anchor event whose `event_type` already names its kind (`request.completed`, later `command.completed`, `task.completed`). |
| Spans | Not added. `parent_trace_id` links traces; nothing planned needs spans. |
| SQL fingerprint | First 16 hex characters of SHA-1 over `"<db_alias>\n<normalised SQL>"` |
| Exception fingerprint | First 16 hex characters of SHA-1 over `"<exception type>\n<file>\n<function>"` of the innermost application frame; no line number |
| Pinning | A `pinned` flag on every row of a trace, set by a staff-only POST; retention skips pinned rows |
| Migrations | New `0003`; `0001` and `0002` untouched |

Alternatives rejected:
- **Watchers pass columns to `record()`:** it touches all seven watchers, and column values would skip `mask_metadata`, so every watcher would have to mask them by hand.
- **Both derived and explicit columns:** two mechanisms for one job.
- **A separate `PinnedTrace` model:** a second public model and a join in every retention query, for one boolean.
- **Backfilling in the migration, or with a command:** there is no data to backfill.

## Model (`django_inspector/storage/models.py`) and migration `0003`

New fields on `Event`. All are nullable except `pinned`:

| Field | Type | Index |
|---|---|---|
| `method` | `CharField(max_length=16, null=True, blank=True)` | — |
| `status_code` | `PositiveSmallIntegerField(null=True, blank=True)` | — |
| `path` | `CharField(max_length=1024, null=True, blank=True)` | — |
| `duration_ms` | `FloatField(null=True, blank=True)` | — |
| `fingerprint` | `CharField(max_length=16, null=True, blank=True)` | `Index(fields=["event_type", "fingerprint"], name="inspector_type_fp_idx")` |
| `parent_trace_id` | `CharField(max_length=32, null=True, blank=True)` | `db_index=True` |
| `pinned` | `BooleanField(default=False)` | — |

- Requests-list filters run after the `(event_type, -timestamp)` index has narrowed the rows to `request.completed`, so `method`, `status_code` and `path` get no index of their own.
- `parent_trace_id` is only a column in this phase. Phase 15 fills it.
- `0003_event_schema_v2.py` is generated with `makemigrations` and then renamed. A test checks that `makemigrations --check` finds no changes.

## Column extraction (`django_inspector/storage/columns.py`)

```python
def derive_columns(event_type: str, metadata: dict) -> dict: ...
def sql_fingerprint(sql: str, db_alias: str) -> str: ...
def exception_fingerprint(exception_type: str, frames: list) -> str: ...
```

`derive_columns` works in two steps:
1. **Generic step, for every event type.** `duration_ms` comes from `metadata["duration_ms"]`, or `metadata["latency_ms"]` when that is absent.
2. **Extractor step.** The extractor for `event_type` in `_EXTRACTORS` (a dict) runs and its keys are merged in. Event types without an extractor get only the generic step, so future watchers get a duration column without extra work.

| Event type | Extractor fills |
|---|---|
| `request.completed` | `method`, `path`, `status_code` (`duration_ms` comes from `latency_ms` in the generic step) |
| `sql.query` | `fingerprint = sql_fingerprint(metadata["sql"], metadata["db_alias"])` |
| `exception.raised` | `fingerprint = exception_fingerprint(metadata["exception_type"], metadata["frames"])`; `method` and `path` from `request_method` and `request_path` when present |

### Coercion

Values are coerced, never trusted. A value that fails its check is dropped, which leaves the column NULL:
- `method`: a `str`, truncated to 16 characters
- `path`: a `str`, truncated to 1024 characters
- `status_code`: an `int` (not a `bool`) from 100 to 599
- `duration_ms`: an `int` or `float` (not a `bool`) that is finite and ≥ 0

### Fingerprints

- **SQL:** `sql_fingerprint` normalises the SQL with the SQL watcher's `_normalize_sql`, moved into `columns.py` as `normalize_sql` and re-exported from `watchers/sql.py` so imports keep working. The input is the stored (masked, possibly truncated) SQL. Masking and truncation only affect literals and the tail, and literals are replaced by placeholders anyway, so equal query shapes still collide. The `lru_cache` makes the second call on the same string nearly free.
- **Exceptions:** `exception_fingerprint` picks the innermost frame whose `file` isn't library code, using `watchers.utils._is_library_file` (renamed to the public `is_library_file`). If every frame is library code, it uses the innermost frame. With no frames, the fingerprint covers the type alone. Frames with a missing file or function contribute an empty string.
- Both hash with `hashlib.sha1(..., usedforsecurity=False)` on Python ≥ 3.9, and plain `sha1` on 3.8.

### Masking

Extractors read only the masked metadata that `record()` buffered. A column can therefore never hold something the masking removed from the metadata. For example, a JWT in `request.path` is already `***REDACTED***` in `metadata["path"]`.

## Flush (`django_inspector/storage/flush.py`)

`flush_events()` builds each `Event` with `**_safe_columns(item)`:

```python
def _safe_columns(item) -> dict:
    try:
        return derive_columns(item["event_type"], item["metadata"])
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS:
            raise
        logger.warning("inspector: could not derive columns for a %s event", item["event_type"], exc_info=True)
        return {}
```

- A failing extractor never loses the event: the event is stored with NULL columns.
- Extraction runs only for events that are flushed. Sampled-out requests clear their buffer before the flush, so they pay nothing for it.
- `parent_trace_id` and `pinned` are never derived. They keep their defaults (NULL, False).

## Retention (`django_inspector/storage/retention.py`, `inspector_cleanup`)

- `delete_older_than()` filters `timestamp__lt=cutoff, pinned=False`. `maybe_prune()` and `inspector_cleanup` both go through it.
- `inspector_cleanup --dry-run` counts with the same filter, so its number matches what a real run deletes.

## Pinning (dashboard)

- **Route:** `POST trace/<str:trace_id>/pin/`, named `trace-pin`, wrapped in `inspector_required`.
- **CSRF:** the view is decorated with `csrf_protect`, so the protection applies even when the host project doesn't use `CsrfViewMiddleware`. htmx sends the token through `hx-headers` on the button. That is an attribute, not inline script, so the dashboard's CSP is unaffected.
- **Request:** the body carries `pinned=1` or `pinned=0`. Any other value returns 400.
- **Response:**
  - It runs `Event.objects.filter(trace_id=<normalised id>).update(pinned=...)` and re-renders the button partial `inspector/_pin_button.html`.
  - If the update touched no rows (an unknown or empty trace), it returns 404.
  - A GET returns 405 (`require_POST`).
- **UI:** the request detail page shows the button next to the trace id: "Pin trace" or "Unpin trace", and a "Pinned" badge while pinned. Pinned requests show the badge in the requests list too.

## Requests-list filters (SCHEMA-06)

`requests_list` switches to the columns:
- `method` filters on `method=`.
- A status class filters on `status_code__gte=low, status_code__lt=high`.
- `path` filters on `path__icontains`.

The queries and exceptions lists keep their JSON lookups: they filter on flags and types that don't have columns.

## Admin (`django_inspector/admin.py`, SCHEMA-05)

`EventAdmin` registered on `admin.site`:
- **List:** `list_display = ("timestamp", "event_type", "trace_id", "method", "status_code", "path", "duration_ms", "pinned")`, `list_filter = ("event_type", "pinned")`, `search_fields = ("trace_id", "fingerprint")`, `ordering = ("-timestamp",)`.
- **Read-only:** every field is in `readonly_fields`. `has_add_permission`, `has_change_permission` and `has_delete_permission` all return `False`. Deleting goes through retention, so pinning stays the only way to keep rows.
- Django loads `admin.py` only when the host has `django.contrib.admin` installed. Hosts without the admin are unaffected.

## Error handling

- Column extraction failures are logged, and the event is still written (see Flush).
- Pinning an unknown trace returns 404 and writes nothing. A bad `pinned` value returns 400.
- Nothing in this phase runs in the per-query hot path. Extraction runs once per event at flush.

## Testing

| File | Covers |
|---|---|
| `tests/test_columns.py` (new) | Each extractor; the generic `duration_ms` step (from `duration_ms` and from `latency_ms`); unknown event types; coercion (bool or out-of-range status, NaN/negative/bool duration, over-long method and path, non-str values); SQL fingerprints equal across literals, different across aliases and across shapes; exception fingerprints equal across line numbers, different across types and functions; all-library fallback; no frames; a JWT in the request path appears masked in the `path` column |
| `tests/test_storage.py` | `flush_events` writes derived columns; a failing extractor still writes the event with NULL columns and logs a warning; with `INSPECTOR_RAISE_ERRORS` it raises; an end-to-end request through the test client fills `method`/`path`/`status_code`/`duration_ms` |
| `tests/test_migrations.py` (new) | `makemigrations django_inspector --check --dry-run` reports no changes |
| `tests/test_retention.py` | Pinned events survive `maybe_prune`, `delete_older_than` and `inspector_cleanup`; the `--dry-run` count excludes them |
| `tests/test_dashboard_features.py` | Pin and unpin update every row of the trace; CSRF enforced (`Client(enforce_csrf_checks=True)`); non-staff get 403; unknown trace gives 404; bad value gives 400; GET gives 405; the badge shows on the detail page and list; requests-list filters by method, status class and path hit the columns (`str(qs.query)` contains no JSON operator) and return the right rows |
| `tests/test_admin.py` (new) | `EventAdmin` on a fresh `AdminSite`: no add, change or delete permission; every model field is read-only |
| `tests/test_sql_watcher.py`, `tests/test_watcher_utils.py` | `_normalize_sql` and `_is_library_file` still importable from their old modules |

Then:
- the full suite, `ruff check`, `mypy`
- `scripts/benchmark.py`: the p50 must stay within budget (about 0.6 ms at 0.2.0)
- `uv build && python scripts/check_dist.py dist/`: migration `0003` must be in the wheel

## Docs

- `CHANGELOG.md`: a new "0.3.0 (unreleased)" section listing the new columns, fingerprints, pinning and the admin.
- `README.md`: a short "Pinning a trace" paragraph and a note on the admin. There are no new settings, so the settings table and `checks.py` don't change.
- `docs/requirements.md`: amend SCHEMA-01 (no `kind`). On finishing, tick the SCHEMA items and Phase 8 in `docs/roadmap.md`.
