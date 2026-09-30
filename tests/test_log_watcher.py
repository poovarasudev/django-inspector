"""Tests for the Logging Watcher (LOG-01..LOG-06)."""

import inspect
import logging

import pytest
from django.test import TestCase, override_settings

from django_inspector.conf import inspector_settings
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.log import MAX_MESSAGE_LENGTH, InspectorLogHandler, LogWatcher

app_logger = logging.getLogger("shop.checkout")


@pytest.fixture(autouse=True)
def _isolate_app_log_watcher():
    """The app's own LogWatcher is on by default; switch it off so each test sees only its own handler."""
    from django.apps import apps

    app = apps.get_app_config("django_inspector")
    active = [
        w for w in getattr(app, "_watcher_instances", [])
        if isinstance(w, LogWatcher) and w.is_enabled
    ]
    for watcher in active:
        watcher.disable()
    yield
    for watcher in active:
        watcher.enable()


def log_events():
    return [e for e in _get_buffer() if e["event_type"] == "log.record"]


def inspector_handlers():
    return [h for h in logging.getLogger().handlers if isinstance(h, InspectorLogHandler)]


class TestLogWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert LogWatcher.watcher_name == "log"

    def test_on_by_default(self):
        assert inspector_settings.watcher_enabled("log") is True

    def test_enable_adds_one_root_handler_and_disable_removes_it(self):
        watcher = LogWatcher()
        watcher.enable()
        watcher.enable()
        try:
            assert len(inspector_handlers()) == 1
        finally:
            watcher.disable()
        watcher.disable()
        assert inspector_handlers() == []

    def test_second_watcher_does_not_add_a_second_handler(self):
        first, second = LogWatcher(), LogWatcher()
        first.enable()
        second.enable()
        second.disable()
        try:
            assert len(inspector_handlers()) == 1
        finally:
            first.disable()
        assert inspector_handlers() == []

    def test_default_threshold_is_warning(self):
        watcher = LogWatcher()
        watcher.enable()
        try:
            assert inspector_handlers()[0].level == logging.WARNING
        finally:
            watcher.disable()

    def test_threshold_accepts_names_and_numbers(self):
        for value, expected in (("info", logging.INFO), ("ERROR", logging.ERROR), (15, 15)):
            watcher = LogWatcher()
            with override_settings(DJANGO_INSPECTOR={"LOG_LEVEL_THRESHOLD": value}):
                watcher.enable()
            try:
                assert inspector_handlers()[0].level == expected
            finally:
                watcher.disable()

    def test_invalid_threshold_falls_back_to_warning(self):
        watcher = LogWatcher()
        with override_settings(DJANGO_INSPECTOR={"LOG_LEVEL_THRESHOLD": "LOUD"}):
            with self.assertLogs("django_inspector", level="WARNING") as logs:
                watcher.enable()
        try:
            assert inspector_handlers()[0].level == logging.WARNING
            assert any("LOG_LEVEL_THRESHOLD" in line for line in logs.output)
        finally:
            watcher.disable()


class LogWatcherTestCase(TestCase):
    def setUp(self):
        self.watcher = LogWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()

    def use_level(self, log, level):
        previous = log.level
        log.setLevel(level)
        self.addCleanup(log.setLevel, previous)


class TestLogCapture(LogWatcherTestCase):
    def test_warning_is_captured_with_source_location(self):
        line = inspect.currentframe().f_lineno + 1
        app_logger.warning("order %s failed", 42)
        [event] = log_events()
        meta = event["metadata"]
        assert event["trace_id"] == self.trace_id
        assert meta["logger"] == "shop.checkout"
        assert meta["level"] == logging.WARNING
        assert meta["level_name"] == "WARNING"
        assert meta["message"] == "order 42 failed"
        assert meta["file"].endswith("test_log_watcher.py")
        assert meta["line"] == line
        assert meta["function"] == "test_warning_is_captured_with_source_location"
        assert "traceback" not in meta

    def test_info_below_default_threshold_is_dropped(self):
        self.use_level(app_logger, logging.INFO)
        app_logger.info("just saying")
        assert log_events() == []

    def test_info_is_captured_when_threshold_is_lowered(self):
        self.use_level(app_logger, logging.INFO)
        with override_settings(DJANGO_INSPECTOR={"LOG_LEVEL_THRESHOLD": "INFO"}):
            self.watcher.disable()
            self.watcher.enable()
        app_logger.info("just saying")
        [event] = log_events()
        assert event["metadata"]["level_name"] == "INFO"

    def test_exception_traceback_is_captured(self):
        try:
            raise KeyError("sku-42")
        except KeyError:
            app_logger.exception("lookup failed")
        meta = log_events()[0]["metadata"]
        assert meta["level_name"] == "ERROR"
        assert meta["exception_type"] == "KeyError"
        assert meta["traceback"].startswith("Traceback (most recent call last)")
        assert "KeyError: 'sku-42'" in meta["traceback"]

    def test_bad_format_arguments_fall_back_to_the_raw_message(self):
        # Fed straight to our handler: pytest's own capture handler re-raises
        # formatting errors before ours would see the record.
        record = logging.LogRecord(
            "shop.checkout", logging.WARNING, __file__, 1, "%s and %s", (1,), None
        )
        inspector_handlers()[0].handle(record)
        assert log_events()[0]["metadata"]["message"] == "%s and %s"

    def test_long_message_is_truncated(self):
        app_logger.warning("x" * (MAX_MESSAGE_LENGTH + 100))
        meta = log_events()[0]["metadata"]
        assert len(meta["message"]) == MAX_MESSAGE_LENGTH
        assert meta["message_truncated"] is True

    def test_inspector_loggers_are_excluded(self):
        logging.getLogger("django_inspector").warning("inspector itself")
        logging.getLogger("django_inspector.watchers.sql").error("inspector child")
        assert log_events() == []

    def test_lookalike_logger_name_is_captured(self):
        logging.getLogger("django_inspector_extras").warning("not ours")
        [event] = log_events()
        assert event["metadata"]["logger"] == "django_inspector_extras"

    def test_non_propagating_logger_is_not_captured(self):
        quiet = logging.getLogger("shop.quiet")
        quiet.propagate = False
        self.addCleanup(setattr, quiet, "propagate", True)
        quiet.warning("kept local")
        assert log_events() == []

    def test_no_events_without_active_trace(self):
        clear_trace_id(self.token)
        self.token = None
        app_logger.warning("outside a request")
        assert log_events() == []

    def test_card_number_in_message_is_masked(self):
        app_logger.warning("card 4111111111111111 declined")
        assert log_events()[0]["metadata"]["message"] == "***REDACTED***"
