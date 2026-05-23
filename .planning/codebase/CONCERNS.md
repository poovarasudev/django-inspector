# Concerns

Risks, smells, and gaps surfaced by reading the current code. Ordered by approximate severity.

---

## HIGH — Production Safety

### C-1. Dashboard has no access control

`@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/dashboard/views.py:7-181` exposes request bodies, headers, exception locals, and SQL — including any captured cookies and Authorization headers — to whoever can reach the dashboard URL.

- PRD §14 calls for Django permissions, superuser mode, and IP allowlist. **None implemented.**
- Anyone who can hit `/inspector/` can read every request that has gone through the system.
- **Mitigation (v1.1):** decorator that requires `is_staff` (configurable), optional IP allowlist setting, optional "read-only when DEBUG=False" mode.

### C-2. No sensitive-data masking

The request watcher captures request bodies, response bodies, headers, cookies, query strings; the exception watcher captures local variables via `repr()`. **Nothing is redacted.**

- Passwords in form POSTs, `Authorization: Bearer …` headers, session cookies, CSRF tokens, credit-card-shaped strings — all land in the DB as plain text.
- PRD §14 explicitly lists masking as required. The `SENSITIVE_KEYS` setting referenced in PRD §16 is not implemented in `conf.py`.
- **Mitigation (v1.1):** masking module that runs on metadata before `buffer_event`. Default sensitive keys: `password`, `token`, `secret`, `authorization`, `cookie`, `csrfmiddlewaretoken`, `api_key`, `*-key`, regex for card-shaped strings.

### C-3. No sampling — every event is recorded

Every request, every query, every exception lands in the DB. Production traffic at >100 req/s will fill the table and the per-request `bulk_create` becomes a meaningful synchronous cost.

- PRD §15 specifies success / error / slow sampling. Setting `SAMPLING_RATE` is referenced but unimplemented.
- **Mitigation (v1.1):** sampling decision happens once per trace at middleware entry. Always-on for errors and slow requests; configurable rate for everything else.

### C-4. `threading.local()` for SQL log and event buffer is NOT async-safe

`@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/watchers/sql.py:22-29` and `@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/storage/flush.py:11-18` use `threading.local()`. Under ASGI / `async def` views, coroutines on the same thread share state.

- The trace id (in `tracing/context.py`) correctly uses `ContextVar`. The buffer and SQL log do not, so queries from coroutine A can leak into coroutine B's log and the wrong queries can be N+1-flagged together.
- **Mitigation (v1.1):** replace `threading.local` with `ContextVar[list]` in `flush.py` and `sql.py`. Same lifecycle (clear at end of middleware) but per-task safety.

### C-5. Silent `try/except Exception: pass`

`@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/middleware.py:66-92`, `@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/watchers/sql.py:153-156`, and several `_safe_*` helpers swallow all exceptions silently.

- Convention is "never break user requests" — good — but the silence hides inspector bugs from operators.
- **Mitigation (v1.1):** keep the try/except but `logger.warning(..., exc_info=True)` inside. Add a setting `INSPECTOR_RAISE_ERRORS=False` that lets developers opt into raising during local development.

---

## MEDIUM — Correctness / Quality

### C-6. Retention is manual

Only `inspector_cleanup --hours N` exists, and it has to be run by hand. No scheduled cleanup, no count-based or size-based caps. PRD §15 wants all three.

- A long-running install will accumulate `Event` rows indefinitely.
- **Mitigation (v1.1):** retention policy as a setting, optionally enforced inside middleware flush (e.g., on every Nth request, delete oldest beyond `MAX_EVENTS`).

### C-7. Synchronous bulk_create on the hot path

`flush_events()` runs inside the request finally block. For a request that issued many queries, this can be a non-trivial DB write.

- PRD §15 says "async persistence; offload event writes". Not implemented.
- **Mitigation (v1.2):** optional thread pool or Celery offload behind a setting.

