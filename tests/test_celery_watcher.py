"""Tests for the Celery Watcher (CELERY-01..05)."""

import contextlib
import logging
import sys
import time
from unittest import mock

import pytest

celery = pytest.importorskip("celery")

from django.test import TestCase, TransactionTestCase, override_settings  # noqa: E402

from django_inspector import trace  # noqa: E402
from django_inspector.storage.models import Event  # noqa: E402
from django_inspector.tracing.context import clear_trace_id, get_current_trace_id  # noqa: E402
from django_inspector.watchers import celery as celery_watcher  # noqa: E402
from django_inspector.watchers import registry  # noqa: E402
from django_inspector.watchers.celery import (  # noqa: E402
    MAX_OPEN_TASK_RUNS,
    CeleryWatcher,
    _on_before_task_publish,
    _on_task_failure,
    _on_task_postrun,
    _on_task_prerun,
    _on_task_retry,
)


class FakeRequest:
    """Stands in for celery.app.task.Context (task.request)."""

    def __init__(self, **kw):
        self.inspector_trace_id = None
        self.headers = None
        self.retries = 0
        self.hostname = "worker1"
        self.delivery_info = {"routing_key": "celery"}
        self.eta = None
        self.id = None
        self.__dict__.update(kw)


class FakeTask:
    def __init__(self, name, request=None):
        self.name = name
        self.request = request or FakeRequest()


@pytest.fixture(autouse=True)
def _clean_state():
    from django_inspector.storage.flush import clear_buffer

    clear_buffer()
    celery_watcher._runs.clear()
    yield
    clear_buffer()
    celery_watcher._runs.clear()


class TestInstallRemoveHooks(TestCase):
    """The process-wide celery watcher is already enabled (WATCHERS["celery"] defaults
    True); these tests work on their own instances, keeping that one disabled meanwhile."""

    def setUp(self):
        self.global_watcher = registry.get_instance("celery")
        self.global_watcher.disable()

    def tearDown(self):
        self.global_watcher.enable()

    def test_enable_connects_signals_and_disable_disconnects(self):
        from celery import signals

        assert signals.task_prerun.receivers == []
        watcher = CeleryWatcher()
        watcher.enable()
        try:
            assert len(signals.task_prerun.receivers) == 1
            assert len(signals.before_task_publish.receivers) == 1
            assert len(signals.task_failure.receivers) == 1
            assert len(signals.task_retry.receivers) == 1
            assert len(signals.task_postrun.receivers) == 1
        finally:
            watcher.disable()
        assert signals.task_prerun.receivers == []
        assert signals.before_task_publish.receivers == []

    def test_no_celery_installed_is_a_noop(self):
        with mock.patch.dict(sys.modules, {"celery": None}):
            watcher = CeleryWatcher()
            watcher.enable()  # must not raise
            watcher.disable()  # must not raise


class TestTaskSent(TestCase):
    def test_header_injection_and_recording_only_happen_inside_a_trace(self):
        headers = {"id": "task-x", "eta": None, "retries": 0}
        _on_before_task_publish(
            sender="app.tasks.add", headers=headers, body=None,
            routing_key="celery", exchange="", properties={},
        )
        assert "inspector_trace_id" not in headers
        assert not Event.objects.filter(event_type="task.sent").exists()

    def test_task_sent_fields_inside_a_trace(self):
        headers = {"id": "task-y", "eta": "2026-10-01T00:00:00", "retries": 2}
        with trace("sender-job") as sender_trace_id:
            _on_before_task_publish(
                sender="app.tasks.add", headers=headers, body=None,
                routing_key="celery", exchange="", properties={},
            )
            assert headers["inspector_trace_id"] == sender_trace_id

        sent = Event.objects.get(event_type="task.sent")
        assert sent.trace_id == sender_trace_id
        assert sent.metadata["task"] == "app.tasks.add"
        assert sent.metadata["task_id"] == "task-y"
        assert sent.metadata["queue"] == "celery"
        assert sent.metadata["eta"] == "2026-10-01T00:00:00"
        assert sent.metadata["retries"] == 2

    def test_sensitive_header_values_are_masked(self):
        headers = {"id": "task-z", "eta": None, "retries": 0}
        with trace("sender-job"):
            _on_before_task_publish(
                sender="app.tasks.send_email", headers=headers, body=None,
                routing_key="emails", exchange="", properties={"password": "hunter2"},
            )
        sent = Event.objects.get(event_type="task.sent")
        assert sent.metadata["task"] == "app.tasks.send_email"


