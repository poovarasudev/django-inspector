# Phase 14 — HTTP Client Watcher

**Status:** Written under the owner's standing approval (2026-09-30); assumptions are listed at the end for review · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** HTTP-01..03 (`docs/requirements.md`), PRD §11.14
**Roadmap:** Phase 14 (v1.3) · **Builds on:** Phase 8 columns, the Phase 12 Issues page

## Goal

Outgoing HTTP calls made with `requests` and `httpx` (sync and async) show up in the trace that made them, with:
- method, masked URL and host
- status, duration and error
- redirect count and response size

The Issues page ranks slow hosts. Neither library becomes a dependency.

## Decisions

| Decision | Choice |
|---|---|
| `requests` hook | Patch `requests.Session.send`. Every `requests.get/post/...` call and every session goes through it. |
| `httpx` hook | Patch `httpx.Client.send` and `httpx.AsyncClient.send`. `get/post/stream/request` all end there. |
| Lazy import | `install_hooks()` tries each import separately. A missing library is skipped with a `logger.debug`, and the watcher still enables for the other. The package never imports either at module import time. |
| Event | `http.request`, one per `send` call. Redirects that the library follows internally count as one call, with `redirects = len(response.history)`. |
| URL masking | Query parameters whose names match `SENSITIVE_KEYS` are replaced with `***REDACTED***`, then the whole URL goes through `mask_value` (JWTs, card numbers). Userinfo (`user:pass@`) is removed. |
| Stored data | No headers and no bodies: they usually carry credentials and payloads. Only the response size (`Content-Length`, or `len(content)` when already read and non-streaming). |
| Default | `"http": True`. The volume is low and nothing sensitive is stored beyond the masked URL. |
| Retries | Not captured. `urllib3` retries inside the adapter and `httpx` transport retries are invisible at `send`; each `send` call is one event. |

