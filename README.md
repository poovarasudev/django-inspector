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
- Django 4.2, 5.x or 6.x. Tested on 4.2 (Python 3.8–3.12), 5.2 (Python 3.10–3.14), and 6.0 and 6.1 (Python 3.12–3.14), on SQLite and PostgreSQL. Django 4.0/4.1 are expected to work but aren't tested.

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
| Command | `"command"` | on | commands listed in `COMMAND_WATCH_LIST`: name, args, options, bounded stdout/stderr, duration, exit code — see [Tracing management commands](#tracing-management-commands) |
| Mail | `"mail"` | off | recipients, subject, text/HTML bodies (up to 64K characters each), headers, backend, attachment filenames/types/sizes (never contents), outcome — see [Viewing sent mail](#viewing-sent-mail) |
| HTTP | `"http"` | on | outgoing `requests`/`httpx` calls (sync and async): method, masked URL, host, status, duration, redirects, response size, error — see [Outgoing HTTP calls](#outgoing-http-calls) |
| Celery | `"celery"` | on | every task sent and run: task name, masked args/kwargs, queue, ETA, retries, worker, result or exception — see [Celery tasks](#celery-tasks) |

```python
DJANGO_INSPECTOR = {
    "WATCHERS": {"cache": True, "template": True, "signal": True},
}
```

Keys you set under `WATCHERS` are merged with the defaults, so you only list what you change.

Two watchers have scope limits worth knowing:
- **Log watcher.** It adds one handler to the root logger and never changes your logging config. So it only sees records that reach the root logger: loggers with `propagate=False` aren't captured, and nor are records below a logger's own level.
- **Signal watcher.** It only watches the signals you list. By default that's the five model signals: `pre_save`, `post_save`, `pre_delete`, `post_delete` and `m2m_changed`.

## Tracing code outside requests

Every request already gets its own trace, but background jobs, management commands and cron scripts don't. `trace()` starts one anywhere:

```python
from django_inspector import trace

with trace("nightly-report"):
    ...

async with trace("sync-prices"):
    ...

@trace("send-digest")
def send_digest():
    ...

@trace("refresh-cache")
async def refresh_cache():
    ...
```

Whatever runs inside the block — SQL queries, log records at `LOG_LEVEL_THRESHOLD`+, a raised exception — is captured under one trace id, exactly like a request. A `trace()` opened while a trace is already active (nested calls, or one started inside a request) joins the active one instead of starting a new trace: no extra flush, no extra anchor event, and `trace()` (as a context manager) returns the outer trace id.

A traced block is sampled the same way a request is: `SAMPLING_RATE` decides successful runs, and a run that raised or took at least `SLOW_REQUEST_THRESHOLD_MS` is always kept. An exception is recorded (type, message, stack with locals, chained causes — the same as an unhandled request exception) and then re-raised unchanged: `trace()` never hides or replaces your exception, even if something inside the inspector itself fails while finishing up.

**Async code.** Use `async with trace(...):` or `@trace(...)` on an `async def` function inside async code — not plain `with trace(...):`, which does its database write synchronously and will raise Django's `SynchronousOnlyOperation` there (caught and logged like any other inspector failure, so your code still runs, but nothing is recorded).

### Finding your way back to a trace

A request's response can carry its trace id in a header, and every log record can carry the current trace id too:

- **`X-Inspector-Trace` header.** Set via `TRACE_HEADER`: `"debug"` (default, only when `settings.DEBUG` is on), `True` (always) or `False` (never). Only added to a *kept* trace — an unkept one has nothing to show at its id.
- **Logging filter.** `django_inspector.tracing.TraceIdFilter` sets `record.inspector_trace_id` (the current trace id, or `"-"` outside a trace) on every record it sees:

  ```python
  LOGGING = {
      "version": 1,
      "filters": {"trace_id": {"()": "django_inspector.tracing.TraceIdFilter"}},
      "formatters": {"plain": {"format": "%(levelname)s [%(inspector_trace_id)s] %(message)s"}},
      "handlers": {
          "console": {
              "class": "logging.StreamHandler",
              "filters": ["trace_id"],
              "formatter": "plain",
          },
      },
      "root": {"handlers": ["console"], "level": "INFO"},
  }
  ```

## Tracing management commands

`trace()` covers a script you write yourself; the command watcher covers `manage.py`/`django-admin` commands and `call_command()` for you, with no code changes to the command itself. It's opt-in and empty by default:

```python
DJANGO_INSPECTOR = {
    "COMMAND_WATCH_LIST": ["backfill_prices", "send_digest"],
}
```

A command not in that list runs untraced. A handful of names are **never traced even when listed** — `runserver`, `runserver_plus`, `testserver`, `test`, `shell`, `shell_plus`, `dbshell`, `migrate` and `inspector_cleanup` — since tracing them would be noisy at best and self-referential at worst; listing one anyway triggers the system check `django_inspector.W009`.

A traced run gets a `command.completed` anchor with its name, args, options (masked, `stdout`/`stderr`/`_`-prefixed keys dropped), the first 8192 characters of stdout and stderr (masked — the command's real streams still get everything), duration and exit code (`0` on success; `CommandError`'s `returncode`; `SystemExit`'s code, with `None` or `0` meaning success; `130` for `KeyboardInterrupt`; `1` for anything else), plus its SQL, log and exception events, exactly like a request. A command run inside an active trace (another `trace()`, a request, or another traced command) joins it instead of starting its own — its `command.completed` anchor lands in the outer trace, without an early flush.

**Long commands.** A traced command's buffer flushes every 500 events instead of holding everything until the end, so memory stays flat and a killed command keeps whatever it already flushed (the trace page shows it with an "incomplete trace" notice — no anchor was ever recorded). `MAX_EVENTS_PER_TRACE` still applies to a command's total event count; set it to `0` for unlimited, chunked capture on a command that legitimately produces very many events.

## Viewing sent mail

The mail watcher is **off by default**: email bodies routinely hold password-reset and sign-in links whose tokens no masking rule can recognise. Turn it on deliberately:

```python
DJANGO_INSPECTOR = {
    "WATCHERS": {"mail": True},
}
```

It patches `django.core.mail.get_connection`, so `send_mail`, `send_mass_mail`, `mail_admins`, `mail_managers` and `EmailMessage.send()` are all covered — one `mail.sent` event per message, recorded after the backend's `send_messages` returns or raises. A message sent through a backend you instantiated directly (bypassing `get_connection()`) isn't captured.

What's stored: recipients and subject, the text and HTML bodies (each capped at 64K characters and masked like any other event — `SENSITIVE_KEYS` and the card/JWT scan both apply), headers (masked by key), the backend and message class, and each attachment's filename, MIME type and size — **never its contents**. The outcome is `"sent"`, `"failed"` (the backend reported fewer messages sent than you gave it — used with `fail_silently`) or `"error"` (the backend raised; your exception is always re-raised unchanged).

The Mail page (`/mail/`) lists sent messages with to/subject/status filters and CSV export. Its detail page shows the message as it would arrive: a **Text** tab, a **Headers** tab, and an **HTML** tab that embeds the stored body in a sandboxed, same-origin `<iframe>` (no `allow-scripts`, no `allow-same-origin`) pointed at a dedicated preview endpoint with its own locked-down `Content-Security-Policy` (`default-src 'none'; sandbox; frame-ancestors 'self'`) — the HTML is never rendered inline in the dashboard's own page, since it's attacker-influenced content. **Download .eml** rebuilds the message from its stored fields (no connection, nothing is sent) as a downloadable `message/rfc822` file; since attachment contents were never stored, they're listed in an `X-Inspector-Omitted-Attachments` header instead of included.

## Outgoing HTTP calls

The HTTP watcher is **on by default**: it only ever stores a masked URL and call metadata, never headers or bodies. It patches `requests.Session.send` and `httpx.Client.send`/`httpx.AsyncClient.send` on the class, so every session or client — including the `requests.get()`-style module helpers, and `get`/`post`/`stream`/`request` on either library — goes through one hook per library. Neither `requests` nor `httpx` is a dependency: each is imported lazily when the watcher enables, a missing one is skipped, and the watcher still enables for the other.

One `http.request` event per `send()` call: library, method, masked URL and host, status code, duration, redirect count (`len(response.history)` — redirects the library follows internally still count as one call), response size (`Content-Length`, or the body length when it was already read and the call wasn't streaming), a `streamed` flag, the origin file/line/function, and — on a failure — an error (`"Type: message"`, masked) with the original exception always re-raised unchanged.

**Retries aren't captured**: `urllib3`'s adapter-level retries and `httpx`'s transport-level retries are invisible at `send()`, so each `send()` call is one event regardless of how many attempts happened underneath it.

**Streaming.** When a call passes `stream=True`, the event's `streamed` flag is set and its duration covers only up to receiving the headers — the body is never forced to be read for byte counting, so `response_bytes` comes from `Content-Length` only (or `None` without that header).

**URL masking.** Query parameters whose names match the sensitive-key rules are replaced with `***REDACTED***`, userinfo (`user:pass@`) is dropped, and the whole URL is then run through the same JWT/card-number scan as any other value — so a token in the path is caught too. The masked URL is what's stored, shown and exported; nothing unmasked ever reaches the dashboard.

The HTTP page (`/http/`) lists calls with host/status-class/"errors only" filters and CSV export; its detail page shows every field, the origin, and a link back to the parent request or trace. The Issues page's **Slow hosts** section (below) ranks hosts by p95 latency.

## Celery tasks

The Celery watcher is **on by default** and does nothing when Celery isn't installed — there's no setup beyond having both `django_inspector` and your Celery app configured. It connects five Celery signals (`before_task_publish`, `task_prerun`, `task_failure`, `task_retry`, `task_postrun`); it never patches Celery's own classes, and `celery` is never imported until the watcher enables.

**Linked, not nested, traces.** A task is sent before the sender's keep/drop sampling decision is made, so nesting its events inside the sender's trace would orphan them if that trace were dropped. Instead:
- The trace that calls `.delay()`/`.apply_async()` records a `task.sent` event (task name, queue, ETA, retries) and the task's message carries that trace's id in an `inspector_trace_id` header.
- The worker gives the task's own run its own trace, anchored by a `task.completed` event whose `parent_trace_id` points back to the sender. Each is **sampled independently** with `SAMPLING_RATE` (failures and slow runs are always kept) — a task's trace can be kept while its sender's wasn't, or vice versa; the dashboard says so when a link points at a trace that isn't stored.

**What's captured** on `task.completed`: task name, task id, state (`SUCCESS`, `FAILURE`, or `RETRY` while retrying), retries, worker hostname, queue, ETA, masked and bounded args/kwargs, a masked result `repr()` (success only, capped at 500 characters) or a masked `"Type: message"` exception — plus the task's own SQL, log and exception events, exactly like a request. A genuine failure also produces an `exception.raised` event in the task's trace; a retry does not.

**Eager mode.** With `task_always_eager`, the task runs inline on the sender's thread — it joins the sender's trace instead of starting its own, and its `task.completed` anchor lands there (no separate `parent_trace_id` link needed, since it's the same trace).

**The `[celery]` extra** (`django-inspector[celery]`) pins `celery>=5.2` for documentation purposes only — the watcher's code never imports Celery until it enables, so it's never a hard dependency.

The Traces page lists task traces alongside `trace()` runs and commands (filterable by type); a task's page shows its state, retries, worker, queue, args/kwargs, result/exception and a "Sent by" link to the sender's trace. A request's or trace's own detail page lists the tasks it sent under a **Tasks** tab/section, each linking to its task trace when one was kept.

## Catching N+1 queries in tests

`django_inspector.testing.assert_queries()` fails a test that breaks a query budget — no trace, middleware, buffer or database write is involved, so it works whether or not the inspector's own watchers are installed:

```python
from django.test import TestCase
from django_inspector.testing import assert_queries

class OrderViewTests(TestCase):
    def test_order_list_has_no_n_plus_one(self):
        with assert_queries(max=5, no_n_plus_one=True, no_duplicates=True) as guard:
            self.client.get("/orders/")
        assert len(guard.queries) <= 5  # guard.queries: CapturedQuery(sql, params, alias, duration_ms, file, line, function)
```

It also works as a function decorator (sync or async), a `TestCase` method decorator, and a `TestCase` class decorator (every attribute starting with `test` that's callable is wrapped):

```python
@assert_queries(max=3)
def test_view(client):
    client.get("/orders/")

@assert_queries(no_n_plus_one=True)
class OrderViewTests(TestCase):
    def test_list(self):
        ...
    def test_detail(self):
        ...
```

A failing budget raises `QueryBudgetExceeded` (an `AssertionError`) naming each offending query shape, its count, and up to three `file:line (function)` origins:

```
Query budget exceeded: 1 N+1 shape

N+1 queries:
  3× SELECT * FROM "orders_lineitem" WHERE "order_id" = ?
    orders/views.py:42 (order_list)
```

Bound parameters are never printed in the message — they can hold secrets — but `guard.queries` carries them for your own assertions. `using` picks which database aliases are guarded: `None` (default) guards every alias in `connections`, a string guards one, and a list guards those.

**Thread note.** The guard only sees queries run on the thread it was entered on — the request/response cycle through Django's test client, and `sync_to_async(thread_sensitive=True)` code, both run there. Queries from another thread (e.g. a thread pool) aren't captured.

**pytest.** Installing django-inspector registers a `pytest11` entry point that adds an `assert_queries` fixture (the same factory, so you don't need to import it) and an `@pytest.mark.inspector_queries(max=..., no_n_plus_one=..., no_duplicates=..., using=...)` marker that wraps the whole test call in one guard:

```python
def test_order_list(client, assert_queries):
    with assert_queries(max=5):
        client.get("/orders/")

@pytest.mark.django_db
@pytest.mark.inspector_queries(max=5, no_n_plus_one=True)
def test_order_list(client):
    client.get("/orders/")
```

The marker still needs `pytest-django`'s `db`/`django_db` for database access; it doesn't grant it.

**Development mode.** The `N_PLUS_ONE` setting reports N+1 queries as your app runs, independently of `assert_queries`, only while `settings.DEBUG` is on:

```python
DJANGO_INSPECTOR = {"N_PLUS_ONE": "warn"}  # or "raise"
```

`"warn"` logs once per query shape (at its third occurrence) to the `django_inspector` logger; `"raise"` raises `NPlusOneDetected` at that point instead — the query itself has already run. This is a development aid, not a test tool: it only fires inside traced code (a request, `trace()` or a traced command) while `DEBUG` is on.

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
| `RETENTION_HOURS` | `None` | Delete events older than this many hours automatically. Pruning runs after a request's flush, at most once every 5 minutes per process and 1,000 rows at a time. `None` keeps events until you run `inspector_cleanup`. |
| `MAX_EVENTS_PER_TRACE` | `1000` | Most events stored per request. Later ones are dropped and counted in the request's `events_dropped`; the request event itself is always kept. `0` means no limit. |
| `SENSITIVE_KEYS` | `[]` | Extra keys to redact, added to the built-in list (password, token, secret, api_key, authorization, cookie, csrf, session, …). |
| `SAMPLING_RATE` | `1.0` | Fraction of successful requests to keep (0.0–1.0). 5xx responses are always kept. |
| `EARLY_SAMPLING` | `False` | Decide sampling when a request starts instead of when it ends. Requests that aren't picked then skip SQL, cache, template, signal and log capture entirely (much cheaper), but a failed or slow one is still kept with its request and exception events. |
| `SLOW_REQUEST_THRESHOLD_MS` | `1000` | Any trace — a request or a `trace()` run — at or above this duration is always kept, whatever the sampling rate. |
| `INSPECTOR_DASHBOARD_PERMISSION` | `None` | Dotted path to a `(request) -> bool` callable. `None` means the user must be `is_staff`. |
| `INSPECTOR_DASHBOARD_IP_ALLOWLIST` | `[]` | IP addresses or CIDR ranges allowed to see the dashboard. Empty means no IP restriction. |
| `TRUSTED_PROXY_COUNT` | `0` | Number of reverse proxies in front of Django. `X-Forwarded-For` is trusted for this many hops only; `0` uses `REMOTE_ADDR`. Used for the recorded client IP and the IP allowlist. |
| `IGNORE_PATHS` | `[]` | Regexes matched against `request.path`. Matching requests aren't traced at all. |
| `IGNORE_EXCEPTIONS` | `[]` | Dotted exception class names the exception watcher skips. |
| `SIGNAL_WATCH_LIST` | the five model signals | Dotted paths of the signals the signal watcher captures. |
| `LOG_LEVEL_THRESHOLD` | `"WARNING"` | Minimum level the log watcher records: a level name or number. |
| `INSPECTOR_RAISE_ERRORS` | `False` | Re-raise the inspector's own internal errors instead of logging them. Useful in development and tests. |
| `TRACE_HEADER` | `"debug"` | `X-Inspector-Trace` on a kept trace's response: `"debug"` sends it only when `settings.DEBUG` is on, `True` always sends it, `False` never does. |
| `COMMAND_WATCH_LIST` | `[]` | Management command names to trace — see [Tracing management commands](#tracing-management-commands). Empty means no command is traced. |
| `N_PLUS_ONE` | `"off"` | Dev-mode N+1 reporting while `DEBUG` is on — see [Catching N+1 queries in tests](#catching-n1-queries-in-tests). `"off"`, `"warn"` (log) or `"raise"` (raise `NPlusOneDetected`). |

### Configuration checks

`manage.py check` (and `runserver`/`migrate`) validates the configuration: unknown keys (with a "did you mean" hint), out-of-range or wrongly typed values, invalid regexes and IP ranges, unimportable dotted paths, a missing or misplaced middleware, a `DASHBOARD_URL_PREFIX` that doesn't match where the dashboard is mounted, a production dashboard protected by `is_staff` alone, and a `COMMAND_WATCH_LIST` entry that's never traced. Warnings use the ids `django_inspector.W001`–`W009` and can be silenced with `SILENCED_SYSTEM_CHECKS`.

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
- **Issues**: see [The Issues page](#the-issues-page) below.
- **Live Feed**
- **Requests**: each request has a timeline, tabs and a waterfall view of everything in its trace.
- **Traces**: `trace()` runs, traced commands and Celery task runs — name, status, duration — each with a timeline and waterfall on its own `/trace/<id>/` page (a command's page also shows its exit code, args, options and captured output; a task's page shows its state, retries, worker, queue, args/kwargs, result/exception and a "Sent by" link), filterable by type. A request's trace id also resolves there and redirects to its request page.
- **Queries**, **Cache**, **Templates**, **Mail**, **HTTP**, **Logs**, **Signals** and **Exceptions**: each page has filters and an "Export CSV" link — see [CSV export](#csv-export) below. Mail's detail page also has an HTML preview and a "Download .eml" button — see [Viewing sent mail](#viewing-sent-mail) above.

### Pinning a trace

A request's detail page has a "Pin trace" button next to its trace id. Pinning sets a flag on every event of that trace, which keeps it out of `RETENTION_HOURS` pruning and `inspector_cleanup` — useful for holding on to a specific bug report while everything else keeps expiring. Pinned requests show a "Pinned" badge in the requests list too; "Unpin trace" clears the flag.

### The Issues page

`/issues/` answers "what should I fix first?" with four tables, each linking to example traces:
- **Exceptions**, grouped by fingerprint (type plus the innermost application frame) — count, affected traces, first/last seen.
- **Query shapes**, grouped by normalised SQL — count, affected traces, p50/p95 duration, and how many traces hit an N+1; N+1 shapes sort to the top.
- **Endpoints**, grouped by `(method, route)` (the URL pattern, not the raw path, so `/orders/1/` and `/orders/2/` count as one) — p50/p95 duration, error rate and client-error rate.
- **Slow hosts**, outgoing HTTP calls grouped by host — count, p50/p95 duration, and an error rate (a recorded error or a 5xx status), sorted by p95 descending.

A `?window=` query parameter picks `1h`, `24h` (the default) or `7d`; an unrecognised value falls back to `24h`. Each section scans at most its newest 10,000 matching events, however big the table gets, and says so ("Based on the latest 10,000 …") when that cuts the window short. With `SAMPLING_RATE` below 1, a notice marks the counts as relative, not totals — they aren't scaled to compensate. The page is a snapshot: no live polling.

### CSV export

Every dashboard list exports to CSV: `?format=csv` on a list URL streams the current filtered queryset (timestamp, trace id, event type, duration, plus that list's own fields) as `text/csv`, capped at 10,000 rows. A cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with `'` so a spreadsheet doesn't treat it as a formula. The data comes straight from stored, already-masked metadata.

## Production notes

- **Masking.** Before an event is stored, keys matching the sensitive list are redacted anywhere in it. Values holding a Luhn-valid card number or a JWT are also redacted, whatever their key. Masking fails closed: if it ever raises, the event is stored as a `masking_failed` placeholder instead of the original data. Form and JSON bodies (request and response) are parsed and masked before they are stored, and `@sensitive_post_parameters` is honoured. Multipart bodies are only summarised when your view parsed them, binary bodies are stored as a size summary, and the recorded full URL has its sensitive query parameters masked.
- **Sampling.** Use `SAMPLING_RATE` to limit how much is stored. Errors and slow requests are always kept, so the interesting ones survive. By default every request is captured and the unpicked ones are discarded at the end; set `EARLY_SAMPLING = True` to also skip the capture work for them (their errors and slow requests keep only the request and exception events).
- **Retention.** Events accumulate until you delete them. Set `RETENTION_HOURS` to prune old events automatically, or schedule the cleanup command, for example hourly from cron:

  ```bash
  python manage.py inspector_cleanup --hours 24      # delete events older than 24 hours
  python manage.py inspector_cleanup                 # uses RETENTION_HOURS, or 24
  python manage.py inspector_cleanup --dry-run       # show how many would be deleted
  ```

  Deletes run in batches (`--batch-size`, default 10,000), so a large backlog doesn't lock the table in one long statement.

- **ASGI.** The middleware handles async requests natively. Database writes and the lazy `request.user` stay in a sync context, and concurrent requests never share trace state.
- **Failure isolation.** The inspector's own errors are caught and logged to the `django_inspector` logger at WARNING, and your request carries on. Set `INSPECTOR_RAISE_ERRORS = True` to surface them while developing.
- **Storage.** Events are buffered in memory and written with one `bulk_create` at the end of each request, into a single `Event` table (`trace_id`, `event_type`, `timestamp`, JSON `metadata`, plus `method`/`status_code`/`path`/`duration_ms`/`fingerprint`/`parent_trace_id`/`pinned` columns derived from the masked metadata for filtering and grouping).
- **Admin.** If your project has `django.contrib.admin` installed, `Event` shows up there as a read-only list (no add, change or delete) — filterable by type and pinned, searchable by trace id and fingerprint.

## Development

```bash
uv run --extra dev python -m pytest          # the test suite
uv build && python scripts/check_dist.py dist/   # build and check the wheel/sdist contents
uv run --extra dev ruff check django_inspector tests scripts
uv run --extra dev mypy
uv run --extra dev python scripts/benchmark.py  # per-request overhead, inspector on vs off
INSPECTOR_TEST_DB=postgres uv run --extra dev --with 'psycopg[binary]' python -m pytest   # on PostgreSQL (PG* env vars)
```

Planning docs live in `docs/`: the roadmap, requirements, and a spec and plan per feature under `docs/superpowers/`.

## License

MIT. See [LICENSE](LICENSE).