class TestWorkerLifecycle(TestCase):
    def test_prerun_postrun_pair_produces_a_kept_task_trace_with_its_anchor_and_extras(self):
        request = FakeRequest(inspector_trace_id="parent-trace-id", retries=1, hostname="w1",
                              delivery_info={"routing_key": "celery"}, eta=None)
        task = FakeTask("app.tasks.add", request)

        _on_task_prerun(task_id="t-success", task=task, args=(1, 2), kwargs={"x": "y"})
        _on_task_postrun(task_id="t-success", task=task, args=(1, 2), kwargs={"x": "y"}, retval=3, state="SUCCESS")

        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.parent_trace_id == "parent-trace-id"
        assert anchor.metadata["status"] == "ok"
        assert anchor.metadata["task"] == "app.tasks.add"
        assert anchor.metadata["task_id"] == "t-success"
        assert anchor.metadata["state"] == "SUCCESS"
        assert anchor.metadata["retries"] == 1
        assert anchor.metadata["worker"] == "w1"
        assert anchor.metadata["queue"] == "celery"
        assert anchor.metadata["args"] == [1, 2]
        assert anchor.metadata["kwargs"] == {"x": "y"}
        assert anchor.metadata["result"] == "3"
        assert "exception" not in anchor.metadata

    def test_falls_back_to_headers_dict_for_the_parent_trace_id(self):
        request = FakeRequest(inspector_trace_id=None, headers={"inspector_trace_id": "header-parent"})
        task = FakeTask("app.tasks.add", request)

        _on_task_prerun(task_id="t-headers", task=task, args=(), kwargs={})
        _on_task_postrun(task_id="t-headers", task=task, args=(), kwargs={}, retval=None, state="SUCCESS")

        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.parent_trace_id == "header-parent"

    def test_no_parent_header_means_no_parent_trace_id(self):
        task = FakeTask("app.tasks.add", FakeRequest())
        _on_task_prerun(task_id="t-no-parent", task=task, args=(), kwargs={})
        _on_task_postrun(task_id="t-no-parent", task=task, args=(), kwargs={}, retval=None, state="SUCCESS")
        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.parent_trace_id is None

    def test_args_and_kwargs_are_bounded_and_masked(self):
        task = FakeTask("app.tasks.add", FakeRequest())
        _on_task_prerun(task_id="t-mask", task=task, args=(), kwargs={"password": "hunter2"})
        _on_task_postrun(task_id="t-mask", task=task, args=(), kwargs={"password": "hunter2"},
                         retval=None, state="SUCCESS")
        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.metadata["kwargs"]["password"] == "***REDACTED***"


class TestFailure(TestCase):
    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_failure_sets_failure_state_records_exception_and_is_always_kept(self):
        task = FakeTask("app.tasks.fail", FakeRequest())
        _on_task_prerun(task_id="t-fail", task=task, args=(), kwargs={})
        try:
            raise ValueError("boom")
        except ValueError as exc:
            _on_task_failure(task_id="t-fail", exception=exc, traceback=exc.__traceback__)
        _on_task_postrun(task_id="t-fail", task=task, args=(), kwargs={}, retval=None, state="FAILURE")

        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.metadata["status"] == "error"
        assert anchor.metadata["state"] == "FAILURE"
        assert "boom" in anchor.metadata["exception"]
        assert "result" not in anchor.metadata

        exc_event = Event.objects.get(trace_id=anchor.trace_id, event_type="exception.raised")
        assert exc_event.metadata["exception_message"] == "boom"


class TestRetry(TestCase):
    def test_retry_is_not_a_failure_and_reports_retry_state(self):
        request = FakeRequest(id="t-retry")
        task = FakeTask("app.tasks.flaky", request)
        _on_task_prerun(task_id="t-retry", task=task, args=(), kwargs={})
        _on_task_retry(request=request, reason=Exception("try again"))
        _on_task_postrun(task_id="t-retry", task=task, args=(), kwargs={}, retval=None, state="RETRY")

        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.metadata["status"] == "ok"
        assert anchor.metadata["state"] == "RETRY"
        assert not Event.objects.filter(event_type="exception.raised").exists()


class TestEagerMode(TestCase):
    def test_prerun_inside_an_active_trace_joins_it_without_an_early_flush(self):
        task = FakeTask("app.tasks.add", FakeRequest())
        with trace("sender-job") as outer_id:
            _on_task_prerun(task_id="t-eager", task=task, args=(), kwargs={})
            _on_task_postrun(task_id="t-eager", task=task, args=(), kwargs={}, retval=1, state="SUCCESS")
            # Still inside the block: nothing has been flushed to the DB yet.
            assert not Event.objects.filter(trace_id=outer_id).exists()

        events = Event.objects.filter(trace_id=outer_id)
        types = set(events.values_list("event_type", flat=True))
        assert "trace.completed" in types
        assert "task.completed" in types
        assert events.filter(event_type="task.completed").count() == 1


class TestOpenRunCap(TestCase):
    def test_the_cap_evicts_the_oldest_open_run(self):
        with mock.patch.object(celery_watcher, "MAX_OPEN_TASK_RUNS", 2):
            for i in range(3):
                task = FakeTask("app.tasks.add", FakeRequest())
                ctx = self.assertLogs("django_inspector", level="WARNING") if i == 2 else contextlib.nullcontext()
                with ctx:
                    _on_task_prerun(task_id="run-%d" % i, task=task, args=(), kwargs={})
                clear_trace_id()  # simulate each run starting in its own, independent worker context

        assert "run-0" not in celery_watcher._runs
        assert "run-1" in celery_watcher._runs
        assert "run-2" in celery_watcher._runs

    def test_the_default_cap_is_1000(self):
        assert MAX_OPEN_TASK_RUNS == 1000


