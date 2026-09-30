"""
assert_queries() — a query budget guard for tests, independent of tracing.

Installs Django's ``connection.execute_wrapper()`` on the guarded aliases'
connections (no trace, middleware, buffer or database writes), times and
records every query that runs inside the block, and evaluates the budget on
a clean exit. Detection reuses the SQL watcher's own building blocks
(``normalize_sql`` for shapes, ``_is_inspector_query``, ``_params_key``), so
a query counts as an N+1 or a duplicate exactly the way the SQL watcher
would flag it.

Requirements: TEST-01..03 (TEST-04 is the SQL watcher's dev-mode reporting).
"""

import asyncio
import functools
import logging
import time
import unittest
from collections import Counter, defaultdict
from contextlib import ExitStack
from typing import List, NamedTuple, Optional

from django.db import connections

from django_inspector.storage.columns import normalize_sql
from django_inspector.watchers.sql import _is_inspector_query, _params_key

logger = logging.getLogger("django_inspector")

MAX_SHAPE_LENGTH = 300     # characters of a query shape shown in a failure message
MAX_ORIGINS_SHOWN = 3      # distinct file:line origins shown per shape
MAX_SHAPES_SHOWN = 10      # shapes listed when `max` is the failing rule

_SAVEPOINT_PREFIXES = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


class QueryBudgetExceeded(AssertionError):
    """Raised by assert_queries() when the block broke its query budget."""


class NPlusOneDetected(Exception):
    """Raised by the SQL watcher's dev mode (N_PLUS_ONE="raise"); see watchers/sql.py."""


class CapturedQuery(NamedTuple):
    sql: str
    params: object
    alias: str
    duration_ms: float
    file: Optional[str]
    line: Optional[int]
    function: Optional[str]


def _is_savepoint(sql: str) -> bool:
    return sql.strip().upper().startswith(_SAVEPOINT_PREFIXES)


def _validate_max(max_queries):
    if max_queries is None:
        return
    if isinstance(max_queries, bool) or not isinstance(max_queries, int):
        raise TypeError(
            "assert_queries(max=...) must be a non-negative int or None; got %r" % (max_queries,)
        )
    if max_queries < 0:
        raise ValueError(
            "assert_queries(max=...) must be a non-negative int or None; got %r" % (max_queries,)
        )


def _pluralize(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + "s")


def _group_by_shape(queries: List[CapturedQuery]):
    groups = defaultdict(list)
    for query in queries:
        groups[normalize_sql(query.sql)].append(query)
    return groups


def _group_by_exact(queries: List[CapturedQuery]):
    groups = defaultdict(list)
    for query in queries:
        groups[(query.sql, _params_key(query.params))].append(query)
    return groups


def _format_block(title, shapes):
    """``shapes``: an ordered list of (shape text, [CapturedQuery, ...])."""
    lines = ["%s:" % title]
    for shape, queries in shapes:
        origins = Counter((q.file, q.line, q.function) for q in queries)
        top_origins = sorted(origins.items(), key=lambda kv: kv[1], reverse=True)[:MAX_ORIGINS_SHOWN]
        lines.append("  %d× %s" % (len(queries), shape[:MAX_SHAPE_LENGTH]))
        for (file, line, function), _origin_count in top_origins:
            lines.append("    %s:%s (%s)" % (file, line, function))
    return "\n".join(lines)


