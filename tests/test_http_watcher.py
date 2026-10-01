"""Tests for the HTTP Client Watcher (HTTP-01..03)."""

import sys
from unittest import mock

import httpx
import pytest
import requests
from django.test import TestCase, override_settings
from requests.adapters import BaseAdapter
from requests.models import Response as RequestsResponse

from django_inspector import trace
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers import registry
from django_inspector.watchers.http import HTTPClientWatcher

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


def http_events():
    return [e for e in _get_buffer() if e["event_type"] == "http.request"]


class FakeAdapter(BaseAdapter):
    """A requests transport adapter that never touches the network."""

    def __init__(self, response=None, exc=None):
        super().__init__()
        self.response = response
        self.exc = exc
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response

    def close(self):
        pass


def requests_response(status_code=200, content=b"", headers=None, history=None,
                       url="https://api.example.com/x"):
    response = RequestsResponse()
    response.status_code = status_code
    response._content = content
    response.headers.update(headers or {})
    response.history = history or []
    response.url = url
    return response


def session_with_fake_adapter(response=None, exc=None):
    session = requests.Session()
    adapter = FakeAdapter(response=response, exc=exc)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session, adapter


class TestLifecycle(TestCase):
    """The process-wide http watcher is already enabled (WATCHERS["http"] defaults
    True); these tests work on their own instances, keeping that one disabled
    meanwhile."""

    def setUp(self):
        self.global_watcher = registry.get_instance("http")
        self.global_watcher.disable()

    def tearDown(self):
        self.global_watcher.enable()

    def test_watcher_name(self):
        assert HTTPClientWatcher.watcher_name == "http"

    def test_on_by_default(self):
        from django_inspector.conf import inspector_settings

        assert inspector_settings.watcher_enabled("http") is True

    def test_install_patches_requests_session_send_and_remove_restores_it(self):
        original = requests.Session.send
        watcher = HTTPClientWatcher()
        watcher.enable()
        try:
            assert requests.Session.send is not original
        finally:
            watcher.disable()
        assert requests.Session.send is original

    def test_install_patches_httpx_sync_and_async_send_and_remove_restores_them(self):
        original_sync = httpx.Client.send
        original_async = httpx.AsyncClient.send
        watcher = HTTPClientWatcher()
        watcher.enable()
        try:
            assert httpx.Client.send is not original_sync
            assert httpx.AsyncClient.send is not original_async
        finally:
            watcher.disable()
        assert httpx.Client.send is original_sync
        assert httpx.AsyncClient.send is original_async

    def test_a_second_instance_never_double_patches_requests(self):
        original = requests.Session.send
        watcher1 = HTTPClientWatcher()
        watcher1.enable()
        try:
            patched = requests.Session.send
            watcher2 = HTTPClientWatcher()
            watcher2.enable()
            try:
                assert requests.Session.send is patched
            finally:
                watcher2.disable()
            assert requests.Session.send is patched
        finally:
            watcher1.disable()
        assert requests.Session.send is original

    def test_a_second_instance_never_double_patches_httpx(self):
        original = httpx.Client.send
        watcher1 = HTTPClientWatcher()
        watcher1.enable()
        try:
            patched = httpx.Client.send
            watcher2 = HTTPClientWatcher()
            watcher2.enable()
            try:
                assert httpx.Client.send is patched
            finally:
                watcher2.disable()
        finally:
            watcher1.disable()
        assert httpx.Client.send is original

    def test_requests_missing_still_enables_httpx(self):
        original_httpx_send = httpx.Client.send
        with mock.patch.dict(sys.modules, {"requests": None}):
            watcher = HTTPClientWatcher()
            watcher.enable()
            try:
                assert httpx.Client.send is not original_httpx_send
            finally:
                watcher.disable()
        assert httpx.Client.send is original_httpx_send

    def test_httpx_missing_still_enables_requests(self):
        original_requests_send = requests.Session.send
        with mock.patch.dict(sys.modules, {"httpx": None}):
            watcher = HTTPClientWatcher()
            watcher.enable()
            try:
                assert requests.Session.send is not original_requests_send
            finally:
                watcher.disable()
        assert requests.Session.send is original_requests_send

    def test_neither_installed_is_a_noop(self):
        original_requests_send = requests.Session.send
        original_httpx_send = httpx.Client.send
        with mock.patch.dict(sys.modules, {"requests": None, "httpx": None}):
            watcher = HTTPClientWatcher()
            watcher.enable()  # must not raise
            watcher.disable()
        assert requests.Session.send is original_requests_send
        assert httpx.Client.send is original_httpx_send


