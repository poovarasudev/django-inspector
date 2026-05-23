"""
SQL Watcher — captures every SQL query executed during a request with
full metadata, timing, origin file/line, and pattern detection.

Requirements: SQL-01..SQL-07
"""

import logging
import time
import traceback
from contextvars import ContextVar

from django.db import connections

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")

_query_log_var: ContextVar = ContextVar("inspector_query_log", default=None)


def _get_query_log():
    """Per-request query log for N+1 and duplicate detection (ContextVar-based, async-safe)."""
    log = _query_log_var.get()
    if log is None:
        log = []
        _query_log_var.set(log)
    return log


def clear_query_log():
    """Clear the per-request query log. Called at end-of-request."""
    log = _query_log_var.get()
    if log is not None:
        log.clear()


class SQLWatcher(BaseWatcher):
    """
    Captures SQL queries via Django's database instrumentation API.

    - SQL-01: Raw SQL and bound parameters
    - SQL-02: Query execution time
    - SQL-03: Database alias
    - SQL-04: Origin file and line number
    - SQL-05: Slow query flagging (configurable threshold)
    - SQL-06: N+1 pattern detection
    - SQL-07: Duplicate query detection
    """

    watcher_name = "sql"

    def install_hooks(self):
        """Install instrumentation on all database connections."""
        for alias in connections:
            connection = connections[alias]
            self._install_on_connection(connection)
        # Also hook connection_created signal for new connections
        from django.db.backends.signals import connection_created
        connection_created.connect(self._on_connection_created)

    def remove_hooks(self):
        """Remove instrumentation from all database connections."""
        for alias in connections:
            connection = connections[alias]
            self._remove_from_connection(connection)
        from django.db.backends.signals import connection_created
        connection_created.disconnect(self._on_connection_created)

    def _on_connection_created(self, sender, connection, **kwargs):
        """Hook new database connections as they're created."""
        self._install_on_connection(connection)

    def _install_on_connection(self, connection):
        """Add our instrumentation wrapper to a connection."""
        if not hasattr(connection, "_inspector_instrumented"):
            connection.execute_wrappers.append(_query_wrapper)
            connection._inspector_instrumented = True

    def _remove_from_connection(self, connection):
        """Remove our instrumentation wrapper from a connection."""
        if hasattr(connection, "_inspector_instrumented"):
            try:
                connection.execute_wrappers.remove(_query_wrapper)
            except ValueError:
                pass
            del connection._inspector_instrumented


def _query_wrapper(execute, sql, params, many, context):
    """
    Database instrumentation wrapper. Wraps every query to capture timing
    and metadata, then delegates to the original executor.
    """
    trace_id = get_current_trace_id()
    if trace_id is None or not inspector_settings.is_enabled:
        return execute(sql, params, many, context)

    # Check if this is an inspector query (avoid recording our own queries)
    if _is_inspector_query(sql):
        return execute(sql, params, many, context)

    start = time.monotonic()
    try:
        result = execute(sql, params, many, context)
    finally:
        duration_ms = round((time.monotonic() - start) * 1000, 2)

    connection = context.get("connection")
    db_alias = getattr(connection, "alias", "default") if connection else "default"

    # SQL-04: Origin file and line
    origin = _extract_origin()

    # SQL-05: Slow query flag
    slow_threshold = inspector_settings.SQL_SLOW_THRESHOLD_MS
    is_slow = duration_ms >= slow_threshold

    # Build metadata
    metadata = {
        "sql": sql,
        "params": _safe_params(params),
        "duration_ms": duration_ms,
        "db_alias": db_alias,
        "origin_file": origin.get("file"),
        "origin_line": origin.get("line"),
        "origin_function": origin.get("function"),
        "is_slow": is_slow,
    }

    # Track for N+1 and duplicate detection
    query_log = _get_query_log()
    query_log.append({"sql": sql, "params": _safe_params(params)})

    # SQL-06: N+1 detection — check for repeated similar queries
    n_plus_one = _detect_n_plus_one(sql, query_log)
    metadata["n_plus_one"] = n_plus_one

    # SQL-07: Duplicate detection — exact same SQL+params
    duplicate_count = _detect_duplicates(sql, params, query_log)
    metadata["is_duplicate"] = duplicate_count > 1
    metadata["duplicate_count"] = duplicate_count

    # Record via the watcher's record() method (which goes through base class)
    from django_inspector.watchers import registry
    watcher_cls = registry.get("sql")
    if watcher_cls is not None:
        from django.apps import apps
        try:
            app = apps.get_app_config("django_inspector")
            for inst in getattr(app, "_watcher_instances", []):
                if isinstance(inst, SQLWatcher) and inst.is_enabled:
                    inst.record("sql.query", metadata)
                    break
        except Exception:
            pass

    return result


def _is_inspector_query(sql: str) -> bool:
    """Check if a query is from django-inspector itself (avoid recursion)."""
    sql_lower = sql.lower().strip()
    return "django_inspector_event" in sql_lower


def _extract_origin() -> dict:
    """
    Walk the call stack to find the application code that triggered the query.
    Skips django internals and django-inspector's own frames.
    """
    skip_patterns = (
        "django_inspector",
        "django/db",
        "django/core",
        "django/utils",
        "django/test",
    )
    for frame_info in reversed(traceback.extract_stack()):
        filename = frame_info.filename
        # Skip Python internals, Django internals, and our own code
        if any(pat in filename for pat in skip_patterns):
            continue
        if "site-packages" in filename:
            continue
        if "<" in filename:  # <frozen>, <string>, etc.
            continue
        # Skip standard library modules
        if "/lib/python" in filename and "/tests/" not in filename:
            continue
        return {
            "file": filename,
            "line": frame_info.lineno,
            "function": frame_info.name,
        }
    return {"file": None, "line": None, "function": None}


def _safe_params(params) -> list | str | None:
    """Safely convert query params to a serializable format."""
    if params is None:
        return None
    try:
        if isinstance(params, (list, tuple)):
            return [str(p) for p in params]
        if isinstance(params, dict):
            return {str(k): str(v) for k, v in params.items()}
        return str(params)
    except Exception:
        return "<unserializable>"


def _detect_n_plus_one(sql: str, query_log: list) -> bool:
    """
    Detect N+1 pattern: 3+ similar queries for the same table with
    different parameters (typically different PKs).

    SQL-06: Repeated queries for same table with different PKs.
    """
    if len(query_log) < 3:
        return False

    # Normalize SQL: strip parameter values to get a "template"
    normalized = _normalize_sql(sql)
    similar_count = sum(1 for q in query_log if _normalize_sql(q["sql"]) == normalized)
    return similar_count >= 3


def _detect_duplicates(sql: str, params, query_log: list) -> int:
    """
    Detect duplicate identical queries (same SQL + same params).

    SQL-07: Exact duplicates within a single request.
    """
    safe_params = _safe_params(params)
    count = 0
    for q in query_log:
        if q["sql"] == sql and q["params"] == safe_params:
            count += 1
    return count


def _normalize_sql(sql: str) -> str:
    """
    Normalize SQL by replacing literal values with placeholders.
    Used for N+1 pattern comparison.
    """
    import re
    # Replace quoted strings
    normalized = re.sub(r"'[^']*'", "'?'", sql)
    # Replace numbers
    normalized = re.sub(r"\b\d+\b", "?", normalized)
    # Replace parameter placeholders (%s, ?)
    normalized = re.sub(r"%s", "?", normalized)
    return normalized.strip()


# Auto-register on import
register("sql", SQLWatcher)
