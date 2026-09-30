# Phase 7 — ASGI Verification & v0.2.0 Release Prep

**Status:** Approved in chat · **Date:** 2026-09-30 · **Branch:** `feat/phase-7-asgi-release-prep` (based on `feat/phase-6-signal-logging-watchers`)
**Covers:** ASYNC-03, plus release hygiene (README, CHANGELOG, license, packaging, CI)
**Roadmap:** Phase 7 (`docs/roadmap.md`), the last phase of the v1.1 milestone

## Goal

Prove that every v1.1 watcher works end to end under ASGI, and make the package releasable as **0.2.0** under the **MIT** license. Tagging a release and publishing to PyPI are out of scope; the owner triggers those.

## Findings that shape the phase

1. **The package crashes on import on Python 3.8/3.9.** `watchers/request.py` (two functions) and `watchers/sql.py` (one) use `X | None` return annotations, which are evaluated at function definition time. Verified: on Python 3.9.6 with Django 4.2, `apps.ready()` raises `TypeError: unsupported operand type(s) for |`. With the annotations fixed, the full suite passes on 3.9/4.2 (259 passed, 2 skipped: the async-signal tests need Django ≥ 5.0).
2. **The built wheel ships no templates.** `uv build` produces a wheel with 0 `.html` files, so a pip-installed dashboard raises `TemplateDoesNotExist` on every page. The tests don't catch it because they run from the source tree.
3. **`InspectorMiddleware` is sync-only.** Under ASGI, Django adapts it with a thread hop on every request. The end-of-request flush writes to the database, and the request watcher can evaluate the lazy `request.user`; both must stay in a sync context.

4. **Found during implementation (CI): events could be lost under concurrent ASGI requests.** The event buffer and SQL query log were `ContextVar`-backed but created lazily. Request tasks that inherited a buffer from their parent context shared one list, so one request's flush (copy, write, clear, in a worker thread) could wipe another request's freshly appended event. The fix: the middleware gives every request fresh buffers at the start and resets them at the end. `tests/test_asgi.py` has a deterministic regression test.

## Decisions

| Decision | Choice |
|---|---|
| License | **MIT.** Replace `LICENSE` (Copyright (c) 2026 Poovarasu Sekar). `pyproject.toml` already says MIT. |
| Version | **0.2.0.** A single source: `django_inspector.__version__`, read by `pyproject.toml` (`dynamic = ["version"]`) and by the dashboard sidebar. |
| Middleware | Supports both sync and async requests (`sync_capable = async_capable = True`). In async mode, the DB flush and the request watcher's `on_response` run through `sync_to_async`. Everything else in the request lifecycle is unchanged. |
| asgiref compatibility | Use `asgiref.sync.iscoroutinefunction`/`markcoroutinefunction` (asgiref ≥ 3.6, Django ≥ 4.2). On older asgiref (Django 4.0/4.1), fall back to the `asyncio` marker. |
| Supported and tested versions | The package keeps `django>=4.0`. CI tests Django 4.2 (Python 3.8–3.12) and Django 5.2 (Python 3.10–3.13). The README says 4.0/4.1 are expected to work but aren't tested. |
| Packaging | Add `[tool.setuptools.package-data]` for `templates/**/*.html`, and `scripts/check_dist.py`, which fails if the wheel or sdist is missing templates, migrations, `LICENSE` or `README.md`. |
| CI | GitHub Actions: a test matrix, plus a build job that runs `python -m build` and `scripts/check_dist.py`. |
| Release | No git tag, no PyPI upload. |

## Async middleware

```python
class InspectorMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.async_mode = iscoroutinefunction(get_response)
        if self.async_mode:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.async_mode:
            return self.__acall__(request)
        ...  # existing sync path, unchanged
```

`__acall__` follows the sync path step for step, with these changes:
- `await self.get_response(request)`
- `await sync_to_async(self._notify_request_end)(request, response)`
- `await sync_to_async(self._flush)()`

The trace id is set and reset in the request's own task, so concurrent requests never share it. Sampling, `IGNORE_PATHS` and dashboard-path skipping behave exactly as in sync mode.

## ASGI end-to-end verification (ASYNC-03)

- `tests/asgi_urls.py`: an async `checkout` view that, for each request:
  - awaits `cache.aset`/`cache.aget`
  - renders a small template tree (layout ← page with an include)
  - dispatches a watched test signal (`asend` on Django ≥ 5.0, otherwise `send`)
  - logs a warning
  - awaits a short sleep, so concurrent requests interleave

  A sync twin, `checkout_sync`, does the same for WSGI parity.
- `tests/test_asgi.py`:
  - **Middleware mode:** async `get_response` gives a coroutine-function middleware; sync `get_response` gives a plain one.
  - **ASGI concurrency:** 5 requests run at once through `AsyncClient` (the real `ASGIHandler` middleware chain). Each trace holds exactly its own events: 1 `request.completed`, 2 cache ops on its own key, 3 template renders, 1 `signal.dispatched` whose receiver saw its own order id, and 1 `log.record` with its own id.
  - **ASGI edge cases:** an ignored path and a dashboard path produce no events; `SAMPLING_RATE=0.0` with a 200 response produces no events.
  - **WSGI parity:** the same view run through the sync `Client` produces the same event set.

## Release documents

- **`README.md`:**
  - what the package does, and the watchers it has
  - install
  - `INSTALLED_APPS`, `MIDDLEWARE` (first), mounting `dashboard.urls` (the prefix must match `DASHBOARD_URL_PREFIX`) and `migrate`
  - dashboard access (`is_staff` default, `INSPECTOR_DASHBOARD_PERMISSION`, IP allowlist)
  - a reference table of every setting with its default
  - watcher defaults
  - production notes (sampling, masking, `inspector_cleanup`, ASGI)
  - supported versions
- **`CHANGELOG.md`** in Keep a Changelog format:
  - `0.2.0`: everything since 0.1.0, grouped as Added / Changed / Fixed, with requirement IDs
  - `0.1.0`: the v1.0 MVP
- **Package metadata:** classifiers (Django 4.2/5.x, Python 3.8–3.13, MIT), `authors` (name only), and project URLs.

## Out of scope

- A git tag, a GitHub release, and PyPI publishing.
- Testing Django 4.0/4.1.
- Event timestamps (deferred in Phase 5).
- The MASK-05 fix, which remains a separate item.