class HTTPWatcherTestCase(TestCase):
    """An active trace with an empty buffer. The process-wide http watcher
    (WATCHERS["http"] defaults True) does the actual capturing."""

    def setUp(self):
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()


class TestRequestsCapture(HTTPWatcherTestCase):
    def test_get_records_one_event_with_expected_fields(self):
        response = requests_response(status_code=200, content=b"hello", url="https://api.example.com/x")
        session, _ = session_with_fake_adapter(response=response)
        session.get("https://api.example.com/x")

        [event] = http_events()
        meta = event["metadata"]
        assert event["event_type"] == "http.request"
        assert event["trace_id"] == self.trace_id
        assert meta["library"] == "requests"
        assert meta["method"] == "GET"
        assert meta["host"] == "api.example.com"
        assert meta["status_code"] == 200
        assert meta["duration_ms"] >= 0
        assert meta["redirects"] == 0
        assert meta["streamed"] is False
        assert meta["response_bytes"] == len(b"hello")
        assert "error" not in meta
        assert meta["origin_file"] is not None
        assert meta["origin_function"] is not None

    def test_post_is_captured(self):
        response = requests_response(status_code=201)
        session, _ = session_with_fake_adapter(response=response)
        session.post("https://api.example.com/orders", data={"a": "b"})
        [event] = http_events()
        assert event["metadata"]["method"] == "POST"
        assert event["metadata"]["status_code"] == 201

    def test_module_level_requests_get_is_captured(self):
        response = requests_response(status_code=204)
        with mock.patch("requests.adapters.HTTPAdapter.send", return_value=response):
            requests.get("https://api.example.com/ping")
        [event] = http_events()
        assert event["metadata"]["method"] == "GET"
        assert event["metadata"]["status_code"] == 204

    def test_redirects_are_counted(self):
        history = [requests_response(status_code=301), requests_response(status_code=302)]
        response = requests_response(status_code=200, history=history)
        session, _ = session_with_fake_adapter(response=response)
        session.get("https://api.example.com/x")
        assert http_events()[0]["metadata"]["redirects"] == 2

    def test_a_followed_redirect_is_one_event(self):
        # requests follows redirects by calling Session.send again for each
        # hop; only the outer call is the user's call (spec: one event, with
        # the hops counted in `redirects`).
        class RedirectingAdapter(BaseAdapter):
            def send(self, request, **kwargs):
                if request.url.endswith("/start"):
                    response = requests_response(
                        status_code=302, headers={"Location": "https://api.example.com/end"}, url=request.url
                    )
                else:
                    response = requests_response(status_code=200, content=b"ok", url=request.url)
                response.request = request
                return response

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", RedirectingAdapter())
        session.get("https://api.example.com/start")

        [event] = http_events()
        assert event["metadata"]["url"] == "https://api.example.com/start"
        assert event["metadata"]["redirects"] == 1
        assert event["metadata"]["status_code"] == 200

    def test_error_is_recorded_and_reraised_unchanged(self):
        exc = requests.exceptions.ConnectionError("connection refused")
        session, _ = session_with_fake_adapter(exc=exc)
        with pytest.raises(requests.exceptions.ConnectionError, match="connection refused"):
            session.get("https://api.example.com/x")
        meta = http_events()[0]["metadata"]
        assert meta["status_code"] is None
        assert meta["error"] == "ConnectionError: connection refused"

    def test_streaming_flag_and_size_from_content_length_only(self):
        response = requests_response(status_code=200, headers={"Content-Length": "4096"})
        session, _ = session_with_fake_adapter(response=response)
        session.get("https://api.example.com/big", stream=True)
        meta = http_events()[0]["metadata"]
        assert meta["streamed"] is True
        assert meta["response_bytes"] == 4096

    def test_response_bytes_falls_back_to_body_length_when_not_streamed(self):
        response = requests_response(status_code=200, content=b"hello world")
        session, _ = session_with_fake_adapter(response=response)
        session.get("https://api.example.com/x")
        assert http_events()[0]["metadata"]["response_bytes"] == len(b"hello world")

    def test_streamed_without_content_length_has_no_response_bytes(self):
        response = requests_response(status_code=200)
        session, _ = session_with_fake_adapter(response=response)
        session.get("https://api.example.com/x", stream=True)
        assert http_events()[0]["metadata"]["response_bytes"] is None