class assert_queries:
    """
    Context manager, function/async-function decorator and TestCase class
    decorator that fails when a block of code breaks a query budget.

        with assert_queries(max=5, no_n_plus_one=True, no_duplicates=True) as guard:
            client.get("/orders/")
        guard.queries  # list of CapturedQuery

    ``using``: None guards every alias in ``connections``, a string guards
    one alias, and a list guards those aliases. An unknown alias raises
    KeyError when the guard is entered.
    """

    def __init__(self, max=None, no_n_plus_one=False, no_duplicates=False, using=None):
        _validate_max(max)
        self.max = max
        self.no_n_plus_one = no_n_plus_one
        self.no_duplicates = no_duplicates
        self.using = using
        self.queries: List[CapturedQuery] = []
        self._stack: Optional[ExitStack] = None

    # -- context manager -------------------------------------------------

    def __enter__(self):
        self.queries = []
        aliases = self._resolve_aliases()
        stack = ExitStack()
        try:
            for alias in aliases:
                stack.enter_context(connections[alias].execute_wrapper(self._make_wrapper(alias)))
        except Exception:
            stack.close()
            raise
        self._stack = stack
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        stack = self._stack
        self._stack = None
        assert stack is not None  # __exit__ always follows a successful __enter__
        stack.close()
        if exc_type is not None:
            return False
        message = self._budget_message()
        if message is not None:
            raise QueryBudgetExceeded(message)
        return False

    def _resolve_aliases(self):
        all_aliases = list(connections)
        if self.using is None:
            requested = all_aliases
        elif isinstance(self.using, str):
            requested = [self.using]
        else:
            requested = list(self.using)
        for alias in requested:
            if alias not in all_aliases:
                raise KeyError(alias)
        return requested

    # -- capture -----------------------------------------------------------

    def _make_wrapper(self, alias):
        def _wrapper(execute, sql, params, many, context):
            if _is_savepoint(sql) or _is_inspector_query(sql):
                return execute(sql, params, many, context)
            start = time.monotonic()
            try:
                result = execute(sql, params, many, context)
            finally:
                duration_ms = round((time.monotonic() - start) * 1000, 2)
            try:
                self._record(sql, params, alias, duration_ms)
            except Exception:
                logger.warning("django-inspector: assert_queries failed to record a query", exc_info=True)
            return result
        return _wrapper

    def _record(self, sql, params, alias, duration_ms):
        from django_inspector.watchers.utils import extract_origin

        origin = extract_origin()
        self.queries.append(CapturedQuery(
            sql=sql, params=params, alias=alias, duration_ms=duration_ms,
            file=origin.get("file"), line=origin.get("line"), function=origin.get("function"),
        ))

    # -- budget evaluation ---------------------------------------------------

    def _budget_message(self) -> Optional[str]:
        max_exceeded = self.max is not None and len(self.queries) > self.max

        shape_groups = _group_by_shape(self.queries) if (max_exceeded or self.no_n_plus_one) else {}
        n_plus_one_groups = (
            {shape: qs for shape, qs in shape_groups.items() if len(qs) >= 3}
            if self.no_n_plus_one else {}
        )
        duplicate_groups = (
            {key: qs for key, qs in _group_by_exact(self.queries).items() if len(qs) > 1}
            if self.no_duplicates else {}
        )

        if not (max_exceeded or n_plus_one_groups or duplicate_groups):
            return None

        parts = []
        if max_exceeded:
            count = len(self.queries)
            parts.append("%d %s (max %d)" % (count, _pluralize(count, "query", "queries"), self.max))
        if n_plus_one_groups:
            count = len(n_plus_one_groups)
            parts.append("%d N+1 %s" % (count, _pluralize(count, "shape")))
        if duplicate_groups:
            count = len(duplicate_groups)
            parts.append("%d duplicate %s" % (count, _pluralize(count, "query", "queries")))
        summary = "Query budget exceeded: " + "; ".join(parts)

        blocks = [summary]
        if max_exceeded:
            top = sorted(shape_groups.items(), key=lambda kv: len(kv[1]), reverse=True)[:MAX_SHAPES_SHOWN]
            blocks.append(_format_block("Most frequent queries", top))
        if n_plus_one_groups:
            top = sorted(n_plus_one_groups.items(), key=lambda kv: len(kv[1]), reverse=True)
            blocks.append(_format_block("N+1 queries", top))
        if duplicate_groups:
            top = sorted(duplicate_groups.items(), key=lambda kv: len(kv[1]), reverse=True)
            shaped = [(sql, qs) for (sql, _params_key_value), qs in top]
            blocks.append(_format_block("Duplicate queries", shaped))
        return "\n\n".join(blocks)

    # -- decorator use -----------------------------------------------------

    def __call__(self, target):
        if isinstance(target, type):
            return self._decorate_class(target)
        if asyncio.iscoroutinefunction(target):
            @functools.wraps(target)
            async def _async_wrapper(*args, **kwargs):
                with self._clone():
                    return await target(*args, **kwargs)
            return _async_wrapper

        @functools.wraps(target)
        def _wrapper(*args, **kwargs):
            with self._clone():
                return target(*args, **kwargs)
        return _wrapper

    def _clone(self) -> "assert_queries":
        """A fresh guard with this one's configuration — each decorated call gets its own."""
        return assert_queries(
            max=self.max, no_n_plus_one=self.no_n_plus_one,
            no_duplicates=self.no_duplicates, using=self.using,
        )

    def _decorate_class(self, cls):
        if not (isinstance(cls, type) and issubclass(cls, unittest.TestCase)):
            raise TypeError(
                "assert_queries can only decorate a unittest.TestCase subclass; got %r" % (cls,)
            )
        for name in dir(cls):
            if name.startswith("test") and callable(getattr(cls, name, None)):
                setattr(cls, name, self(getattr(cls, name)))
        return cls
