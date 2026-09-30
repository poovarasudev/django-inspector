# ASGI Verification & v0.2.0 Release Prep Implementation Plan

**Goal:** Verify every v1.1 watcher under ASGI, and make the package releasable as 0.2.0 (MIT).

**Spec:** `docs/superpowers/specs/2026-09-30-asgi-release-prep-design.md`

**Execution:** Implemented directly in-session, as in Phases 5–6. TDD where there is behaviour to test, one commit per task. Test command: `uv run --extra dev python -m pytest`.

## Review Focus

1. **Concurrent ASGI requests.** Trace ids, event buffers and the template render tree must never leak between requests (Task 3).
2. **The async flush.** It must run in a sync context (`sync_to_async`), or Django raises `SynchronousOnlyOperation` and events are silently lost (Task 2).
3. **A pip-installed package.** The wheel must contain templates, migrations and license metadata (Task 1).
4. **Python 3.8/3.9 imports.** No runtime-evaluated 3.10+ syntax may come back; CI guards this (Tasks 1, 5).

## Tasks

| # | Task | Files | Verification |
|---|---|---|---|
| 1 | Python 3.8 compatibility + packaging | `watchers/request.py`, `watchers/sql.py` (annotations); `pyproject.toml` (package-data, dynamic version, metadata, classifiers); `django_inspector/__init__.py` (`__version__ = "0.2.0"`); `templatetags/inspector_tags.py` + `_sidebar.html` (version); `LICENSE` (MIT); `scripts/check_dist.py` | Suite on 3.11/5.2 and 3.9/4.2; `uv build` + `check_dist.py`; test for the sidebar version |
| 2 | Async-capable middleware | `middleware.py` | `tests/test_asgi.py` (mode detection, async flush, ignore/dashboard/sampling in async mode) |
| 3 | ASGI end-to-end tests | `tests/asgi_urls.py`, `tests/templates/inspector_tests/{layout,checkout,line}.html`, `tests/test_asgi.py` | Concurrency test with no cross-trace bleed; WSGI parity |
| 4 | README + CHANGELOG | `README.md`, `CHANGELOG.md` | Settings table checked against `conf.DEFAULTS` by a test |
| 5 | CI | `.github/workflows/ci.yml` | Workflow YAML parses; matrix and build job mirror the local checks |
| 6 | Docs | `docs/roadmap.md`, `docs/requirements.md`, `docs/project.md`, `CLAUDE.md`, `docs/architecture/STRUCTURE.md` note | Full suite; end-to-end install of the built wheel in a fresh venv |