class TestURLMasking(HTTPWatcherTestCase):
    def test_sensitive_query_param_is_masked(self):
        session, _ = session_with_fake_adapter(response=requests_response())
        session.get("https://api.example.com/x?api_key=abc123")
        assert "abc123" not in http_events()[0]["metadata"]["url"]

    def test_userinfo_is_removed(self):
        session, _ = session_with_fake_adapter(response=requests_response())
        session.get("https://user:hunter2@api.example.com/x")
        assert "hunter2" not in http_events()[0]["metadata"]["url"]
        assert "user" not in http_events()[0]["metadata"]["url"]

    def test_jwt_in_path_is_masked(self):
        session, _ = session_with_fake_adapter(response=requests_response())
        session.get("https://api.example.com/auth/%s/" % JWT)
        assert JWT not in http_events()[0]["metadata"]["url"]

    def test_the_stored_path_column_is_also_masked(self):
        """End-to-end: a real Event row's derived `path` column is masked too."""
        from django_inspector.storage.models import Event

        clear_trace_id(self.token)
        self.token = None
        clear_buffer()
        with trace("outbound-call") as outer_id:
            session, _ = session_with_fake_adapter(response=requests_response())
            session.get("https://api.example.com/x?api_key=abc123")
        event = Event.objects.get(trace_id=outer_id, event_type="http.request")
        assert "abc123" not in (event.path or "")


