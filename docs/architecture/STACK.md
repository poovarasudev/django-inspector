# Stack

> **Snapshot:** written at v1.0 (2026-05-23), before Phase 4 added `masking.py`, `sampling.py`, `ignores.py`, `dashboard/auth.py` and the ContextVar buffers. Check the code before relying on details here, and refresh this file when you touch the area it describes.

## Language & Runtime

- **Python** ≥ 3.8 (declared in `pyproject.toml`).
- **Django** ≥ 4.0 — sole runtime framework.
- Project uses `uv` + `venv` for dependency management (see project memory). No `requirements.txt`; deps live in `pyproject.toml` with `uv.lock` as the lockfile.

## Runtime Dependencies

| Package | Version constraint | Purpose |
|---------|--------------------|---------|
| `django` | `>=4.0` | Web framework, ORM, migrations, signals, middleware |

That is the entire runtime dependency surface. Intentional — `django-inspector` is a leaf package that should not pull in heavy transitive deps.

## Dev / Test Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=7.0` | Test runner |
| `pytest-django` | `>=4.5` | Django integration for pytest |
| `coverage` | `>=7.0` | Coverage measurement |

## Build / Packaging

- Build backend: `setuptools>=68` + `wheel` via PEP 517.
- Package discovery: `setuptools.packages.find` rooted at `.`, including `django_inspector*` only.
- License: declared as MIT in `pyproject.toml`; LICENSE file in repo is Apache 2.0 — **inconsistency to resolve**.

## Frontend / Dashboard Stack

- Server-rendered Django templates under `django_inspector/templates/inspector/`.
- **HTMX** is the interactivity layer (used in dashboard for live feed refresh, filters, partial table updates). Loaded via CDN in `base.html`.
- Styling appears to be inline / template-embedded (`base.html` is ~18 KB and contains the bulk of CSS; no separate static asset pipeline configured).
- No JavaScript build step, no Node toolchain.

## Database

- Uses Django's ORM exclusively; no raw SQL outside the SQL watcher's instrumentation.
- Single model: `django_inspector.storage.models.Event` with `(trace_id, event_type, timestamp, metadata JSONField)`.
- Migrations under `django_inspector/migrations/` — one `0001_initial.py`.
- PRD targets SQLite / PostgreSQL / MySQL for v1; nothing is backend-specific today except that JSONField filters (`metadata__field`) behave best on PostgreSQL/SQLite.

## Test Runtime

- `tests/settings.py` configures Django with in-memory SQLite, `django_inspector` installed, `InspectorMiddleware` mounted, and `DJANGO_INSPECTOR = {"INSPECTOR_ENABLED": True}`.
- `pytest.ini_options` block in `pyproject.toml` sets `DJANGO_SETTINGS_MODULE = "tests.settings"`.

## Versions Locked

- Repo carries `uv.lock` (~130 KB) — exact reproducible env.
- No CI configuration found in this snapshot (`.github/` is empty in workspace listing).

## What NOT in Stack (Intentional / Implied)

- No Celery, Redis, OpenTelemetry, async DB driver, frontend SPA framework, or static-asset bundler — all deferred per PRD §5 / §19 phasing.
- No Django REST Framework dependency (DRF integration is a Phase-4 PRD item).
- No `channels`, no `requests`/`httpx` (HTTP client watcher is Phase 3 PRD).

## Confidence

- **HIGH** for everything sourced directly from `pyproject.toml`, `uv.lock` presence, `tests/settings.py`, and on-disk file inspection.
- **MEDIUM** for HTMX inference (inferred from commit message `feat(03-03c): dashboard pages — views, templates, HTMX interactions` and dashboard view code that returns partials on `HX-Request` header).
