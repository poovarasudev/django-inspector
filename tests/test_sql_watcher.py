"""Tests for the SQL Watcher (SQL-01..SQL-07)."""

import pytest
from django.test import TestCase, override_settings
from django.db import connection

from django_inspector.watchers.sql import (
    SQLWatcher,
    MAX_PARAM_LENGTH,
    MAX_PARAMS,
    MAX_SQL_LENGTH,
    _QueryStats,
    _normalize_sql,
    _is_inspector_query,
    _safe_params,
    clear_query_log,
)
from django_inspector.tracing.context import set_trace_id, clear_trace_id, generate_trace_id
from django_inspector.storage.flush import _get_buffer, clear_buffer


@pytest.fixture(autouse=True)
def _clear_state():
    """Clear event buffer and query log before/after each test."""
    clear_buffer()
    clear_query_log()
    yield
    clear_buffer()
    clear_query_log()


class TestSQLWatcher(TestCase):
    def setUp(self):
        self.watcher = SQLWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()
        clear_query_log()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()
        clear_query_log()

    def test_watcher_name(self):
        assert self.watcher.watcher_name == "sql"

    def test_captures_query(self):
        """SQL-01/02/03: Captures SQL, params, timing, db alias."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

        buffer = _get_buffer()
        # Find sql.query events (filter out any non-sql events)
        sql_events = [e for e in buffer if e["event_type"] == "sql.query"]
        assert len(sql_events) >= 1

        event = sql_events[-1]
        meta = event["metadata"]
        assert "SELECT" in meta["sql"]
        assert meta["duration_ms"] >= 0
        assert meta["db_alias"] == "default"

    def test_captures_origin(self):
        """SQL-04: Captures origin file and line."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

        buffer = _get_buffer()
        sql_events = [e for e in buffer if e["event_type"] == "sql.query"]
        assert len(sql_events) >= 1

        meta = sql_events[-1]["metadata"]
        # Origin should point to this test file
        assert meta["origin_file"] is not None
        assert "test_sql_watcher" in meta["origin_file"]
        assert meta["origin_line"] is not None

    def test_slow_query_flag(self):
        """SQL-05: Flags slow queries."""
        # A SELECT 1 should be fast (not slow)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

        buffer = _get_buffer()
        sql_events = [e for e in buffer if e["event_type"] == "sql.query"]
        meta = sql_events[-1]["metadata"]
        assert meta["is_slow"] is False

    def test_excludes_inspector_queries(self):
        """Inspector's own queries should not be recorded."""
        assert _is_inspector_query("SELECT * FROM django_inspector_event WHERE id = 1")
        assert not _is_inspector_query("SELECT * FROM auth_user WHERE id = 1")

    def test_does_not_record_without_trace(self):
        """No events recorded when there's no active trace."""
        clear_trace_id(self.token)
        self.token = None  # Mark as already cleared to avoid double-reset in tearDown
        clear_buffer()

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

        buffer = _get_buffer()
        sql_events = [e for e in buffer if e["event_type"] == "sql.query"]
        assert len(sql_events) == 0


class TestNormalizeSql:
    def test_replaces_numbers(self):
        result = _normalize_sql("SELECT * FROM users WHERE id = 42")
        assert "42" not in result
        assert "?" in result

    def test_replaces_strings(self):
        result = _normalize_sql("SELECT * FROM users WHERE name = 'john'")
        assert "john" not in result
        assert "'?'" in result

    def test_replaces_params(self):
        result = _normalize_sql("SELECT * FROM users WHERE id = %s")
        assert "%s" not in result


class TestNPlusOneDetection:
    def test_detects_repeated_queries(self):
        """SQL-06: the third similar query is flagged as N+1."""
        stats = _QueryStats()
        for i in (1, 2):
            similar, _ = stats.add("SELECT * FROM books WHERE author_id = %d" % i, None)
            assert similar < 3
        similar, _ = stats.add("SELECT * FROM books WHERE author_id = 3", None)
        assert similar == 3

    def test_different_tables_are_not_similar(self):
        stats = _QueryStats()
        stats.add("SELECT * FROM books WHERE id = 1", None)
        stats.add("SELECT * FROM books WHERE id = 2", None)
        similar, _ = stats.add("SELECT * FROM authors WHERE id = 3", None)
        assert similar == 1


