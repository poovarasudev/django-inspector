# Integrations

> **Snapshot:** written at v1.0 (2026-05-23), before Phase 4 added `masking.py`, `sampling.py`, `ignores.py`, `dashboard/auth.py` and the ContextVar buffers. Check the code before relying on details here, and refresh this file when you touch the area it describes.

How `django-inspector` plugs into the host Django project, and what external surfaces it touches.

## Host Django Project Integration Points

### 1. INSTALLED_APPS

Host adds `"django_inspector"` to `INSTALLED_APPS`. `DjangoInspectorConfig.ready()` runs at startup and:

1. Reads `inspector_settings.is_enabled` — bails immediately if disabled.
2. Imports watcher modules to trigger self-registration.
3. Instantiates each registered watcher and calls `enable()` if `WATCHERS[name]` is true.
4. Stores instances on `app._watcher_instances` for later lookup by middleware.

### 2. MIDDLEWARE

Host adds `"django_inspector.middleware.InspectorMiddleware"` at the top of `MIDDLEWARE`. Per request:

- Generates a UUID4 hex trace id.
- Sets it on the `ContextVar` via `set_trace_id()`.
- Pins it onto `request.inspector_trace_id` for downstream code.
- Calls `RequestWatcher.on_request()` (start) and `on_response()` (end).
- Flushes the event buffer to DB and clears the SQL query log in a `finally`.

### 3. URLconf

Host wires the dashboard:

```python
# host project urls.py
path("inspector/", include("django_inspector.dashboard.urls")),
```

The URL prefix is also reflected in `inspector_settings.DASHBOARD_URL_PREFIX` (default `"inspector/"`) so the request watcher can skip its own traffic.

### 4. Settings dictionary

Host configures via `settings.DJANGO_INSPECTOR = {...}`. Current keys:

| Key | Default | Purpose |
|-----|---------|---------|
| `INSPECTOR_ENABLED` | `True` | Master kill switch |
| `WATCHERS` | `{"request": True, "sql": True, "exception": True}` | Per-watcher enable map |
| `DASHBOARD_URL_PREFIX` | `"inspector/"` | Skip-pattern for self-traffic |
| `SQL_SLOW_THRESHOLD_MS` | `100` | Threshold for `is_slow` flag |
| `MAX_BODY_SIZE` | `8192` | Bytes captured from request/response body |

Defaults defined in `django_inspector/conf.py`; user values shallow-merge over defaults (with a one-level deep merge for `WATCHERS`).

## Django Internal Hooks Used

| Hook | Used by | Purpose |
|------|---------|---------|
| `MIDDLEWARE` callable chain | `InspectorMiddleware` | Per-request lifecycle |
| `connection.execute_wrappers` (Django ≥ 2.0 instrumentation API) | `SQLWatcher` | Intercept every query without monkey-patching |
| `django.db.backends.signals.connection_created` | `SQLWatcher` | Attach instrumentation to connections created after startup |
| `django.core.signals.got_request_exception` | `ExceptionWatcher` | Capture unhandled exceptions raised during request handling |
| `django.core.management.BaseCommand` | `inspector_cleanup` | Retention command |
| `django.db.models.Model` + `JSONField` | `Event` model | Single-table event store |
| `django.shortcuts.render`, `get_object_or_404`, `core.paginator.Paginator` | Dashboard views | Render + paginate |

Notably, `django-inspector` does **not** monkey-patch Django. SQL capture goes through the official `execute_wrappers` API, exception capture goes through a signal, and request capture goes through middleware. This makes Django upgrades low-risk.

## Frontend Integration

- Server-rendered templates extending `inspector/base.html`.
- HTMX swaps:
  - `live_feed` view returns `_live_feed_table.html` when `HX-Request` header present.
  - `requests_list`, `queries_list`, `exceptions_list` likewise return `_table.html` partials.
  - Implies host page has HTMX loaded (CDN or static) and the dashboard's `base.html` includes the HTMX script tag.

## Third-Party Integrations Not Yet Present

Per PRD, the following are deferred:

| Integration | PRD Section | Status |
|-------------|-------------|--------|
| Celery (task watcher) | §11.12 | Not started |
| Redis (cache/redis watcher) | §11.5, §11.15 | Not started |
| Django Channels | §11.17 | Not started |
| Django REST Framework | §11.16 | Not started |
| `requests`/`httpx`/`aiohttp` HTTP client watcher | §11.14 | Not started |
| Scheduler (beat/cron) | §11.13 | Not started |
| Email backend hook | §11.9 | Not started |
| Sentry / structlog / ELK exporters | §1 (problem space) | Not started |
| OpenTelemetry exporter | §5 / §19 v2.0 | Out of scope for now |

## How Host Apps See the Inspector

From outside the package, the integration surface is:

- 1 settings key (`DJANGO_INSPECTOR`)
- 1 INSTALLED_APP entry
- 1 MIDDLEWARE entry
- 1 URL include
- 1 management command (`python manage.py inspector_cleanup`)
- 1 attribute attached to every request (`request.inspector_trace_id`)

No new template tags, no new model fields injected into host models, no signal handlers fired into host code.
