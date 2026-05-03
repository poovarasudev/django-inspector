"""Tests for the Request Watcher (REQ-01..REQ-06)."""

import pytest
from django.test import RequestFactory, TestCase, override_settings
from django.http import HttpResponse

from django_inspector.watchers.request import (
    RequestWatcher,
    _extract_headers,
    _get_client_ip,
    _get_user_info,
)
from django_inspector.tracing.context import set_trace_id, clear_trace_id, generate_trace_id
from django_inspector.storage.flush import _get_buffer, clear_buffer


@pytest.fixture(autouse=True)
def _clear_buffer():
    """Clear the event buffer before and after each test."""
    clear_buffer()
    yield
    clear_buffer()


class TestRequestWatcher(TestCase):
    def setUp(self):
        self.watcher = RequestWatcher()
        self.watcher.enable()
        self.factory = RequestFactory()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        clear_trace_id(self.token)
        clear_buffer()

    def test_watcher_name(self):
        assert self.watcher.watcher_name == "request"

    def test_enable_disable(self):
        assert self.watcher.is_enabled
        self.watcher.disable()
        assert not self.watcher.is_enabled

    def test_records_basic_request_metadata(self):
        """REQ-01: Captures method, path, full URL, query parameters."""
        request = self.factory.get("/api/test/?page=1&limit=10")
        response = HttpResponse("ok", status=200)
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        assert len(buffer) == 1
        event = buffer[0]
        assert event["event_type"] == "request.completed"
        meta = event["metadata"]
        assert meta["method"] == "GET"
        assert meta["path"] == "/api/test/"
        assert meta["query_params"]["page"] == ["1"]
        assert meta["query_params"]["limit"] == ["10"]

    def test_records_request_headers(self):
        """REQ-02: Captures request and response headers."""
        request = self.factory.get("/api/test/", HTTP_ACCEPT="application/json")
        response = HttpResponse("ok", content_type="text/plain")
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        meta = buffer[0]["metadata"]
        assert "Accept" in meta["request_headers"]
        assert meta["request_headers"]["Accept"] == "application/json"
        assert "Content-Type" in meta["response_headers"]

    def test_records_request_body(self):
        """REQ-03: Captures request body with truncation."""
        request = self.factory.post("/api/test/", data="hello body", content_type="text/plain")
        response = HttpResponse("response body", status=200)
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        meta = buffer[0]["metadata"]
        assert "hello body" in meta["request_body"]
        assert "response body" in meta["response_body"]

    def test_records_status_and_latency(self):
        """REQ-04: Captures status code and response latency."""
        request = self.factory.get("/api/test/")
        response = HttpResponse("ok", status=201)
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        meta = buffer[0]["metadata"]
        assert meta["status_code"] == 201
        assert meta["latency_ms"] is not None
        assert meta["latency_ms"] >= 0

    def test_records_user_and_ip(self):
        """REQ-05: Captures user, session, IP."""
        request = self.factory.get("/api/test/", REMOTE_ADDR="192.168.1.100")
        response = HttpResponse("ok")
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        meta = buffer[0]["metadata"]
        assert meta["client_ip"] == "192.168.1.100"
        # No authenticated user on this request
        assert meta["user"] is None

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_ENABLED": True, "DASHBOARD_URL_PREFIX": "inspector/"})
    def test_excludes_inspector_requests(self):
        """REQ-06: Inspector's own requests are excluded."""
        request = self.factory.get("/inspector/feed/")
        response = HttpResponse("ok")
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        assert len(buffer) == 0

    def test_x_forwarded_for_ip(self):
        """REQ-05: Checks X-Forwarded-For for client IP."""
        request = self.factory.get(
            "/api/test/",
            HTTP_X_FORWARDED_FOR="10.0.0.1, 192.168.1.1",
            REMOTE_ADDR="127.0.0.1",
        )
        response = HttpResponse("ok")
        self.watcher.on_request(request)
        self.watcher.on_response(request, response)

        buffer = _get_buffer()
        meta = buffer[0]["metadata"]
        assert meta["client_ip"] == "10.0.0.1"


class TestExtractHeaders:
    def test_extracts_http_headers(self):
        meta = {"HTTP_ACCEPT": "text/html", "HTTP_HOST": "example.com", "SERVER_NAME": "localhost"}
        headers = _extract_headers(meta)
        assert headers["Accept"] == "text/html"
        assert headers["Host"] == "example.com"
        assert "Server-Name" not in headers

    def test_includes_content_type(self):
        meta = {"CONTENT_TYPE": "application/json", "CONTENT_LENGTH": "42"}
        headers = _extract_headers(meta)
        assert headers["Content-Type"] == "application/json"
        assert headers["Content-Length"] == "42"


class TestGetClientIp:
    def test_xff_takes_priority(self):
        class FakeRequest:
            META = {"HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8", "REMOTE_ADDR": "127.0.0.1"}
        assert _get_client_ip(FakeRequest()) == "1.2.3.4"

    def test_falls_back_to_remote_addr(self):
        class FakeRequest:
            META = {"REMOTE_ADDR": "192.168.0.1"}
        assert _get_client_ip(FakeRequest()) == "192.168.0.1"


class TestGetUserInfo:
    def test_none_user(self):
        class FakeRequest:
            user = None
        assert _get_user_info(FakeRequest()) is None

    def test_anonymous_user(self):
        class FakeUser:
            is_authenticated = False
        class FakeRequest:
            user = FakeUser()
        assert _get_user_info(FakeRequest()) is None

    def test_authenticated_user(self):
        class FakeUser:
            is_authenticated = True
            pk = 42
            username = "testuser"
        class FakeRequest:
            user = FakeUser()
        assert _get_user_info(FakeRequest()) == "42"
