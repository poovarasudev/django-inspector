# django-inspector

Unified runtime observability for Django — Django's answer to Laravel Telescope. Watchers capture requests, SQL, exceptions, cache operations and template renders (and soon signals and logs) as events correlated by a per-request `trace_id`, shown in a built-in HTMX dashboard.

**Core value:** one dashboard, one trace — every request's full story is reconstructable from a single trace id.

## Where things live

| Path | What |
|------|------|
| `django_inspector/` | The package (watchers, tracing, storage, dashboard, masking, sampling) |
| `tests/` | pytest suite; `tests/test_<module>.py` mirrors the source |
| `prd.md` | Full product requirements (section numbers are cited as `PRD §11.5` etc.) |
| `docs/project.md` | Scope, constraints, key decisions, out-of-scope list |
| `docs/requirements.md` | Requirement IDs (`CACHE-01`, `TMPL-03`, …) and their status |
| `docs/roadmap.md` | Milestones and phases; what's next |
| `docs/architecture/` | Codebase maps (stack, structure, conventions, testing, concerns) — v1.0 snapshot |
| `docs/superpowers/specs/` | Design specs, `YYYY-MM-DD-<topic>-design.md` |
| `docs/superpowers/plans/` | Implementation plans produced from specs |

## Commands

```bash
uv run --extra dev python -m pytest              # full suite (use `python -m` so tests.settings imports)
uv run --extra dev python -m pytest tests/test_sql_watcher.py -q
```

## Workflow

Work follows the Superpowers flow:

1. **Brainstorm** the next roadmap item (`superpowers:brainstorming`) and write a spec to `docs/superpowers/specs/`. Cite the requirement IDs it covers.
2. **Plan** it (`superpowers:writing-plans`) into `docs/superpowers/plans/`.
3. **Execute** on a feature branch with TDD (`superpowers:subagent-driven-development` or `superpowers:executing-plans`).
4. **Finish** (`superpowers:finishing-a-development-branch`), then tick the work items in `docs/roadmap.md` and update statuses in `docs/requirements.md` and `docs/project.md`.

Next up: **Phase 6 — Signal & Logging Watchers** (`SIGL-01..06`, `LOG-01..06`).

## Constraints

- Python ≥ 3.8, Django ≥ 4.0. **No new runtime dependencies** — the package stays a leaf.
- `DJANGO_INSPECTOR` settings keys and the `Event` model schema are public: new keys are additive, schema changes ship a new migration (never edit `0001_initial.py`).
- Middleware overhead stays under ~2 ms p50 with all watchers on at default sampling.
- Every watcher works under WSGI **and** ASGI. Per-trace state uses `ContextVar`, never `threading.local`.
- Never break the host request: inspector errors are caught and logged with `logger.warning(..., exc_info=True)` unless `INSPECTOR_RAISE_ERRORS` is on.
- One logger only: `logging.getLogger("django_inspector")`. No `print`.
- Read settings through `django_inspector.conf.inspector_settings`, never `settings.DJANGO_INSPECTOR` directly.
- Watchers record through `self.record(...)` (applies enable checks, the active-trace check and masking) — never call `buffer_event` directly.
- Each new watcher ships `tests/test_<name>_watcher.py` covering lifecycle, capture, trace correlation, the no-trace case, and edge cases.

## Commits

Conventional commits: `feat(<scope>):`, `fix(<scope>):`, `test(<scope>):`, `docs:`, `chore:`. Use the module or feature as the scope (e.g. `feat(cache-watcher):`) and reference requirement IDs in the body when relevant.