### C-8. O(N) watcher lookup on every request

`@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/middleware.py:51-80` re-imports modules and linear-scans `_watcher_instances` to find the `RequestWatcher` on every request — twice per request (start + end).

- **Mitigation:** stash watcher instances by name on the AppConfig (`app._watcher_by_name`) and look up O(1).

### C-9. SQL origin extraction is full-stack walk per query

`@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/watchers/sql.py:167-196` calls `traceback.extract_stack()` for every query, then iterates skip patterns. Under high query volume this becomes measurable.

- **Mitigation:** use `sys._getframe()` and walk manually with early-exit; cache skip patterns as a compiled set.

### C-10. JSONField filters in dashboard

`@/Users/poovarasu/Arasu/projects/django/django-inspector/django_inspector/dashboard/views.py:27-43` filters by `metadata__method`, `metadata__status_code__gte`, etc. Works on SQLite/PostgreSQL; on MySQL these are unindexed JSON path lookups and slow.

- **Mitigation (v1.2):** denormalize hot filter fields onto the `Event` model as columns (`method`, `status_code`, `is_slow`, `path`). Keeps JSON for everything else.

### C-11. License inconsistency

`pyproject.toml` declares `license = { text = "MIT" }` but `LICENSE` is Apache-2.0 (191 lines). PRD §header says "MIT or BSD-3-Clause".

- **Mitigation:** pick one. If MIT: replace `LICENSE`. If Apache-2.0: update `pyproject.toml` and PRD.

### C-12. PRD's `IGNORE_PATHS` / `IGNORE_EXCEPTIONS` not implemented

Dashboard self-traffic is filtered by hardcoded prefix; nothing else can be ignored. Health checks, metrics endpoints, expected `Http404` exceptions all get recorded.

- **Mitigation (v1.1):** support `IGNORE_PATHS: [regex,...]` and `IGNORE_EXCEPTIONS: ["app.errors.Expected", ...]`.

---

## LOW — Polish

### C-13. No `__repr__` / admin registration for `Event`

`Event.__str__` exists but no admin registration. Operators have no built-in Django admin view of raw events.

### C-14. Migration timestamp is `2026-05-03`

Likely reflects a system clock at the time of generation. PyPI consumers may see a "future" migration date. Harmless functionally; cosmetic.

### C-15. `models.py` stub at the package root

A 64-byte `django_inspector/models.py` exists alongside the canonical `django_inspector/storage/models.py`. Probably re-exports `Event` so Django's auto-discovery picks it up under the app. Worth a comment or removal in favor of explicit `app_label` (which is already set).

### C-16. No CI configuration

`.github/` exists but is empty. No automated test or lint runs on PR.

### C-17. No README in repo snapshot

`pyproject.toml` references `README.md` but it isn't visible in the workspace tree. Either it's gitignored (unlikely) or absent. PyPI release will look bare without it.

### C-18. No Sphinx / mkdocs / RTD setup

Public API docs don't exist; only the PRD. For an open-source library targeting wide adoption, documentation infrastructure should land before v1.0 release.

---

## Open Design Questions

- **Storage interface (PRD §13):** when to introduce `AbstractStorageBackend`? Premature now (only ORM, no alternatives needed). Suggest deferring until first non-ORM backend is in scope.
- **Plugin API (PRD §17):** the internal `register("name", Cls)` is effectively the plugin point. Promoting it to a public entry point (`setuptools` `entry_points` group `django_inspector.watchers`) is straightforward and worth doing in v1.2.
- **Event schema (PRD §10):** PRD lists `trace_id`, `span_id`, `parent_span_id`, `type`, `timestamp`, `tags`, `metadata`. We have `trace_id`, `event_type`, `timestamp`, `metadata`. Spans and tags are missing — important for the eventual timeline/waterfall fidelity. Worth adding empty fields with defaults in v1.1 so we don't need a destructive migration later.
