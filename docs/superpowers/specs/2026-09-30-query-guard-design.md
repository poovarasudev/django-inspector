# Phase 11 — Query Guard for Tests

**Status:** Written under the owner's standing approval (2026-09-30); assumptions are listed at the end for review · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** TEST-01..04 (`docs/requirements.md`), PRD §21 (CI performance regression detection)
**Roadmap:** Phase 11 · **Builds on:** Phase 8 (`normalize_sql`), the SQL watcher's detection

## Goal

Catch N+1 queries, duplicate queries and query-count regressions in CI. A failure message names each offending query shape, how often it ran, and the file and line it came from. An optional development mode reports N+1 queries while the app runs.

## Decisions

| Decision | Choice |
|---|---|
| Mechanism | Django's `connection.execute_wrapper()` on the guarded connections. No trace, middleware, buffer or database writes; independent of whether the SQL watcher is enabled. |
| Detection | Shared with the SQL watcher: `normalize_sql` for shapes, the same "3 or more of one shape = N+1" rule, and exact `(sql, params)` repeats = duplicates |
| Origins | `watchers.utils.extract_origin()` per query (the innermost application frame) |
| Public API | `django_inspector.testing.assert_queries(max=None, no_n_plus_one=False, no_duplicates=False, using=None)`: a context manager, function decorator and `TestCase` class decorator |
| Failure type | `QueryBudgetExceeded(AssertionError)`, so it reads as a normal test failure in pytest and unittest |
| Transaction noise | `SAVEPOINT`, `RELEASE SAVEPOINT` and `ROLLBACK TO SAVEPOINT` statements are ignored, as are the inspector's own queries |
| pytest | A plugin registered through a `pytest11` entry point, so it is only ever loaded by pytest. It provides an `assert_queries` fixture and an `inspector_queries` marker. |
| Dev mode | New setting `N_PLUS_ONE`: `"off"` (default), `"warn"` or `"raise"`. It acts in the SQL watcher when a shape reaches its third run in a trace, and only while `DEBUG` is on. |

Alternatives rejected:
- **Reading `connection.queries`:** needs `DEBUG` or `CaptureQueriesContext`, loses the call stack, and repeats Django's `assertNumQueries`.
- **Running the guard through a `trace()`:** writes events to the database during tests and depends on sampling.
- **Asking users to add `pytest_plugins = [...]` themselves:** more setup for no gain, since the entry point is inert outside pytest.

## Package layout

- `django_inspector/testing/__init__.py`: exports `assert_queries` and `QueryBudgetExceeded`.
- `django_inspector/testing/guard.py`: the implementation.
- `django_inspector/testing/pytest_plugin.py`: the fixture and marker. It imports nothing from Django at module import time, because pytest loads plugins before settings are configured.
- `pyproject.toml`: `[project.entry-points.pytest11] django_inspector = "django_inspector.testing.pytest_plugin"`.

## `assert_queries` (`testing/guard.py`)

```python
with assert_queries(max=5, no_n_plus_one=True, no_duplicates=True) as guard:
    client.get("/orders/")
guard.queries   # list of CapturedQuery(sql, params, alias, duration_ms, file, line, function)
```

- **`using`:** `None` guards every alias in `connections`, a string guards one alias, and a list guards those aliases.
- **Entering:**
  - For each guarded alias, it enters `connections[alias].execute_wrapper(self._wrapper)`, collected in an `ExitStack`.
  - This applies to the connection objects of the current thread, which is where Django's test client and `sync_to_async(thread_sensitive=True)` run them.
- **`_wrapper(execute, sql, params, many, context)`:**
  - It skips recording for savepoint statements and `_is_inspector_query(sql)`.
  - Otherwise it times the call and records a `CapturedQuery` with `extract_origin()`.
  - It always calls `execute` exactly once. Its own bookkeeping is wrapped so that a failure there can't break the query (logged with `logger.warning`).
- **Leaving:**
  - On a normal exit, it evaluates the budget and raises `QueryBudgetExceeded` if broken.
  - When the block raised, it re-raises the block's exception and doesn't evaluate the budget (the real error matters more).
- **Budget checks:**
  - `max`: `len(queries) > max` fails.
  - `no_n_plus_one`: any shape with 3 or more queries fails.
  - `no_duplicates`: any exact `(sql, params-key)` repeated fails. The params key is the SQL watcher's `_params_key`.
- **Failure message:**
  - The first line is a summary, e.g. `Query budget exceeded: 12 queries (max 5); 1 N+1 shape; 2 duplicate queries`.
  - Then one block per failing rule, listing each shape (truncated to 300 characters) with its count and up to 3 distinct `file:line (function)` origins, most frequent first.
  - With `max` exceeded, the ten most frequent shapes follow.
  - Params are not printed: they can hold secrets, and the shape is what matters.
