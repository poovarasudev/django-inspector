# Summary: Plan 04-02 — Dashboard Access Control + Ignore Lists

**Phase:** 4 — Safety & Hardening
**Plan:** 2 of 3
**Status:** Complete
**Completed:** 2026-05-23

## What Was Built

### New Modules
- **`django_inspector/dashboard/auth.py`** — `inspector_required` decorator, `can_access_dashboard()`, IP allowlist check via `ipaddress`, custom permission callable via `import_string`, default `is_staff` fallback.
- **`django_inspector/ignores.py`** — `should_ignore_path()` (regex list, lru_cache), `should_ignore_exception()` (class identity via `import_string`, supports public aliases like `django.http.Http404`), `invalidate_ignore_caches()` for test isolation.

### Modified Files
- **`django_inspector/conf.py`** — Added `INSPECTOR_DASHBOARD_PERMISSION`, `INSPECTOR_DASHBOARD_IP_ALLOWLIST`, `IGNORE_PATHS`, `IGNORE_EXCEPTIONS`, `INSPECTOR_RAISE_ERRORS` to `DEFAULTS`.
- **`django_inspector/dashboard/urls.py`** — All 7 routes wrapped with `inspector_required`.
- **`django_inspector/apps.py`** — `_check_production_auth_config()` logs `WARNING` at startup when `DEBUG=False` and no explicit auth is configured (AUTH-04).
- **`django_inspector/middleware.py`** — `should_ignore_path` gate at `__call__` entry, skips entire trace lifecycle for ignored paths.
- **`django_inspector/watchers/exception.py`** — `should_ignore_exception` gate before `self.record()`.

### New Tests
- **`tests/test_dashboard_auth.py`** — 9 tests: anon/non-staff/staff access, 403 body safety, custom callable, IP allowlist.
- **`tests/test_ignores.py`** — 12 tests: path patterns, prefix regex, empty lists, multiple patterns, `Http404` alias resolution, middleware integration (zero events for ignored paths, no trace_id).

## Commits
- `b9d9dcc` — feat(04-02): add dashboard auth + ignore list + INSPECTOR_RAISE_ERRORS settings
- `a0ca3dd` — feat(04-02): dashboard auth (inspector_required), all 7 routes locked, startup warning (AUTH-01..05)
- `5d76b20` — feat(04-02): ignores.py + IGNORE_PATHS/IGNORE_EXCEPTIONS gates (IGN-01..04)
- `ed5daa5` — test(04-02): add test_dashboard_auth.py (9 tests) + test_ignores.py (12 tests)

## Verification

- `uv run python -m pytest tests/` → **117 passed, 0 failed**
- Anonymous GET to decorated view → 403 with body "Inspector: access denied" ✓
- Staff user GET → 200 ✓
- `IGNORE_PATHS=["/healthz"]` + request to `/healthz` → `Event.objects.count() == 0` ✓
- `IGNORE_EXCEPTIONS=["django.http.Http404"]` → `should_ignore_exception(Http404())` is True ✓

## Self-Check: PASSED

All 9 plan tasks executed. All acceptance criteria met. 117/117 tests pass.

## Deviations from Plan

**[Rule 1 - Bug Fix] `Http404` FQN mismatch — `_compiled_exception_names` used string comparison.**
Found during: Task 9 | Issue: `django.http.Http404` resolves to `django.http.response.Http404` at the class level; string-based FQN matching failed | Fix: Replaced `_compiled_exception_names` (frozenset of strings) with `_compiled_exception_classes` (frozenset of class objects via `import_string`) — class identity check is alias-safe | Files modified: `django_inspector/ignores.py` | Verification: `test_http404_ignored` and `test_multiple_exception_classes` now pass | Commit: `ed5daa5`

**Total deviations:** 1 auto-fixed. **Impact:** Better API — users can specify `django.http.Http404` (the public import path) rather than needing to know the internal module.

## Requirements Satisfied

- AUTH-01: Anonymous users get 403 on every dashboard URL ✓
- AUTH-02: Non-staff users get 403 ✓
- AUTH-03: Staff users (or custom permission) get 200 ✓
- AUTH-04: `DEBUG=False` + no config → startup WARNING logged ✓
- AUTH-05: 403 body contains no trace ids or event data ✓
- IGN-01: `IGNORE_PATHS` setting matched against `request.path` ✓
- IGN-02: `IGNORE_EXCEPTIONS` setting matched against exception class ✓
- IGN-03: Both lists compiled/cached on first use ✓
- IGN-04: Public aliases work (`django.http.Http404`) ✓
