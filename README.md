# django-inspector

Runtime observability for Django: a Laravel Telescope-style dashboard for your own app.

django-inspector records what happens during each request: the request itself, SQL queries, exceptions, cache calls, template renders, signal dispatches and log records. Every event carries the request's **trace id**, so one page shows a request's full story. You browse it in a built-in dashboard (server-rendered, HTMX, no JavaScript build step).

- **One trace, full story.** Every event is tied to the request that caused it. This works under WSGI and ASGI, including concurrent async requests.
- **Production-minded:**
  - Sensitive values are masked before storage.
  - Requests can be sampled; errors and slow requests are always kept.
  - The dashboard is staff-only by default.
  - An inspector bug never breaks your request.
- **No extra dependencies.** Django is the only runtime requirement.

## Requirements

- Python 3.8+
- Django 4.2 or 5.x. Tested on 4.2 (Python 3.8–3.12) and 5.2 (Python 3.10–3.13). Django 4.0/4.1 are expected to work but aren't tested.

## Installation

```bash
pip install django-inspector
```

> Until the first PyPI release, install from GitHub instead:
> `pip install git+https://github.com/poovarasudev/django-inspector.git`

**1. Add the app and the middleware.** Put the middleware **first**, so everything that runs after it (including other middleware) is part of the trace:

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "django_inspector",
]

MIDDLEWARE = [
    "django_inspector.middleware.InspectorMiddleware",
    # ... the rest of your middleware
]
```

**2. Mount the dashboard.** The prefix must match `DASHBOARD_URL_PREFIX` (default `inspector/`). Requests under it are never traced, so browsing the dashboard doesn't record anything.

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    # ...
    path("inspector/", include("django_inspector.dashboard.urls")),
]
```

**3. Create the table:**

```bash
python manage.py migrate django_inspector
```

Then open `/inspector/` while logged in as a staff user.

## Watchers

Each watcher can be turned on or off under `WATCHERS`. The noisier ones are off by default.

| Watcher | Key | Default | Records |
|---|---|---|---|
| Request | `"request"` | on | method, path, URL, query, headers, body (up to `MAX_BODY_SIZE`), status, latency, user, session, client IP |
| SQL | `"sql"` | on | SQL, parameters, duration, database alias, the app code that ran it, slow flag, N+1 and duplicate detection |
| Exception | `"exception"` | on | type, message, stack with locals, chained causes |
| Log | `"log"` | on | log records at `LOG_LEVEL_THRESHOLD`+: logger, level, message, file, line, traceback |
| Cache | `"cache"` | off | `get`/`set`/`add`/`delete`/`clear` and the `*_many` variants: key, alias, backend, hit/miss, TTL, value size (never the value) |
| Template | `"template"` | off | each render with name, source path, time, context key names (never values) and its place in the extends/include tree |
| Signal | `"signal"` | off | dispatches of the signals in `SIGNAL_WATCH_LIST`: sender, receivers in call order with per-receiver time and errors |

```python
DJANGO_INSPECTOR = {
    "WATCHERS": {"cache": True, "template": True, "signal": True},
}
```

Keys you set under `WATCHERS` are merged with the defaults, so you only list what you change.

Two watchers have scope limits worth knowing:
- **Log watcher.** It adds one handler to the root logger and never changes your logging config. So it only sees records that reach the root logger: loggers with `propagate=False` aren't captured, and nor are records below a logger's own level.
- **Signal watcher.** It only watches the signals you list. By default that's the five model signals: `pre_save`, `post_save`, `pre_delete`, `post_delete` and `m2m_changed`.

## Configuration

Every setting goes in one dict, and every key is optional:

```python
DJANGO_INSPECTOR = {
    "SAMPLING_RATE": 0.1,
    "IGNORE_PATHS": [r"^/healthz$", r"^/metrics$"],
    "IGNORE_EXCEPTIONS": ["django.http.Http404"],
    "SENSITIVE_KEYS": ["ssn", "iban"],
}
```