- **Decorator use:**
  - On a function, including a test method, `ContextDecorator` semantics apply: each call gets a fresh guard, and async functions are wrapped with `async def`.
  - On a class, every attribute starting with `test` that is callable is wrapped. Classes that aren't `unittest.TestCase`-like are rejected with `TypeError`.
- **Argument validation:** `max` must be a non-negative int or `None`. Unknown aliases raise `KeyError` at enter.

## pytest plugin (`testing/pytest_plugin.py`)

- **Fixture `assert_queries`:** returns the `assert_queries` factory, so a test can write `with assert_queries(max=3): ...` without importing it.
- **Marker `@pytest.mark.inspector_queries(max=..., no_n_plus_one=..., no_duplicates=..., using=...)`:** wraps the whole test call in one guard. It is implemented with a `pytest_runtest_call` hookwrapper and registered with `config.addinivalue_line("markers", ...)`. Database access still needs `pytest-django`'s `db` fixture as usual; the marker doesn't grant it.
- Django is imported lazily inside the fixture and hook, never at plugin import.

## Development mode (`N_PLUS_ONE`, TEST-04)

- In `watchers/sql.py`'s `_query_wrapper`, after `similar_count` is computed, the check fires when `similar_count == 3`, i.e. once per shape per trace, and only if the setting isn't `"off"` and `django.conf.settings.DEBUG` is True.
- **`"warn"`:** `logger.warning("django-inspector: N+1 query (%d× so far) from %s:%s: %s", ...)`.
- **`"raise"`:**
  - It raises `NPlusOneDetected(Exception)`, defined in `testing/guard.py` and re-exported, with the shape and origin.
  - The raise is outside the watcher's usual catch-and-log handling: surfacing the query is the purpose of this mode. The query itself has already run when the error is raised.
- When the setting is `"off"` or `DEBUG` is off, the only added cost is one string comparison per query.

## Settings and checks

| Key | Default | Rule |
|---|---|---|
| `N_PLUS_ONE` | `"off"` | `"off"`, `"warn"` or `"raise"` |

It gets a README row and a `checks.py` rule.

## Testing

| File | Covers |
|---|---|
| `tests/test_query_guard.py` (new) | Passing and failing `max`; N+1 detection with its message (shape, count, `file:line`); duplicates; savepoints and inspector queries ignored; works with `INSPECTOR_ENABLED=False`, with the SQL watcher disabled and with no middleware; writes no `Event` rows; `guard.queries` contents; the block's own exception wins over a budget failure; `using` with one alias, a list and an unknown alias; function, async function, `TestCase` method and class decorators; wrapper bookkeeping failure doesn't break the query; the message never contains param values |
| `tests/test_query_guard_pytest.py` (new) | Uses `pytester` to run a tiny test module: the fixture works; the marker fails a test that exceeds its budget and passes one that doesn't; the plugin module imports without Django configured |
| `tests/test_sql_watcher.py` | `N_PLUS_ONE="warn"` logs once per shape at the third query; `"raise"` raises `NPlusOneDetected` inside a request under `DEBUG`; nothing happens with `DEBUG` off or `"off"` |
| `tests/test_checks.py` | `N_PLUS_ONE` accepted values and an invalid value |
| `tests/test_docs.py` | README row (existing check) |

`pytester` needs `-p pytester`. Enable it in `tests/conftest.py` with `pytest_plugins = ["pytester"]`; if there's no conftest, add one.

Then the full gates. The benchmark must not regress, since the `N_PLUS_ONE` check is one comparison per query.

## Docs

- README: a "Catching N+1 queries in tests" section (context manager, decorator, fixture, marker, example failure message, the thread note) and the `N_PLUS_ONE` row.
- CHANGELOG: the 0.3.0 section, citing TEST-01..04.
- Roadmap and requirements: tick Phase 11 and TEST-01..04.

## Assumptions (for the owner's end-of-milestone review)

1. **Savepoint statements don't count** toward `max`. Django's `assertNumQueries` does count them, so numbers can differ by the savepoints a test makes.
2. **The N+1 threshold is 3 queries of the same shape,** the same as the SQL watcher. It isn't configurable.
3. **The pytest plugin loads automatically** through the `pytest11` entry point. That adds one fixture and one marker to every pytest run in projects that install django-inspector.
4. **The guard only sees queries in the thread it was entered in.** That covers the test client and thread-sensitive `sync_to_async`. Queries from other threads (e.g. a thread pool) aren't seen.
5. **`N_PLUS_ONE="raise"` only acts under `DEBUG`** and inside traced code (requests, `trace()`, commands). It is a development aid, not a test tool.
