# Conventions

> **Snapshot:** written at v1.0 (2026-05-23), before Phase 4 added `masking.py`, `sampling.py`, `ignores.py`, `dashboard/auth.py` and the ContextVar buffers. Check the code before relying on details here, and refresh this file when you touch the area it describes.

Patterns and conventions established by the existing codebase. New code in v1.1+ should follow these unless there's a documented reason to break.

## Naming

- **Package**: `django_inspector` (underscore — required by Django app loading).
- **Distribution**: `django-inspector` on PyPI / pyproject (hyphenated).
- **Watcher class**: PascalCase ending in `Watcher` (e.g., `RequestWatcher`, `SQLWatcher`, `ExceptionWatcher`).
- **`watcher_name` class attr**: lowercase singular noun matching the module name (`"request"`, `"sql"`, `"exception"`). This is the key used in the `WATCHERS` settings dict and the registry.
- **Event types**: dotted strings `{watcher_name}.{event_kind}`, e.g. `request.completed`, `sql.query`, `exception.raised`.
- **Settings keys**: SCREAMING_SNAKE_CASE in the `DJANGO_INSPECTOR` dict (`INSPECTOR_ENABLED`, `SQL_SLOW_THRESHOLD_MS`).
- **Private helpers**: prefixed `_` (e.g., `_extract_origin`, `_safe_params`, `_extract_headers`).
- **Module-level state**: prefixed `_` (e.g., `_registry`, `_local`, `_trace_id_var`).

## Module Structure

A watcher module follows this shape:

```python
"""
<Watcher Name> — one-paragraph description.

Requirements: REQ-XX..REQ-YY
"""

import ...

from django_inspector.conf import inspector_settings  # if needed
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")


class FooWatcher(BaseWatcher):
    """Docstring referencing requirements."""

    watcher_name = "foo"

    def install_hooks(self):
        ...

    def remove_hooks(self):
        ...

    # any handler methods called by hooks


# module-level private helpers below the class

# Auto-register on import
register("foo", FooWatcher)
```

The `register(...)` call at the bottom triggers on import; `AppConfig.ready()` imports the module to make this happen.

## Settings Access

Always go through `inspector_settings`, never `django.conf.settings.DJANGO_INSPECTOR` directly:

```python
from django_inspector.conf import inspector_settings

if inspector_settings.is_enabled: ...
threshold = inspector_settings.SQL_SLOW_THRESHOLD_MS
```

This is a lazy accessor that re-reads on every call, so test overrides (`@override_settings`) work transparently.

## Event Recording

Watchers buffer via `self.record(event_type, metadata)`. The base class:

- No-ops if the watcher is disabled.
- No-ops if `inspector_settings.is_enabled` is false.
- No-ops if there is no active `trace_id` (i.e., the request didn't enter through `InspectorMiddleware`).
- Otherwise delegates to `buffer_event(trace_id, event_type, metadata)`.

**Always** use `self.record(...)`. Do not call `buffer_event` directly from a watcher — that bypasses the enable check.

`metadata` must be JSON-serializable. Helpers like `_safe_params`, `_safe_locals` exist to coerce arbitrary values (use `repr()`, truncate to a max length).

## Logging

A single named logger `logging.getLogger("django_inspector")` is used package-wide. Levels:

- `DEBUG` for normal lifecycle events (`watcher enabled/disabled`).
- Errors inside watchers are currently swallowed silently (`try/except Exception: pass`) — this is the convention today, though it should be improved to `logger.exception(...)`.

## Error Handling

Convention so far: **never let the inspector break the host application**.

- Middleware notification methods wrap everything in `try/except Exception: pass`.
- SQL wrapper catches `traceback.extract_stack` failures via `extract_origin` having defensive defaults.
- `_safe_params` / `_safe_locals` / `_safe_body` all return safe defaults on `Exception`.

This trades observability of bugs for robustness. Acceptable for v1; v1.1 should at least log at `WARNING`.

## Defensive Coding Idioms

- Use `getattr(obj, "attr", default)` rather than direct attribute access on Django objects that may not have what you expect (e.g., anonymous user, missing session).
- Always check `if foo is None` before using a trace id / user / session.
- Truncate everything that lands in `metadata`: bodies (`MAX_BODY_SIZE`), locals (200 chars per value), strings derived from user objects.

## Testing Conventions

- Test files mirror source: `tests/test_<module>.py`.
- Uses `pytest`, not `unittest.TestCase` (functions named `test_*`).
- Heavy use of `pytest.fixture` and `django_db`.
- Tests for watchers assert against `Event` rows (after flush) and / or against the in-memory buffer (`from django_inspector.storage.flush import _get_buffer`).
- A trace id is established in tests by manually calling `set_trace_id(...)` because tests typically don't go through real middleware.

## Templates

- All dashboard templates extend `inspector/base.html`.
- Filenames `list.html`, `detail.html` for full pages, `_table.html` and `_pagination.html` for partials.
- View pattern: detect `HX-Request` header → return partial; else return full page.
- Filter params come from `request.GET` and are echoed back into context as `filters` for re-display.

## Migrations

- Single migration so far: `0001_initial.py`. New model fields require new numbered migrations; do not edit `0001_initial.py`.
- App label is hard-coded: `app_label = "django_inspector"` in the model `Meta`.

## Git / Commit Conventions

From history:

- Conventional commits: `feat(<scope>):`, `chore:`, `docs(<scope>):`.
- Scope is typically `<phase>-<plan>` (e.g., `feat(03-03a):`).
- Commit messages name what shipped, often with test count (`20 tests passing`).
- Documentation/state commits live alongside code commits.

## What's NOT a Convention Yet

- No type hints policy — code mixes typed and untyped (`def _safe_params(params) -> list | str | None:` is typed, `def _extract_origin() -> dict:` is partially typed). v1.1 should pick a stance.
- No docstring style mandated — most are free-form, some reference `REQ-` IDs.
- No linter / formatter configured in `pyproject.toml`.
- No pre-commit hooks set up.