class TestCorrelation(HTTPWatcherTestCase):
    def test_correlated_with_the_current_trace_id(self):
        session, _ = session_with_fake_adapter(response=requests_response())
        session.get("https://api.example.com/x")
        assert http_events()[0]["trace_id"] == self.trace_id

    def test_no_active_trace_records_nothing(self):
        clear_trace_id(self.token)
        self.token = None
        session, _ = session_with_fake_adapter(response=requests_response())
        session.get("https://api.example.com/x")
        assert http_events() == []

    def test_correlated_inside_trace(self):
        clear_trace_id(self.token)
        self.token = None
        clear_buffer()
        with trace("nightly-sync") as outer_id:
            session, _ = session_with_fake_adapter(response=requests_response())
            session.get("https://api.example.com/x")
        from django_inspector.storage.models import Event

        assert Event.objects.filter(trace_id=outer_id, event_type="http.request").exists()

    def test_correlated_inside_a_request(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from django_inspector.middleware import InspectorMiddleware
        from django_inspector.storage.models import Event

        clear_trace_id(self.token)
        self.token = None
        clear_buffer()

        def view(request):
            session, _ = session_with_fake_adapter(response=requests_response())
            session.get("https://api.example.com/x")
            return HttpResponse("ok")

        InspectorMiddleware(view)(RequestFactory().get("/x/"))
        request_event = Event.objects.get(event_type="request.completed")
        http_event = Event.objects.get(event_type="http.request")
        assert http_event.trace_id == request_event.trace_id


class TestDisabledWatcher(TestCase):
    def test_disabled_watcher_records_nothing(self):
        # The process-wide instance (WATCHERS["http"] defaults True) is
        # disabled for the duration of this test to exercise a genuinely
        # unpatched Session.send.
        global_watcher = registry.get_instance("http")
        global_watcher.disable()
        trace_id = generate_trace_id()
        token = set_trace_id(trace_id)
        clear_buffer()
        try:
            session, _ = session_with_fake_adapter(response=requests_response())
            session.get("https://api.example.com/x")
            assert http_events() == []
        finally:
            clear_trace_id(token)
            clear_buffer()
            global_watcher.enable()


class TestDisabledInspector(HTTPWatcherTestCase):
    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_ENABLED": False})
    def test_calls_still_work_with_inspector_disabled(self):
        session, _ = session_with_fake_adapter(response=requests_response(status_code=204))
        response = session.get("https://api.example.com/x")
        assert response.status_code == 204
        assert http_events() == []


class TestRecordingFailureDoesNotBreakTheCall(HTTPWatcherTestCase):
    def test_a_failing_metadata_build_does_not_break_the_call(self):
        import logging

        import django_inspector.watchers.http as http_watcher_module

        with mock.patch.object(http_watcher_module, "_response_metadata", side_effect=RuntimeError("boom")):
            session, _ = session_with_fake_adapter(response=requests_response(status_code=204))
            with self.assertLogs("django_inspector", level=logging.WARNING):
                response = session.get("https://api.example.com/x")
        assert response.status_code == 204
        assert http_events() == []

    def test_a_failing_metadata_build_on_the_error_path_still_reraises_the_real_exception(self):
        import logging

        import django_inspector.watchers.http as http_watcher_module

        exc = requests.exceptions.ConnectionError("down")
        session, _ = session_with_fake_adapter(exc=exc)
        with mock.patch.object(http_watcher_module, "_error_metadata", side_effect=RuntimeError("boom")):
            with self.assertLogs("django_inspector", level=logging.WARNING):
                with pytest.raises(requests.exceptions.ConnectionError, match="down"):
                    session.get("https://api.example.com/x")
        assert http_events() == []


def _httpx_handler(status_code=200, content=b"", headers=None):
    def handler(request):
        return httpx.Response(status_code, content=content, headers=headers or {}, request=request)

    return handler


def _httpx_redirect_handler(target="https://api.example.com/final"):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(302, headers={"Location": target}, request=request)
        return httpx.Response(200, content=b"ok", request=request)

    return handler


class TestHttpxSyncCapture(HTTPWatcherTestCase):
    def test_get_records_one_event_with_expected_fields(self):
        client = httpx.Client(transport=httpx.MockTransport(_httpx_handler(200, b"hello")))
        client.get("https://api.example.com/x")
        [event] = http_events()
        meta = event["metadata"]
        assert meta["library"] == "httpx"
        assert meta["method"] == "GET"
        assert meta["status_code"] == 200
        assert meta["host"] == "api.example.com"
        assert meta["response_bytes"] == len(b"hello")

    def test_redirects_are_counted(self):
        client = httpx.Client(transport=httpx.MockTransport(_httpx_redirect_handler()), follow_redirects=True)
        client.get("https://api.example.com/start")
        assert http_events()[0]["metadata"]["redirects"] == 1

    def test_error_is_recorded_and_reraised_unchanged(self):
        def handler(request):
            raise httpx.ConnectError("boom", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.ConnectError, match="boom"):
            client.get("https://api.example.com/x")
        meta = http_events()[0]["metadata"]
        assert meta["status_code"] is None
        assert meta["error"] == "ConnectError: boom"

    def test_streaming_flag_and_size_from_content_length_only(self):
        client = httpx.Client(transport=httpx.MockTransport(_httpx_handler(200, headers={"Content-Length": "2048"})))
        request = client.build_request("GET", "https://api.example.com/big")
        client.send(request, stream=True)
        meta = http_events()[0]["metadata"]
        assert meta["streamed"] is True
        assert meta["response_bytes"] == 2048


class TestHttpxAsyncCapture(HTTPWatcherTestCase):
    def test_async_get_records_one_event(self):
        import asyncio

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_httpx_handler(200, b"hi"))) as client:
                return await client.get("https://api.example.com/x")

        response = asyncio.run(run())
        assert response.status_code == 200
        [event] = http_events()
        meta = event["metadata"]
        assert meta["library"] == "httpx"
        assert meta["method"] == "GET"
        assert meta["status_code"] == 200
        assert meta["response_bytes"] == len(b"hi")

    def test_async_error_is_recorded_and_reraised_unchanged(self):
        import asyncio

        async def failing_handler(request):
            raise httpx.ConnectError("async boom", request=request)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)) as client:
                await client.get("https://api.example.com/x")

        with pytest.raises(httpx.ConnectError, match="async boom"):
            asyncio.run(run())
        meta = http_events()[0]["metadata"]
        assert meta["error"] == "ConnectError: async boom"
