"""
Tests verifying async-safe ContextVar isolation in flush.py and sql.py.

Requirements: ASYNC-01, ASYNC-02
"""

import asyncio

import pytest
from django.test import TestCase, override_settings

from django_inspector.storage.flush import _buffer_var, buffer_event, clear_buffer
from django_inspector.watchers.sql import _query_log_var, _get_query_log, clear_query_log


class TestFlushContextVarIsolation(TestCase):
    """Verify flush.py buffer is isolated per async context (ASYNC-02)."""

    def test_buffer_starts_empty_in_fresh_context(self):
        _buffer_var.set(None)
        from django_inspector.storage.flush import _get_buffer
        buf = _get_buffer()
        assert buf == []

    def test_clear_buffer_empties_current_context(self):
        _buffer_var.set(None)
        from django_inspector.storage.flush import _get_buffer
        buf = _get_buffer()
        buf.append({"fake": "event"})
        clear_buffer()
        assert _get_buffer() == []

    def test_two_coroutines_have_isolated_buffers(self):
        """Each async context gets its own buffer — no cross-contamination."""

        buffer_a = []
        buffer_b = []

        async def task_a():
            _buffer_var.set(None)
            from django_inspector.storage.flush import _get_buffer
            buf = _get_buffer()
            buf.append("a_event")
            await asyncio.sleep(0)
            buffer_a.extend(_get_buffer())

        async def task_b():
            _buffer_var.set(None)
            from django_inspector.storage.flush import _get_buffer
            buf = _get_buffer()
            buf.append("b_event")
            await asyncio.sleep(0)
            buffer_b.extend(_get_buffer())

        async def run():
            await asyncio.gather(task_a(), task_b())

        asyncio.run(run())

        assert buffer_a == ["a_event"], f"task_a saw: {buffer_a}"
        assert buffer_b == ["b_event"], f"task_b saw: {buffer_b}"
        assert "b_event" not in buffer_a, "cross-contamination: b_event in task_a's buffer"
        assert "a_event" not in buffer_b, "cross-contamination: a_event in task_b's buffer"


class TestQueryLogContextVarIsolation(TestCase):
    """Verify sql.py query log is isolated per async context (ASYNC-01)."""

    def test_query_log_starts_empty_in_fresh_context(self):
        _query_log_var.set(None)
        log = _get_query_log()
        assert log == []

    def test_clear_query_log_empties_current_context(self):
        _query_log_var.set(None)
        log = _get_query_log()
        log.append({"sql": "SELECT 1"})
        clear_query_log()
        assert _get_query_log() == []

    def test_two_coroutines_have_isolated_query_logs(self):
        """Each async context gets its own query log — no cross-contamination."""

        log_a = []
        log_b = []

        async def task_a():
            _query_log_var.set(None)
            q = _get_query_log()
            q.append("query_a")
            await asyncio.sleep(0)
            log_a.extend(_get_query_log())

        async def task_b():
            _query_log_var.set(None)
            q = _get_query_log()
            q.append("query_b")
            await asyncio.sleep(0)
            log_b.extend(_get_query_log())

        async def run():
            await asyncio.gather(task_a(), task_b())

        asyncio.run(run())

        assert log_a == ["query_a"], f"task_a saw: {log_a}"
        assert log_b == ["query_b"], f"task_b saw: {log_b}"
        assert "query_b" not in log_a, "cross-contamination: query_b in task_a's log"
        assert "query_a" not in log_b, "cross-contamination: query_a in task_b's log"