Alternatives rejected:
- **Patching `urllib3` or `httpcore` directly:** it catches more, including other libraries' traffic, but it ties us to private internals and double-counts with the high-level hooks.
- **Patching `requests.adapters.HTTPAdapter.send`:** misses custom adapters. `Session.send` sees every call.
- **Adding `aiohttp`:** less common in Django apps. Deferred (discovery §3 #4).

## Watcher (`django_inspector/watchers/http.py`)

`HTTPClientWatcher(BaseWatcher)`, `watcher_name = "http"`, added to `apps.WATCHER_MODULES`, with `WATCHERS["http"] = True`.

**Hooks:**
- `install_hooks()` saves and replaces each available `send` on its class, guarded by a marker so it's never patched twice.
- `remove_hooks()` restores exactly what it replaced.

**Sync wrapper** (`requests.Session.send`, `httpx.Client.send`):
1. If `not watcher.is_capturing()`, return the original call.
2. Otherwise, time the original call. On an exception, record `error` as `"Type: message"`, with the message run through `mask_value`, then re-raise it unchanged.
3. Record the event, guarded: a failure logs and never affects the call.

**Async wrapper** (`httpx.AsyncClient.send`): the same, with `await`. Recording is in-memory buffering only (`record()`), so it's safe in async code.

**Streaming.** For `requests`, when `stream=True` is passed in kwargs, the duration covers only up to receiving the headers. For `httpx`, `send(..., stream=True)` behaves the same way. The event gets `streamed: true`, and the size comes from `Content-Length` only.

**`http.request` metadata:**

| Key | Value |
|---|---|
| `library` | `"requests"` or `"httpx"` |
| `method` | Upper-case method |
| `url` | Masked URL, truncated to 2048 characters |
| `host` | `hostname[:port]` from the URL |
| `status_code` | `int` or `None` on error |
| `duration_ms` | Float |
| `redirects` | `int` |
| `response_bytes` | `int` or `None` |
| `streamed` | `bool` |
| `error` | Only when the call raised |
| `origin_file` / `origin_line` / `origin_function` | `extract_origin()`, as the SQL watcher stores them |

**Columns (Phase 8 extractor).** `storage/columns.py` gains an `http.request` extractor:
- `method`, `status_code`, and `path` = the masked URL (truncated to 1024);
- `fingerprint = sha1("<method>\n<host>\n<path template>")[:16]`, where the path template replaces digit runs and UUID-like segments with `?`, so calls to one endpoint group together.

## URL masking helper (`masking.py`)

`mask_url(url, extra_keys=()) -> str`:
- parse with `urllib.parse.urlsplit`;
- drop the userinfo;
- for each query parameter, replace the value with `***REDACTED***` when its key is sensitive (same key rules as `mask_metadata`);
- reassemble, then apply `mask_value`;
- on any parsing error, return `mask_value(url)`.

It works on `str` input only. `httpx.URL` and other objects are `str()`-ed first.

## Dashboard

**HTTP list** (`http/`, `http-list`, sidebar entry "HTTP"):
- Columns: method badge, host, the URL path (truncated), status badge, duration, time.
- Filters: `host` (icontains on `path`, which holds the URL), status class (on the column) and `errors` (only calls that raised).
- CSV export like the other lists.

**HTTP detail** (`http/<pk>/`): all fields, the origin, and a link to the parent trace.

**Request detail and trace pages:** an "HTTP" count and a short list, following the Phase 6/13 pattern.

**Issues page** (HTTP-03): a fourth section, "Slow hosts". `http_host_stats(since, limit)` in `dashboard/issues.py` groups the bounded `http.request` rows by `host` in Python and reports:
- count, p50, p95;
- error rate: share of rows with `error`, or a status ≥ 500;
- a link to the slowest example's trace.

Rows are ordered by p95, descending.

## Tests and dev dependencies

`requests>=2.25` and `httpx>=0.23` are added to the **`dev`** extra only. The package's runtime dependencies stay `django` alone.

No network access:
- **`requests`:** a test adapter (a `requests.adapters.BaseAdapter` subclass returning a built `Response`) mounted on the session, or a monkeypatched `HTTPAdapter.send`.
- **`httpx`:** `httpx.MockTransport` for both clients.

| File | Covers |
|---|---|
| `tests/test_http_watcher.py` (new) | Lifecycle for each library (install/remove/no double-patching); one library missing (simulated with `sys.modules` patching) still enables the other; neither missing → no-op. A requests GET/POST inside a trace → event fields. `requests.get()` module-level helper captured. httpx sync and async (`asyncio` test). Redirects counted. An error re-raised unchanged with `error` recorded. Streaming flags. URL masking (sensitive query keys, userinfo, JWT in path) plus the `path` column masked. No trace → no event. A disabled watcher is inert. A recording failure doesn't break the call. Correlation inside a request and inside `trace()`. |
| `tests/test_masking.py` | `mask_url` cases, including malformed URLs |
| `tests/test_columns.py` | The `http.request` extractor and fingerprint grouping (`/users/1` and `/users/2` share one) |
| `tests/test_issues.py` | `http_host_stats` |
| `tests/test_dashboard_*.py`, `tests/test_csv_export.py` | HTTP list, detail, sidebar, Issues section, request-detail integration, CSV |

Then the full gates. The benchmark doesn't make outgoing calls, so it must be unchanged.

## Docs

- README: an "Outgoing HTTP calls" section (what's hooked, what's stored and not stored, masking, retries note, streaming note) and the `WATCHERS["http"]` row.
- CHANGELOG: a new "0.4.0 (unreleased)" section for v1.3, citing HTTP-01..03.
- Roadmap and requirements: tick Phase 14 and HTTP-01..03.

## Assumptions (for the owner's end-of-milestone review)

1. **On by default,** since only the masked URL and metadata are stored: no headers, no bodies.
2. **Retries aren't captured** (the PRD lists them). Each `send` call is one event, and redirects are a count.
3. **`aiohttp` is deferred.**
4. **The changelog gets a separate v1.3 section, "0.4.0 (unreleased)",** assuming v1.2 ships first as 0.3.0.
