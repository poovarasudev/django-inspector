# Phase 5 — Cache & Template Watchers

**Status:** Draft for review · **Date:** 2026-09-30 · **Branch:** `feat/phase-5-cache-template-watchers`
**Covers:** CACHE-01..07, TMPL-01..05 (`docs/requirements.md`), PRD §11.5 and §11.6
**Roadmap:** Phase 5 (`docs/roadmap.md`)

## Goal

Capture cache operations and Django template renders as trace-correlated events. Each request's cache activity and template tree can then be read from its trace, and browsed on dedicated dashboard pages.

## Decisions

| Decision | Choice |
|---|---|
| Spec granularity | One spec for both watchers. They share lifecycle, wiring and the dashboard-trace fix. |
| Cache hook | Wrap methods on the backend **classes** configured in `settings.CACHES` |
| Template hook | Wrap `django.template.base.Template._render` (the hook Django's test instrumentation uses) |
| Defaults | Both watchers **off** by default (`"cache": False`, `"template": False`) |
| Cache value size | Measured for `str`/`bytes` only; `None` for other types (no second pickle) |
| Dashboard scope | Full list + detail pages for both, plus request-detail integration |

Alternatives rejected:
- **Wrapping `caches` instances:** misses per-thread instances that already exist, and breaks `isinstance`.
- **A wrapper `BACKEND`:** turning the watcher on would need two config changes.
- **The `template_rendered` signal:** only fires under the test environment.
- **A custom template backend:** only sees top-level renders.

## Architecture

### New and changed modules

| Path | Change |
|---|---|
| `django_inspector/watchers/cache.py` | New. `CacheWatcher` (`watcher_name = "cache"`), `CACHE_EVENT_TYPES`, registered via `register("cache", CacheWatcher)` |
| `django_inspector/watchers/template.py` | New. `TemplateWatcher` (`watcher_name = "template"`), registered via `register("template", TemplateWatcher)` |
| `django_inspector/watchers/utils.py` | New. `extract_origin()` moved out of `sql.py` (`sql.py` imports it) |
| `django_inspector/ignores.py` | Add `is_dashboard_path(path)` |
| `django_inspector/middleware.py` | Skip tracing for dashboard paths |
| `django_inspector/watchers/request.py` | `should_ignore_request` delegates to `is_dashboard_path` |
| `django_inspector/apps.py` | Import the two new watcher modules in `_autodiscover_watchers` |
| `django_inspector/conf.py` | `DEFAULTS["WATCHERS"]` gains `"cache": False`, `"template": False` |
| `django_inspector/dashboard/{views,urls}.py` + templates | New pages, request-detail integration (see Dashboard) |

### Wiring rules

- `install_hooks()` builds wrappers as closures that capture the watcher instance and call `self.record(...)` directly. Unlike `sql.py`, there is no registry or app-config lookup per call.
- `remove_hooks()` restores exactly what it replaced. `enable()`/`disable()` are already idempotent through `BaseWatcher._enabled` (CACHE-06).
- No new settings keys besides the two `WATCHERS` entries. No schema change and no migration: events use the existing `Event(trace_id, event_type, metadata)`.

### Dashboard requests produce no events (targeted fix)

Today the middleware opens a trace for **every** request, including the dashboard. `RequestWatcher` skips dashboard paths, but the SQL watcher still records their auth/session queries. Those are stored as orphan events with no `request.completed` event. The live feed polls, so the orphans pile up. The template watcher would add roughly 5 more orphans per dashboard page view.

Fix: add `is_dashboard_path(path)` to `ignores.py`, holding the prefix normalisation that `RequestWatcher.should_ignore_request` does today. `InspectorMiddleware.__call__` treats a dashboard path the same way as `IGNORE_PATHS`: no trace is opened, so no watcher records anything. `RequestWatcher.should_ignore_request` delegates to the helper.

The existing assumption remains: `DASHBOARD_URL_PREFIX` must match where the host mounts `django_inspector.dashboard.urls`. This is documented in `conf.py` next to the key.

### Free behaviour (no work needed)

- **Sampling:** the middleware clears the buffer for unsampled requests.
- **`IGNORE_PATHS`:** those requests never get a trace.
- **Masking:** applied by `record()`.
- **ASGI:** all per-trace state is in `ContextVar`s, and `sync_to_async` copies the context.

## Cache watcher

### Hooking

- On `install_hooks()`, read `settings.CACHES`, import each alias's `BACKEND` class, and collect the **unique** classes.
- For each class, wrap `get`, `set`, `add`, `delete`, `clear`, `get_many`, `set_many`, `delete_many`.
- Record which methods were wrapped and whether each was in the class's own `__dict__`:
  - **Defined on the class:** restore the saved original on `remove_hooks()`.
  - **Inherited:** set the wrapper on the class, and `delattr` it on `remove_hooks()`.
- A class is never wrapped twice. A `BACKEND` that fails to import is logged at warning and skipped.
- `get_or_set` is **not** wrapped. `BaseCache.get_or_set` runs a `get` then an `add`, and those appear as two events, which matches what reached the backend. `has_key`, `incr`, `decr` and `touch` are deferred.
- Async methods (`aget`, `aset`, …) delegate to the sync methods through `sync_to_async`, so they are covered, with the trace context copied.

### Re-entrancy

A `ContextVar` flag, `_in_cache_op`, marks an operation in progress. When it is set, the wrapper calls the original directly without recording. So only the outermost call is recorded: `get_many` falling back to per-key `get` produces **one** `cache.get_many` event.

### Fast path

If `get_current_trace_id()` is `None`, or `_in_cache_op` is set, call the original and return. No other work happens.

### Alias resolution

A backend instance does not know its alias. On first use, the wrapper checks `caches[alias] is self` for each alias in `settings.CACHES` and stores the result on the instance as `_inspector_alias`. This handles two aliases sharing one class. Instances built by hand resolve to `None`.

### Event types and metadata

`CACHE_EVENT_TYPES = ("cache.get", "cache.set", "cache.add", "cache.delete", "cache.clear", "cache.get_many", "cache.set_many", "cache.delete_many")`

Common fields on every cache event:

| Field | Value |
|---|---|
| `operation` | `"get"`, `"set"`, … |
| `alias` | `CACHES` alias or `None` |
| `backend` | Dotted path of the backend class |
| `duration_ms` | Wall time of the backend call, rounded to 2 dp |
| `origin_file`, `origin_line`, `origin_function` | First app frame, from `extract_origin()` |
| `error` | `"ExcType: message"` if the backend raised, else absent |

Per-operation fields:

| Operation | Fields |
|---|---|
| `get` | `key`, `hit`. The original is called with a private sentinel as `default`. `hit = result is not sentinel`. On a miss, the caller's `default` is returned. A stored `None` is a hit. |
| `set` | `key`, `ttl_seconds`, `value_type`, `value_size_bytes` |
| `add` | as `set`, plus `stored` (the return value) |
| `delete` | `key`, `deleted` (the return value) |
| `clear` | `key: "*"` |
| `get_many` | `keys`, `key_count`, `hit_count` (= `len(result)`), `miss_count` |
| `set_many` | `keys`, `key_count`, `ttl_seconds`, `failed_keys` (the return value) |
| `delete_many` | `keys`, `key_count` |

Field rules:
- `ttl_seconds`: the passed `timeout`. When it is `DEFAULT_TIMEOUT`, use the instance's `default_timeout`. `None` means the entry never expires.
- `value_size_bytes`: `len(value)` for `bytes`/`bytearray`, `len(value.encode("utf-8"))` for `str`, otherwise `None`. `value_type` is `type(value).__name__`. **Values are never recorded.**
- Keys are recorded as the caller passed them, not the prefixed or versioned backend key. `keys` is capped at 100 entries. `key_count` is always the full count. Keys go through masking like any other metadata.

## Template watcher

### Hooking

`install_hooks()` saves `Template._render` and replaces it with a wrapper. `remove_hooks()` restores the saved function. This covers:
- top-level renders (`django.template.backends.django.Template.render` → `Template.render` → `_render`)
- `{% include %}` (`IncludeNode` → `Template.render`)
- `{% extends %}` parents (`ExtendsNode` → `compiled_parent._render`)
- inclusion tags

It only works with the Django template language. Jinja2 is deferred (TMPL-05).

### Render tree state

- One `ContextVar` holds `{"trace_id", "next_id", "stack"}`. When the stored `trace_id` differs from the current one, the state is replaced with a fresh one. No explicit reset hook is needed.
- On entry, the render takes `render_id = next_id`, `next_id` is incremented, and the id is pushed onto the stack.
- On exit (in a `finally`), the id is popped and the event is recorded.
- `parent_id` is the stack top at entry, and `depth` is the stack length at entry.

`parent_id` reflects **runtime nesting**, not where the tag sits in the source. A child's `{% block %}` content renders while its base template is rendering. So an `{% include %}` written inside a child's block gets the **base** template as its parent. This is documented behaviour, not a bug.

### Event `template.rendered`

| Field | Value |
|---|---|
| `render_id` | 1-based, render order within the trace |
| `parent_id` | `render_id` of the enclosing render, or `None` |
| `depth` | 0 for top level |
| `relation` | `None` at top level. `"extends"` if the parent template's top-level nodelist contains an `ExtendsNode` (an extending template renders nothing directly except its base). Otherwise `"include"`. |
| `name` | `template.name`, or `None` for `from_string` templates |
| `origin_path` | `template.origin.name` |
| `loader` | Dotted path of `template.origin.loader`'s class, or `None` |
| `duration_ms` | Inclusive of nested renders, rounded to 2 dp |
| `context_key_count` | Number of keys in `context.flatten()`, excluding `True`, `False`, `None` |
| `context_keys` | Sorted key names, capped at 50. **Names only, never values.** |
| `error` | `"ExcType: message"` if rendering raised, else absent |

To compute `relation`, the parent template object is kept on the stack alongside its id.

## Dashboard

All routes are wrapped in `inspector_required` and follow the Queries/Exceptions pattern: function views, `Paginator(qs, 25)`, and an HTMX partial response when the `HX-Request` header is present.

| Route | Name | View | Queryset / filters |
|---|---|---|---|
| `cache/` | `cache-list` | `cache_list` | `event_type__in=CACHE_EVENT_TYPES`. Filters: `operation`, `alias`, `result` (`hit`/`miss` → `metadata__hit`), `key` (`metadata__key__icontains`). |
| `cache/<pk>/` | `cache-detail` | `cache_detail` | Event + parent `request.completed` |
| `templates/` | `templates-list` | `templates_list` | `event_type="template.rendered"`. Filters: `name` (icontains), `top_level` (`metadata__depth=0`). |
| `templates/<pk>/` | `template-detail` | `template_detail` | Event + parent request + parent render + child renders (same trace, `metadata__parent_id == render_id`) |

- `event_type__in` rather than `startswith`, so the event-type index is used on PostgreSQL.
- HTML templates go in `inspector/cache/{list,_table,detail}.html` and `inspector/template_renders/{list,_table,detail}.html`.
- `_sidebar.html` gains **Cache** and **Templates** links, with active-state handling.

**Request detail** (`request_detail` view and `requests/detail.html`):
- Summary adds `cache_ops`, `cache_hits`, `cache_misses` and `template_renders`.
- Timeline and waterfall show readable labels and colour modifiers for `cache.*` and `template.rendered`.
- The tabs view gains **Cache (n)** and **Templates (n)** tabs.
- New CSS modifiers live in `base.html` next to the existing ones.

## Error handling

Each wrapper has three stages:

1. **Pre:** fast-path checks and metadata that can be computed before the call, inside `try/except`.
2. **Call:** the original method, **outside** any inspector `try`. Its return value and exceptions reach the host unchanged. If it raises, the exception is captured into `error`, the event is recorded, and the exception is re-raised with a bare `raise`.
3. **Post:** building the event and calling `record()`, inside `try/except`.

- An inspector failure in the pre or post stage → `logger.warning("inspector: …", exc_info=True)`. It is re-raised only when `INSPECTOR_RAISE_ERRORS` is on. If the pre stage fails, the wrapper still calls the original, without recording.
- Only `logging.getLogger("django_inspector")` is used. No `print`.

## Testing

TDD throughout. Suite command: `uv run --extra dev python -m pytest`.

**Test setup changes:**
- `tests/settings.py` gains `TEMPLATES` (DjangoTemplates, `APP_DIRS=True`, `DIRS=[tests/templates]`) and `ROOT_URLCONF = "tests.urls"`.
- New `tests/urls.py` mounts `inspector/` → `django_inspector.dashboard.urls`.
- New `tests/templates/` holds the filesystem template used for the `origin_path` test.
- The existing suite must stay green.

**`tests/test_cache_watcher.py`**
- Lifecycle:
  - enable/disable are idempotent
  - originals are restored by identity
  - wrappers on inherited methods are deleted
  - off by default
- Capture:
  - each operation's fields
  - `hit` is true for a stored `None`
  - the caller's `default` is returned on a miss
  - default vs explicit TTL
  - value size for `str`, `bytes` and `dict`
  - `*_many` counts, `failed_keys` and the 100-key cap
- Re-entrancy: a backend whose `get_many` falls back to `get` yields exactly one event.
- Alias resolution: two aliases on one LocMem class.
- Trace correlation, and no event without a trace.
- A backend error is recorded in `error` and re-raised unchanged.
- `aget` under a trace records `cache.get`.
- A sensitive-looking key is masked.

**`tests/test_template_watcher.py`**
- Lifecycle: `_render` is restored, and the watcher is off by default.
- Capture:
  - `name`, `origin_path`, `loader`, `duration_ms`, `context_keys` and the count
  - `from_string` templates have `name` of `None`
- Render tree:
  - an extends chain gives `relation="extends"`
  - an include gives `relation="include"` and the right `depth`
  - an include inside a child block gets the base as its parent
  - `render_id` values are sequential per trace and restart for a new trace
- A render error is recorded and re-raised.
- Trace correlation, the no-trace case, and rendering through `sync_to_async`.
- Most tests use a locmem-loader `Engine`. One uses `tests/templates/` for a real `origin_path`.

**`tests/test_dashboard_pages.py`** (new; staff user via `RequestFactory`)
- Cache and template list and detail pages render.
- Each filter narrows results.
- An HTMX request returns the partial.
- Template detail shows the parent and child renders.
- Request detail shows the cache and template summary counts and tabs.
- The sidebar has the new links.

**Middleware regression** (`tests/test_ignores.py`): a request under `DASHBOARD_URL_PREFIX` produces no events even when the SQL and template watchers are on.

## Out of scope (deferred)

- **Event timestamps:** `Event.timestamp` is `auto_now_add`, set at flush, so events in one trace share roughly the same timestamp and the waterfall offsets are meaningless. That needs its own fix, and a capture-time timestamp needs a migration. Template relationships use `render_id`/`parent_id` instead.
- Jinja2 templates (TMPL-05).
- Cache `has_key`, `incr`, `decr`, `touch`, and value sizes for non-`str`/`bytes` values.
- Serialized size of the whole template context (TMPL-03's optional part).
- The MASK-05 fix (the unmasked fallback in `BaseWatcher.record`). This is a separate small fix, not blocked by this phase.

## Done when

- All CACHE-01..07 and TMPL-01..05 acceptance points above are covered by passing tests. The full suite is green.
- Roadmap Phase 5 success criteria 1–6 hold, with criterion 6's "respect `SAMPLING_RATE`" provided by the middleware's buffer clear.
- The docs are updated:
  - `docs/roadmap.md`: Phase 5 work items ticked
  - `docs/requirements.md`: statuses updated
  - `docs/project.md`: cache and template watchers moved to Validated
  - `CLAUDE.md`: "Next up" set to Phase 6
