"""
Tests for the public trace() API.

Requirements: TRACE-01, TRACE-03, TRACE-04, TRACE-07
"""

import asyncio
import logging
import subprocess
import sys

import pytest
from django.test import TestCase, override_settings

from django_inspector import trace
from django_inspector.storage.models import Event
from django_inspector.tracing.context import get_current_trace_id


@pytest.fixture(autouse=True)
def _clean_buffer():
    from django_inspector.storage.flush import clear_buffer
    clear_buffer()
    yield
    clear_buffer()


class TestReentrance(TestCase):
    def test_reentering_an_open_trace_object_raises(self):
        t = trace("double")
        with t:
            with pytest.raises(RuntimeError):
                with t:
                    pass

    def test_a_fresh_with_after_exit_works_again(self):
        t = trace("reusable")
        with t:
            pass
        with t:
            pass  # no RuntimeError: the object is closed between the two


class TestEnterReturnsTraceId(TestCase):
    def test_with_returns_the_trace_id(self):
        with trace("job") as trace_id:
            assert trace_id is not None
            assert trace_id == get_current_trace_id()

    async def test_async_with_returns_the_trace_id(self):
        async with trace("job") as trace_id:
            assert trace_id == get_current_trace_id()
        assert trace_id is not None


class TestOneKeptTraceWithAnchor(TestCase):
    def test_with_form(self):
        with trace("nightly-report") as trace_id:
            pass
        event = Event.objects.get(trace_id=trace_id, event_type="trace.completed")
        assert event.metadata["name"] == "nightly-report"
        assert event.metadata["status"] == "ok"
        assert event.metadata["error_type"] is None

    async def test_async_with_form(self):
        from asgiref.sync import sync_to_async

        async with trace("sync-prices") as trace_id:
            pass
        event = await sync_to_async(Event.objects.get)(trace_id=trace_id, event_type="trace.completed")
        assert event.metadata["name"] == "sync-prices"

    def test_sync_decorator(self):
        captured = {}

        @trace("send-digest")
        def send_digest():
            captured["trace_id"] = get_current_trace_id()

        send_digest()
        event = Event.objects.get(trace_id=captured["trace_id"], event_type="trace.completed")
        assert event.metadata["name"] == "send-digest"

    async def test_async_decorator(self):
        from asgiref.sync import sync_to_async

        captured = {}

        @trace("refresh-cache")
        async def refresh_cache():
            captured["trace_id"] = get_current_trace_id()

        await refresh_cache()
        event = await sync_to_async(Event.objects.get)(
            trace_id=captured["trace_id"], event_type="trace.completed"
        )
        assert event.metadata["name"] == "refresh-cache"


class TestSharedTrace(TestCase):
    def test_sql_log_and_exception_events_share_the_trace(self):
        from django.db import connection

        from django_inspector.watchers import registry

        # Other test modules install/remove the SQL hook on this same shared
        # connection through their own SQLWatcher instances; make sure it's
        # actually installed here regardless of what ran before this test.
        sql_watcher = registry.get_instance("sql")
        sql_watcher.disable()
        sql_watcher.enable()

        logger = logging.getLogger("django_inspector_test_shared")
        with trace("mixed") as trace_id:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            logger.warning("something happened")
            try:
                raise ValueError("boom")
            except ValueError:
                pass  # swallowed inside the block; not the traced exception

        types = set(Event.objects.filter(trace_id=trace_id).values_list("event_type", flat=True))
        assert "sql.query" in types
        assert "log.record" in types
        assert "trace.completed" in types


class TestNestedJoins(TestCase):
    def test_inner_joins_outer_with_one_anchor_and_one_flush(self):
        with trace("outer") as outer_id:
            with trace("inner") as inner_id:
                pass
            assert inner_id == outer_id

        anchors = Event.objects.filter(trace_id=outer_id, event_type="trace.completed")
        assert anchors.count() == 1
        assert anchors.first().metadata["name"] == "outer"


class TestExceptionHandling(TestCase):
    def test_exception_is_recorded_then_reraised_unchanged(self):
        raised = ValueError("kaboom")
        caught = None
        trace_id = None
        try:
            with trace("boom") as trace_id:
                raise raised
        except ValueError as exc:
            caught = exc

        assert caught is raised
        assert caught.__traceback__ is not None

        exc_event = Event.objects.get(trace_id=trace_id, event_type="exception.raised")
        assert exc_event.metadata["exception_message"] == "kaboom"

        anchor = Event.objects.get(trace_id=trace_id, event_type="trace.completed")
        assert anchor.metadata["status"] == "error"
        assert anchor.metadata["error_type"] == "builtins.ValueError"

    def test_ok_anchor_has_no_error_type(self):
        with trace("clean") as trace_id:
            pass
        anchor = Event.objects.get(trace_id=trace_id, event_type="trace.completed")
        assert anchor.metadata["status"] == "ok"
        assert anchor.metadata["error_type"] is None