| Setting | Default | Meaning |
|---|---|---|
| `INSPECTOR_ENABLED` | `True` | Master switch. `False` disables tracing, recording and watcher installation. |
| `WATCHERS` | see [Watchers](#watchers) | Turn individual watchers on or off. |
| `DASHBOARD_URL_PREFIX` | `"inspector/"` | Where `dashboard.urls` is mounted. Requests under it are never traced. |
| `SQL_SLOW_THRESHOLD_MS` | `100` | Queries at or above this duration are flagged as slow. |
| `SQL_CAPTURE_PARAMS` | `True` | Store bound SQL parameters. They can hold secrets (for example a token being inserted) and are only masked by value pattern, not by column name. Set `False` to store the SQL text only. |
| `MAX_BODY_SIZE` | `8192` | Request and response bodies are truncated to this many bytes. |
| `MAX_EVENTS_PER_TRACE` | `1000` | Most events stored per request. Later ones are dropped and counted in the request's `events_dropped`; the request event itself is always kept. `0` means no limit. |
| `SENSITIVE_KEYS` | `[]` | Extra keys to redact, added to the built-in list (password, token, secret, api_key, authorization, cookie, csrf, session, …). |
| `SAMPLING_RATE` | `1.0` | Fraction of successful requests to keep (0.0–1.0). 5xx responses are always kept. |
| `EARLY_SAMPLING` | `False` | Decide sampling when a request starts instead of when it ends. Requests that aren't picked then skip SQL, cache, template, signal and log capture entirely (much cheaper), but a failed or slow one is still kept with its request and exception events. |
| `SLOW_REQUEST_THRESHOLD_MS` | `1000` | Requests at or above this latency are always kept, whatever the sampling rate. |
| `INSPECTOR_DASHBOARD_PERMISSION` | `None` | Dotted path to a `(request) -> bool` callable. `None` means the user must be `is_staff`. |
| `INSPECTOR_DASHBOARD_IP_ALLOWLIST` | `[]` | IP addresses or CIDR ranges allowed to see the dashboard. Empty means no IP restriction. |
| `TRUSTED_PROXY_COUNT` | `0` | Number of reverse proxies in front of Django. `X-Forwarded-For` is trusted for this many hops only; `0` uses `REMOTE_ADDR`. Used for the recorded client IP and the IP allowlist. |
| `IGNORE_PATHS` | `[]` | Regexes matched against `request.path`. Matching requests aren't traced at all. |
| `IGNORE_EXCEPTIONS` | `[]` | Dotted exception class names the exception watcher skips. |
| `SIGNAL_WATCH_LIST` | the five model signals | Dotted paths of the signals the signal watcher captures. |
| `LOG_LEVEL_THRESHOLD` | `"WARNING"` | Minimum level the log watcher records: a level name or number. |
| `INSPECTOR_RAISE_ERRORS` | `False` | Re-raise the inspector's own internal errors instead of logging them. Useful in development and tests. |

### Configuration checks

`manage.py check` (and `runserver`/`migrate`) validates the configuration: unknown keys (with a "did you mean" hint), out-of-range or wrongly typed values, invalid regexes and IP ranges, unimportable dotted paths, a missing or misplaced middleware, a `DASHBOARD_URL_PREFIX` that doesn't match where the dashboard is mounted, and a production dashboard protected by `is_staff` alone. Warnings use the ids `django_inspector.W001`–`W008` and can be silenced with `SILENCED_SYSTEM_CHECKS`.

## Dashboard access

By default, only staff users (`user.is_staff`) can open the dashboard; everyone else gets a 403. You can tighten or replace that check:

```python
DJANGO_INSPECTOR = {
    # Any (request) -> bool callable, e.g. a superuser-only check:
    "INSPECTOR_DASHBOARD_PERMISSION": "myproject.permissions.can_view_inspector",
    # Also require the client IP to be in one of these ranges:
    "INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["10.0.0.0/8", "127.0.0.1"],
}
```

The allowlist checks `REMOTE_ADDR` unless you set `TRUSTED_PROXY_COUNT` to the number of reverse proxies in front of Django. `X-Forwarded-For` is never trusted beyond those hops, so a client can't spoof its way past the allowlist.

When both are set, the request must pass both. When `DEBUG = False` and neither is set, the system check `django_inspector.W008` warns you: in production, staff status alone is often not the protection you want.

The dashboard has these pages:
- **Live Feed**
- **Requests**: each request has a timeline, tabs and a waterfall view of everything in its trace.
- **Queries**, **Cache**, **Templates**, **Logs**, **Signals** and **Exceptions**: each page has filters.

## Production notes

- **Masking.** Before an event is stored, keys matching the sensitive list are redacted anywhere in it. Values holding a Luhn-valid card number or a JWT are also redacted, whatever their key. Masking fails closed: if it ever raises, the event is stored as a `masking_failed` placeholder instead of the original data. Form and JSON bodies (request and response) are parsed and masked before they are stored, and `@sensitive_post_parameters` is honoured. Multipart bodies are only summarised when your view parsed them, binary bodies are stored as a size summary, and the recorded full URL has its sensitive query parameters masked.
- **Sampling.** Use `SAMPLING_RATE` to limit how much is stored. Errors and slow requests are always kept, so the interesting ones survive. By default every request is captured and the unpicked ones are discarded at the end; set `EARLY_SAMPLING = True` to also skip the capture work for them (their errors and slow requests keep only the request and exception events).
- **Retention.** Events accumulate until you delete them. Schedule the cleanup command, for example hourly from cron:

  ```bash
  python manage.py inspector_cleanup --hours 24      # delete events older than 24 hours
  python manage.py inspector_cleanup --dry-run       # show how many would be deleted
  ```

- **ASGI.** The middleware handles async requests natively. Database writes and the lazy `request.user` stay in a sync context, and concurrent requests never share trace state.
- **Failure isolation.** The inspector's own errors are caught and logged to the `django_inspector` logger at WARNING, and your request carries on. Set `INSPECTOR_RAISE_ERRORS = True` to surface them while developing.
- **Storage.** Events are buffered in memory and written with one `bulk_create` at the end of each request, into a single `Event` table (`trace_id`, `event_type`, `timestamp`, JSON `metadata`).

## Development

```bash
uv run --extra dev python -m pytest          # the test suite
uv build && python scripts/check_dist.py dist/   # build and check the wheel/sdist contents
```

Planning docs live in `docs/`: the roadmap, requirements, and a spec and plan per feature under `docs/superpowers/`.

## License

MIT. See [LICENSE](LICENSE).