class TestHandlerFailuresNeverRaise(TestCase):
    def test_a_failing_prerun_handler_logs_and_does_not_raise(self):
        with mock.patch.object(celery_watcher, "_start_task_run", side_effect=RuntimeError("boom")):
            with self.assertLogs("django_inspector", level="WARNING"):
                _on_task_prerun(task_id="x", task=FakeTask("app.x", FakeRequest()), args=(), kwargs={})
        assert "x" not in celery_watcher._runs

    def test_a_failing_before_publish_handler_logs_and_does_not_raise(self):
        with trace("sender-job"):
            with mock.patch.object(celery_watcher, "_record_task_sent", side_effect=RuntimeError("boom")):
                with self.assertLogs("django_inspector", level="WARNING"):
                    _on_before_task_publish(sender="app.x", headers={"id": "x"}, body=None,
                                            routing_key="celery", exchange="", properties={})

    def test_raise_errors_setting_propagates_the_failure(self):
        with override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": True}):
            with mock.patch.object(celery_watcher, "_start_task_run", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError):
                    _on_task_prerun(task_id="y", task=FakeTask("app.y", FakeRequest()), args=(), kwargs={})


# --- Integration: a real Celery app, in-memory broker, real worker thread ---

INTEGRATION_LOGGER = logging.getLogger("tests.celery_integration")

integration_app = celery.Celery(
    "django_inspector_test_app", broker="memory://", backend="cache+memory://",
)
integration_app.conf.update(
    task_always_eager=False,
    broker_connection_retry_on_startup=True,
    task_default_queue="celery",
    # The worker's log setup otherwise strips every handler off the root
    # logger (including the log watcher's), replacing them with its own.
    worker_hijack_root_logger=False,
)


@integration_app.task(name="tests.celery_integration.query_and_log")
def _query_and_log_task():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    INTEGRATION_LOGGER.warning("task log line")
    return "done"


@contextlib.contextmanager
def _shared_connections():
    """
    Shares this thread's DB connections with the Celery worker thread, the
    same way LiveServerTestCase shares them with its server thread: without
    this, a worker thread opening its own connection to the ":memory:"
    sqlite test database would see a separate, empty database.
    """
    from django.db import connections

    overrides = {}
    for conn in connections.all():
        if conn.vendor == "sqlite" and conn.is_in_memory_db():
            conn.inc_thread_sharing()
            overrides[conn.alias] = conn

    def _on_worker_ready(sender=None, **kw):
        for alias, conn in overrides.items():
            connections[alias] = conn

    celery.signals.worker_ready.connect(_on_worker_ready, weak=False)
    try:
        yield
    finally:
        celery.signals.worker_ready.disconnect(_on_worker_ready)
        for conn in overrides.values():
            conn.dec_thread_sharing()
        # The worker thread's queries may have left this connection idle in a
        # state the test runner's teardown (DROP DATABASE on Postgres) won't
        # tolerate; closing it here is safe since Django reopens on demand.
        connections.close_all()


class TestCeleryIntegration(TransactionTestCase):
    """CELERY-01..05 end to end: a real task through a real (in-memory broker)
    worker, asserted on the stored Event rows — not on faked internals."""

    def test_a_task_sent_from_a_view_gets_its_own_linked_trace_with_its_events(self):
        from celery.contrib.testing.worker import start_worker
        from django.http import HttpResponse
        from django.test import RequestFactory

        from django_inspector.middleware import InspectorMiddleware

        def view(request):
            _query_and_log_task.delay()
            return HttpResponse("ok")

        with _shared_connections():
            with start_worker(integration_app, perform_ping_check=False, pool="solo", loglevel="warning"):
                InspectorMiddleware(view)(RequestFactory().get("/send-task/"))

                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not Event.objects.filter(event_type="task.completed").exists():
                    time.sleep(0.05)

        request_event = Event.objects.get(event_type="request.completed")
        sent_event = Event.objects.get(event_type="task.sent", trace_id=request_event.trace_id)
        assert sent_event.metadata["task"] == "tests.celery_integration.query_and_log"

        anchor = Event.objects.get(event_type="task.completed")
        assert anchor.trace_id != request_event.trace_id
        assert anchor.parent_trace_id == request_event.trace_id
        assert anchor.metadata["status"] == "ok"
        assert anchor.metadata["result"] == "'done'"

        task_events = Event.objects.filter(trace_id=anchor.trace_id)
        types = set(task_events.values_list("event_type", flat=True))
        assert "sql.query" in types
        assert "log.record" in types

        assert get_current_trace_id() is None  # the test thread's own context is untouched
