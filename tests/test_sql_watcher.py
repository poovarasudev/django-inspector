"""Tests for the SQL Watcher (SQL-01..SQL-07)."""

import pytest
from django.test import TestCase, override_settings
from django.db import connection

from django_inspector.watchers.sql import (
    SQLWatcher,
    _normalize_sql,
    _detect_n_plus_one,
    _detect_duplicates,
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
        """SQL-06: Detects N+1 when 3+ similar queries exist."""
        query_log = [
            {"sql": "SELECT * FROM books WHERE author_id = 1", "params": ["1"]},
            {"sql": "SELECT * FROM books WHERE author_id = 2", "params": ["2"]},
            {"sql": "SELECT * FROM books WHERE author_id = 3", "params": ["3"]},
        ]
        assert _detect_n_plus_one("SELECT * FROM books WHERE author_id = 4", query_log) is True

    def test_no_false_positive(self):
        """No N+1 for fewer than 3 similar queries."""
        query_log = [
            {"sql": "SELECT * FROM books WHERE author_id = 1", "params": ["1"]},
        ]
        assert _detect_n_plus_one("SELECT * FROM books WHERE author_id = 2", query_log) is False


class TestDuplicateDetection:
    def test_detects_exact_duplicates(self):
        """SQL-07: Detects identical queries."""
        query_log = [
            {"sql": "SELECT * FROM users WHERE id = 1", "params": ["1"]},
            {"sql": "SELECT * FROM users WHERE id = 1", "params": ["1"]},
        ]
        count = _detect_duplicates("SELECT * FROM users WHERE id = 1", ["1"], query_log)
        assert count == 2

    def test_no_false_positive_different_params(self):
        """Different params = not a duplicate."""
        query_log = [
            {"sql": "SELECT * FROM users WHERE id = 1", "params": ["1"]},
        ]
        count = _detect_duplicates("SELECT * FROM users WHERE id = 1", ["2"], query_log)
        assert count == 0


class TestSafeParams:
    def test_none_params(self):
        assert _safe_params(None) is None

    def test_list_params(self):
        result = _safe_params([1, "hello"])
        assert result == ["1", "hello"]

    def test_dict_params(self):
        result = _safe_params({"id": 42})
        assert result == {"id": "42"}
