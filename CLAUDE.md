# django-inspector

Unified runtime observability for Django — Django's answer to Laravel Telescope. Watchers capture requests, SQL, exceptions, cache operations, template renders, signal dispatches and log records as events correlated by a per-request `trace_id`, shown in a built-in HTMX dashboard.

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
uv build && python scripts/check_dist.py dist/   # built wheel/sdist must contain templates, assets, migrations, LICENSE
uv run --extra dev ruff check django_inspector tests scripts   # lint (CI gate)
uv run --extra dev mypy                          # type-check (CI gate)
uv run --extra dev python scripts/benchmark.py   # p50 overhead, inspector on vs off (CI budget: 4 ms)
INSPECTOR_TEST_DB=postgres uv run --extra dev --with 'psycopg[binary]' python -m pytest   # PostgreSQL run
```

## Workflow

Work follows the Superpowers flow:

1. **Brainstorm** the next roadmap item (`superpowers:brainstorming`) and write a spec to `docs/superpowers/specs/`. Cite the requirement IDs it covers.
2. **Plan** it (`superpowers:writing-plans`) into `docs/superpowers/plans/`.
3. **Execute** on a feature branch with TDD (`superpowers:subagent-driven-development` or `superpowers:executing-plans`).
4. **Finish** (`superpowers:finishing-a-development-branch`), then tick the work items in `docs/roadmap.md` and update statuses in `docs/requirements.md` and `docs/project.md`.

v1.1 is complete (package **0.2.0**). Next up: the owner tags and publishes 0.2.0, then **v1.2** gets broken into phases in `docs/roadmap.md` (Model, Email, Management Command, Middleware watchers).

## Constraints

- Python ≥ 3.8, Django ≥ 4.0. **No new runtime dependencies** — the package stays a leaf. CI covers Django 4.2, 5.2, 6.0 and 6.1, plus a PostgreSQL job.
- Annotations must import on Python 3.8: no `X | Y` unions or `list[...]`-style builtin generics (`tests/test_compat.py` enforces this). CI runs Python 3.8–3.13 × Django 4.2/5.2.
- The version lives only in `django_inspector/__init__.py` (`__version__`); record user-visible changes in `CHANGELOG.md`, and keep the README settings table in step with `conf.DEFAULTS` (`tests/test_docs.py`).
- `DJANGO_INSPECTOR` settings keys and the `Event` model schema are public: new keys are additive, schema changes ship a new migration (never edit `0001_initial.py`).
- Middleware overhead stays under ~2 ms p50 with all watchers on at default sampling (`scripts/benchmark.py`; about 0.6 ms at 0.2.0).
- Every watcher works under WSGI **and** ASGI. Per-trace state uses `ContextVar`, never `threading.local`.
- Never break the host request: inspector errors are caught and logged with `logger.warning(..., exc_info=True)` unless `INSPECTOR_RAISE_ERRORS` is on.
- Mask before serialising: a payload stored as a string (a body, a repr) must be masked with `masking.mask_value()` first, because key-based masking can't see inside strings.
- New settings get a default in `conf.DEFAULTS`, a README row, and a type/range rule in `checks.py`.
- Hot-path hooks bail out early with `BaseWatcher.is_capturing()` and bound what they store (see the `MAX_*` constants).
- One logger only: `logging.getLogger("django_inspector")`. No `print`.
- Read settings through `django_inspector.conf.inspector_settings`, never `settings.DJANGO_INSPECTOR` directly.
- Watchers record through `self.record(...)` (applies enable checks, the active-trace check and masking) — never call `buffer_event` directly.
- Each new watcher ships `tests/test_<name>_watcher.py` covering lifecycle, capture, trace correlation, the no-trace case, and edge cases.

## Commits

Conventional commits: `feat(<scope>):`, `fix(<scope>):`, `test(<scope>):`, `docs:`, `chore:`. Use the module or feature as the scope (e.g. `feat(cache-watcher):`) and reference requirement IDs in the body when relevant.
