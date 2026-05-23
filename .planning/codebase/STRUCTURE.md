# Structure

## Top-Level Layout

```
django-inspector/
├── django_inspector/         # The installable package
│   ├── __init__.py
│   ├── apps.py               # AppConfig; autodiscover watchers
│   ├── conf.py               # Settings access layer
│   ├── middleware.py         # InspectorMiddleware
│   ├── models.py             # Stub (re-exports from storage)
│   ├── core/                 # Reserved namespace (empty)
│   ├── tracing/
│   │   ├── __init__.py       # Public surface for tracing
│   │   └── context.py        # ContextVar-based trace propagation
│   ├── watchers/
│   │   ├── __init__.py
│   │   ├── base.py           # BaseWatcher ABC
│   │   ├── registry.py       # Registry dict + register/get/all
│   │   ├── request.py        # RequestWatcher
│   │   ├── sql.py            # SQLWatcher
│   │   └── exception.py      # ExceptionWatcher
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── models.py         # Event model
│   │   └── flush.py          # Buffer + bulk_create flush
│   ├── dashboard/
│   │   ├── __init__.py
│   │   ├── urls.py           # 7 routes
│   │   └── views.py          # FBVs (list / detail / live_feed)
│   ├── management/
│   │   └── commands/
│   │       └── inspector_cleanup.py
│   ├── migrations/
│   │   ├── __init__.py
│   │   └── 0001_initial.py
│   └── templates/
│       └── inspector/
│           ├── base.html
│           ├── _sidebar.html
│           ├── _pagination.html
│           ├── _live_feed_table.html
│           ├── live_feed.html
│           ├── requests/{list,detail,_table}.html
│           ├── queries/{list,detail,_table}.html
│           └── exceptions/{list,detail,_table}.html
├── tests/
│   ├── __init__.py
│   ├── settings.py
│   ├── test_tracing.py            # 8 tests
│   ├── test_watcher_base.py       # 6 tests
│   ├── test_storage.py            # 6 tests
│   ├── test_request_watcher.py    # 16 tests
│   ├── test_sql_watcher.py        # 16 tests
│   └── test_exception_watcher.py  # 13 tests
├── prd.md                    # Full PRD (812 lines)
├── pyproject.toml            # Build, deps, pytest config
├── uv.lock                   # Dependency lockfile
├── LICENSE                   # Apache 2.0 file (pyproject declares MIT — mismatch)
├── README.md                 # (referenced by pyproject; not present in tree snapshot)
├── .gitignore
├── .gitattributes
├── .planning/                # GSD planning artifacts (this directory)
├── .windsurf/                # GSD / Windsurf skills + workflows + bin
├── .claude/                  # Claude config (gitignored)
└── .git/
```

## Package Boundaries

| Layer | What it knows about | What it imports |
|-------|--------------------|-----------------| 
| `tracing/` | Standalone — no Django dependency beyond stdlib | `uuid`, `contextvars` |
| `conf.py` | Django settings only | `django.conf.settings` |
| `storage/` | Django ORM, conf | `django.db.models`, `conf` |
| `watchers/base.py` | Conf, tracing, storage | `conf`, `tracing.context`, `storage.flush` |
| `watchers/{request,sql,exception}.py` | base + Django request/db/signal APIs | `base`, `registry`, Django internals |
| `middleware.py` | tracing, conf, watchers (lazy), storage.flush (lazy) | conf, tracing.context, lazy others |
| `dashboard/views.py` | Storage models, Django shortcuts | `storage.models.Event`, `django.shortcuts`, paginator |
| `management/commands/inspector_cleanup.py` | Storage model | `storage.models.Event` |

Dependency direction is strictly: **dashboard → storage ← watchers → tracing ← middleware → all**. No cycles.

## Filename / Module Conventions

- Singular module names for the package (`watchers/request.py`, not `requests/`).
- Test files: `tests/test_<unit>.py`, one per source module exercised.
- Templates organized by event kind (`templates/inspector/{requests,queries,exceptions}/{list,detail,_table}.html`), with underscore-prefixed partials.

## Reserved / Placeholder Paths

- `django_inspector/core/` — empty namespace, ready for "core domain" code (event schema validators, sampling, masking — none of which exist yet).
- `django_inspector/models.py` — 64-byte stub (presumably re-exports from `storage/models.py` so Django auto-discovers the Event model under the app label).

## Counts

| Layer | LOC |
|-------|-----|
| Package code (.py) | ~1,030 |
| Tests (.py) | ~800 |
| Templates (.html) | ~28 KB across 14 files |

## Where New Code Goes (mental model)

| Adding... | Goes in... |
|-----------|-----------|
| A new watcher | `django_inspector/watchers/<name>.py`, subclass `BaseWatcher`, call `register("name", Cls)` |
| A new event kind | Watcher emits `record("<watcher>.<kind>", metadata)` — no schema change needed |
| Sampling logic | New module `django_inspector/sampling/` (does not exist) |
| Masking logic | New module `django_inspector/masking/` (does not exist) |
| A new storage backend | New module `django_inspector/storage/<backend>.py` + an abstract base in `storage/__init__.py` (interface does not exist yet) |
| New dashboard page | `dashboard/urls.py` + view in `dashboard/views.py` + template under `templates/inspector/` |
| Settings | `django_inspector/conf.py` DEFAULTS dict |