class TestDuplicateDetection:
    def test_detects_exact_duplicates(self):
        """SQL-07: identical SQL and params are counted."""
        stats = _QueryStats()
        stats.add("SELECT * FROM users WHERE id = %s", [1])
        _, exact = stats.add("SELECT * FROM users WHERE id = %s", [1])
        assert exact == 2

    def test_different_params_are_not_duplicates(self):
        stats = _QueryStats()
        stats.add("SELECT * FROM users WHERE id = %s", [1])
        _, exact = stats.add("SELECT * FROM users WHERE id = %s", [2])
        assert exact == 1

    def test_clear_resets_counts(self):
        stats = _QueryStats()
        stats.add("SELECT 1", None)
        stats.clear()
        assert len(stats) == 0
        assert stats.add("SELECT 1", None) == (1, 1)


class TestDetectionScalesLinearly(TestCase):
    """Detection used to re-normalise every earlier query: O(n^2) per request."""

    def setUp(self):
        self.watcher = SQLWatcher()
        self.watcher.enable()
        self.token = set_trace_id(generate_trace_id())
        clear_query_log()

    def tearDown(self):
        self.watcher.disable()
        clear_trace_id(self.token)

    def test_each_query_is_normalised_once(self):
        from unittest import mock

        import django_inspector.watchers.sql as sql_module

        calls = []
        original = sql_module._normalize_sql

        def counting(sql):
            calls.append(sql)
            return original(sql)

        with mock.patch.object(sql_module, "_normalize_sql", counting):
            with connection.cursor() as cursor:
                for i in range(300):
                    cursor.execute("SELECT %s", [i])
        assert len(calls) == 300


class TestCaptureLimits(TestCase):
    def setUp(self):
        self.watcher = SQLWatcher()
        self.watcher.enable()
        self.token = set_trace_id(generate_trace_id())
        clear_buffer()
        clear_query_log()

    def tearDown(self):
        self.watcher.disable()
        clear_trace_id(self.token)

    def _last_query(self):
        return [e for e in _get_buffer() if e["event_type"] == "sql.query"][-1]["metadata"]

    @override_settings(DJANGO_INSPECTOR={"SQL_CAPTURE_PARAMS": False})
    def test_params_can_be_turned_off(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s", ["tok-123"])
            cursor.execute("SELECT %s", ["tok-123"])
        meta = self._last_query()
        assert meta["params"] is None
        assert meta["params_omitted"] is True
        assert meta["is_duplicate"] is True  # detection still sees the params

    def test_long_sql_is_truncated(self):
        long_sql = "SELECT 1" + " " * MAX_SQL_LENGTH + "-- end"
        with connection.cursor() as cursor:
            cursor.execute(long_sql)
        meta = self._last_query()
        assert len(meta["sql"]) == MAX_SQL_LENGTH
        assert meta["sql_truncated"] is True

    def test_params_are_bounded(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s", ["x" * (MAX_PARAM_LENGTH * 3)])
        param = self._last_query()["params"][0]
        assert len(param) == MAX_PARAM_LENGTH + len("...")

    def test_param_count_is_bounded(self):
        placeholders = ", ".join(["%s"] * (MAX_PARAMS + 20))
        with connection.cursor() as cursor:
            cursor.execute("SELECT " + placeholders, list(range(MAX_PARAMS + 20)))
        meta = self._last_query()
        assert len(meta["params"]) == MAX_PARAMS
        assert meta["params_truncated"] is True


class TestSafeParams:
    def test_none_params(self):
        assert _safe_params(None) is None

    def test_list_params(self):
        result = _safe_params([1, "hello"])
        assert result == ["1", "hello"]

    def test_dict_params(self):
        result = _safe_params({"id": 42})
        assert result == {"id": "42"}
