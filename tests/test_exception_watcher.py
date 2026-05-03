"""Tests for the Exception Watcher (EXC-01..EXC-04)."""

import pytest
from django.test import TestCase

from django_inspector.watchers.exception import (
    ExceptionWatcher,
    _extract_frames,
    _safe_locals,
    _extract_chain,
)
from django_inspector.tracing.context import set_trace_id, clear_trace_id, generate_trace_id
from django_inspector.storage.flush import _get_buffer, clear_buffer


@pytest.fixture(autouse=True)
def _clear_buffer():
    clear_buffer()
    yield
    clear_buffer()


class TestExceptionWatcher(TestCase):
    def setUp(self):
        from django.core.signals import got_request_exception
        # Disconnect any handlers registered by AppConfig to avoid double-counting
        got_request_exception.receivers.clear()
        self.watcher = ExceptionWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        clear_trace_id(self.token)
        clear_buffer()

    def test_watcher_name(self):
        assert self.watcher.watcher_name == "exception"

    def test_enable_disable(self):
        assert self.watcher.is_enabled
        self.watcher.disable()
        assert not self.watcher.is_enabled

    def test_captures_exception_via_signal(self):
        """EXC-01: Captures exception type, message, stack trace."""
        from django.core.signals import got_request_exception

        try:
            raise ValueError("test error")
        except ValueError:
            got_request_exception.send(sender=self.__class__, request=None)

        buffer = _get_buffer()
        exc_events = [e for e in buffer if e["event_type"] == "exception.raised"]
        assert len(exc_events) == 1

        meta = exc_events[0]["metadata"]
        assert "ValueError" in meta["exception_type"]
        assert meta["exception_message"] == "test error"
        assert len(meta["stack_trace"]) > 0

    def test_captures_frames_with_locals(self):
        """EXC-02: Captures local variables at each stack frame."""
        from django.core.signals import got_request_exception

        local_var = "captured_value"
        try:
            raise RuntimeError("frame test")
        except RuntimeError:
            got_request_exception.send(sender=self.__class__, request=None)

        buffer = _get_buffer()
        exc_events = [e for e in buffer if e["event_type"] == "exception.raised"]
        meta = exc_events[0]["metadata"]

        assert len(meta["frames"]) > 0
        # At least one frame should have locals
        all_locals = {}
        for frame in meta["frames"]:
            all_locals.update(frame.get("locals", {}))
        assert "local_var" in all_locals
        assert "captured_value" in all_locals["local_var"]

    def test_captures_chained_exceptions(self):
        """EXC-03: Captures __cause__ and __context__."""
        from django.core.signals import got_request_exception

        try:
            try:
                raise KeyError("original")
            except KeyError:
                raise ValueError("chained") from KeyError("original")
        except ValueError:
            got_request_exception.send(sender=self.__class__, request=None)

        buffer = _get_buffer()
        exc_events = [e for e in buffer if e["event_type"] == "exception.raised"]
        meta = exc_events[0]["metadata"]

        assert len(meta["chained_exceptions"]) >= 1
        chained = meta["chained_exceptions"][0]
        assert "KeyError" in chained["type"]
        assert chained["chain_type"] == "cause"

    def test_links_to_trace_id(self):
        """EXC-04: Exception event is linked to trace_id."""
        from django.core.signals import got_request_exception

        try:
            raise TypeError("trace test")
        except TypeError:
            got_request_exception.send(sender=self.__class__, request=None)

        buffer = _get_buffer()
        exc_events = [e for e in buffer if e["event_type"] == "exception.raised"]
        assert exc_events[0]["trace_id"] == self.trace_id

    def test_captures_request_context(self):
        """Exception includes request method and path when available."""
        from django.core.signals import got_request_exception
        from django.test import RequestFactory

        request = RequestFactory().get("/api/fail/")

        try:
            raise Exception("request context test")
        except Exception:
            got_request_exception.send(sender=self.__class__, request=request)

        buffer = _get_buffer()
        exc_events = [e for e in buffer if e["event_type"] == "exception.raised"]
        meta = exc_events[0]["metadata"]
        assert meta["request_method"] == "GET"
        assert meta["request_path"] == "/api/fail/"


class TestSafeLocals:
    def test_captures_simple_types(self):
        result = _safe_locals({"x": 42, "name": "test"})
        assert "x" in result
        assert "42" in result["x"]

    def test_skips_dunder_vars(self):
        result = _safe_locals({"__builtins__": {}, "x": 1})
        assert "__builtins__" not in result
        assert "x" in result

    def test_truncates_long_values(self):
        result = _safe_locals({"big": "a" * 500})
        assert len(result["big"]) <= 210  # 200 + repr quotes + "..."


class TestExtractChain:
    def test_empty_chain(self):
        try:
            raise ValueError("no chain")
        except ValueError as e:
            chain = _extract_chain(e)
        assert chain == []

    def test_explicit_cause(self):
        try:
            try:
                raise KeyError("root")
            except KeyError as ke:
                raise ValueError("wrapper") from ke
        except ValueError as e:
            chain = _extract_chain(e)
        assert len(chain) == 1
        assert chain[0]["chain_type"] == "cause"
        assert "KeyError" in chain[0]["type"]

    def test_implicit_context(self):
        try:
            try:
                raise KeyError("root")
            except KeyError:
                raise ValueError("wrapper")
        except ValueError as e:
            chain = _extract_chain(e)
        assert len(chain) == 1
        assert chain[0]["chain_type"] == "context"
