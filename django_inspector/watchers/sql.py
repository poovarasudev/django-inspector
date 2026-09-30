"""
SQL Watcher — captures every SQL query executed during a request with
full metadata, timing, origin file/line, and pattern detection.

Requirements: SQL-01..SQL-07
"""

import logging
import re
import time
from collections import Counter
from contextvars import ContextVar
from functools import lru_cache
from typing import Tuple, Union

from django.db import connections

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers import registry
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import extract_origin

logger = logging.getLogger("django_inspector")

MAX_SQL_LENGTH = 10000   # characters of SQL text stored per query
MAX_PARAMS = 100          # parameters stored per query
MAX_PARAM_LENGTH = 500    # characters stored per parameter


class _QueryStats:
    """
    Per-request counts for N+1 and duplicate detection. Each query is
    normalised once and counted in O(1), so detection stays linear in the
    number of queries (it used to re-scan every earlier query).
    """

    __slots__ = ("similar", "exact")

    def __init__(self):
        self.similar = Counter()  # normalised SQL -> count
        self.exact = Counter()    # hash of (SQL, params) -> count

    def add(self, sql, params) -> Tuple[int, int]:
        """Count one query; return (similar count, exact duplicate count)."""
        normalized = _normalize_sql(sql)
        self.similar[normalized] += 1
        key = hash((sql, _params_key(params)))
        self.exact[key] += 1
        return self.similar[normalized], self.exact[key]

    def clear(self):
        self.similar.clear()
        self.exact.clear()

    def __len__(self):
        return sum(self.similar.values())


_query_log_var: ContextVar = ContextVar("inspector_query_log", default=None)


def _get_query_log() -> _QueryStats:
    """Per-request query stats for N+1 and duplicate detection (ContextVar-based, async-safe)."""
    log = _query_log_var.get()
    if log is None:
        log = _QueryStats()
        _query_log_var.set(log)
    return log


def start_query_log():
    """Give the current request its own query stats; returns a token for reset_query_log()."""
    return _query_log_var.set(_QueryStats())


def reset_query_log(token):
    """Restore the query log the context had before start_query_log()."""
    _query_log_var.reset(token)


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
    origin = extract_origin()

    # SQL-05: Slow query flag
    slow_threshold = inspector_settings.SQL_SLOW_THRESHOLD_MS
    is_slow = duration_ms >= slow_threshold

    # SQL-06/07: N+1 (3+ similar queries) and exact duplicates, counted in O(1)
    similar_count, duplicate_count = _get_query_log().add(sql, params)

    metadata = {
        "sql": sql[:MAX_SQL_LENGTH],
        "duration_ms": duration_ms,
        "db_alias": db_alias,
        "origin_file": origin.get("file"),
        "origin_line": origin.get("line"),
        "origin_function": origin.get("function"),
        "is_slow": is_slow,
        "n_plus_one": similar_count >= 3,
        "is_duplicate": duplicate_count > 1,
        "duplicate_count": duplicate_count,
    }
    if len(sql) > MAX_SQL_LENGTH:
        metadata["sql_truncated"] = True
    if inspector_settings.SQL_CAPTURE_PARAMS:
        metadata["params"] = _safe_params(params)
        if isinstance(params, (list, tuple, dict)) and len(params) > MAX_PARAMS:
            metadata["params_truncated"] = True
    else:
        metadata["params"] = None
        metadata["params_omitted"] = True

    watcher = registry.get_instance("sql")
    if watcher is not None and watcher.is_enabled:
        try:
            watcher.record("sql.query", metadata)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording sql.query event", exc_info=True)

    return result


def _is_inspector_query(sql: str) -> bool:
    """Check if a query is from django-inspector itself (avoid recursion)."""
    sql_lower = sql.lower().strip()
    return "django_inspector_event" in sql_lower


def _safe_params(params) -> Union[list, dict, str, None]:
    """Serialisable, bounded copy of query params: at most MAX_PARAMS, each cut to MAX_PARAM_LENGTH."""
    if params is None:
        return None
    try:
        if isinstance(params, (list, tuple)):
            return [_short(p) for p in params[:MAX_PARAMS]]
        if isinstance(params, dict):
            items = list(params.items())[:MAX_PARAMS]
            return {str(k): _short(v) for k, v in items}
        return _short(params)
    except Exception:
        return "<unserializable>"


def _short(value) -> str:
    text = str(value)
    if len(text) > MAX_PARAM_LENGTH:
        return text[:MAX_PARAM_LENGTH] + "..."
    return text


def _params_key(params):
    """A hashable stand-in for params, used only for duplicate detection."""
    try:
        return repr(params)
    except Exception:
        return id(params)


_QUOTED = re.compile(r"'[^']*'")
_NUMBER = re.compile(r"\b\d+\b")


@lru_cache(maxsize=2048)
def _normalize_sql(sql: str) -> str:
    """
    Normalize SQL by replacing literal values with placeholders.
    Used for N+1 pattern comparison.
    """
    normalized = _QUOTED.sub("'?'", sql)
    normalized = _NUMBER.sub("?", normalized)
    return normalized.replace("%s", "?").strip()


# Auto-register on import
register("sql", SQLWatcher)