class TestSampling(TestCase):
    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_rate_0_drops_an_ok_run(self):
        with trace("ok-run") as trace_id:
            pass
        assert not Event.objects.filter(trace_id=trace_id).exists()

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_rate_0_keeps_a_failing_run(self):
        trace_id = None
        try:
            with trace("fail-run") as trace_id:
                raise ValueError("x")
        except ValueError:
            pass
        assert Event.objects.filter(trace_id=trace_id, event_type="trace.completed").exists()

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0, "SLOW_REQUEST_THRESHOLD_MS": 0})
    def test_rate_0_keeps_a_slow_run(self):
        with trace("slow-run") as trace_id:
            pass
        assert Event.objects.filter(trace_id=trace_id, event_type="trace.completed").exists()


class TestDisabledInspector(TestCase):
    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_ENABLED": False})
    def test_disabled_inspector_does_nothing(self):
        with trace("noop") as trace_id:
            assert trace_id is None
            assert get_current_trace_id() is None
        assert not Event.objects.exists()


class TestFlushFailure(TestCase):
    def test_failing_flush_is_logged_and_does_not_break_the_block(self):
        import django_inspector.storage.flush as flush_mod
        original = flush_mod.flush_events

        def _raising():
            raise RuntimeError("flush failed")

        flush_mod.flush_events = _raising
        try:
            with self.assertLogs("django_inspector", level=logging.WARNING) as cm:
                with trace("x"):
                    pass
            assert any("flush_events failed" in msg for msg in cm.output), cm.output
        finally:
            flush_mod.flush_events = original

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": True})
    def test_raise_errors_propagates_the_flush_failure(self):
        import django_inspector.storage.flush as flush_mod
        original = flush_mod.flush_events

        def _raising():
            raise RuntimeError("flush failed")

        flush_mod.flush_events = _raising
        try:
            with pytest.raises(RuntimeError, match="flush failed"):
                with trace("x"):
                    pass
        finally:
            flush_mod.flush_events = original


class TestFailingStepDuringException(TestCase):
    def test_a_failing_anchor_step_still_reraises_the_users_exception(self):
        import django_inspector.tracing.api as api_mod
        original = api_mod._record_anchor

        def _raising(*args, **kwargs):
            raise RuntimeError("anchor broke")

        api_mod._record_anchor = _raising
        try:
            with pytest.raises(ValueError, match="user error"):
                with trace("x"):
                    raise ValueError("user error")
        finally:
            api_mod._record_anchor = original

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": True})
    def test_still_reraises_the_users_exception_even_with_raise_errors_on(self):
        import django_inspector.tracing.api as api_mod
        original = api_mod._record_anchor

        def _raising(*args, **kwargs):
            raise RuntimeError("anchor broke")

        api_mod._record_anchor = _raising
        try:
            with pytest.raises(ValueError, match="user error"):
                with trace("x"):
                    raise ValueError("user error")
        finally:
            api_mod._record_anchor = original


class TestContextReset(TestCase):
    def test_context_is_fully_reset_afterwards(self):
        from django_inspector.storage.flush import _get_buffer

        with trace("temp"):
            pass
        assert get_current_trace_id() is None
        assert len(_get_buffer()) == 0


class TestConcurrentAsyncWith(TestCase):
    async def test_concurrent_runs_stay_isolated(self):
        async def worker(name):
            async with trace(name) as trace_id:
                await asyncio.sleep(0)
                assert get_current_trace_id() == trace_id
            return trace_id

        ids = await asyncio.gather(worker("a"), worker("b"))
        assert ids[0] != ids[1]
        assert None not in ids


class TestConcurrentDecoratedCalls(TestCase):
    async def test_each_call_gets_its_own_trace(self):
        @trace("job")
        async def job():
            await asyncio.sleep(0)
            return get_current_trace_id()

        ids = await asyncio.gather(job(), job())
        assert ids[0] != ids[1]


def test_importing_django_inspector_does_not_import_storage_models():
    code = (
        "import sys\n"
        "import django_inspector\n"
        "assert 'django_inspector.storage.models' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
