"""Tests for the Request Watcher (REQ-01..REQ-06)."""

import json

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.request import (
    RequestWatcher,
    _extract_headers,
    _get_user_info,
)


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

    @override_settings(DJANGO_INSPECTOR={"TRUSTED_PROXY_COUNT": 1})
    def test_x_forwarded_for_ip_behind_a_trusted_proxy(self):
        """REQ-05: X-Forwarded-For is used only for TRUSTED_PROXY_COUNT hops."""
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
        assert meta["client_ip"] == "192.168.1.1"

    def test_x_forwarded_for_is_ignored_by_default(self):
        request = self.factory.get(
            "/api/test/", HTTP_X_FORWARDED_FOR="10.0.0.1", REMOTE_ADDR="203.0.113.9"
        )
        self.watcher.on_request(request)
        self.watcher.on_response(request, HttpResponse("ok"))
        assert _get_buffer()[0]["metadata"]["client_ip"] == "203.0.113.9"


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


class TestBodyAndUrlMasking(TestCase):
    """S1/S2: bodies and the full URL are masked like every other field (MASK-01, REQ-03)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.watcher = RequestWatcher()
        self.watcher.enable()
        self.token = set_trace_id(generate_trace_id())

    def tearDown(self):
        clear_trace_id(self.token)
        self.watcher.disable()

    def _record(self, request, response=None):
        self.watcher.on_request(request)
        self.watcher.on_response(request, response if response is not None else HttpResponse("ok"))
        return _get_buffer()[-1]["metadata"]

    def test_form_body_password_is_masked(self):
        request = self.factory.post(
            "/login/", "username=u&password=hunter2",
            content_type="application/x-www-form-urlencoded",
        )
        meta = self._record(request)
        assert "hunter2" not in meta["request_body"]
        assert json.loads(meta["request_body"]) == {"username": "u", "password": "***REDACTED***"}
        assert meta["request_body_format"] == "form"

    def test_json_body_password_is_masked(self):
        request = self.factory.post(
            "/login/", json.dumps({"user": {"password": "hunter2"}, "n": 1}),
            content_type="application/json",
        )
        meta = self._record(request)
        assert json.loads(meta["request_body"]) == {"user": {"password": "***REDACTED***"}, "n": 1}
        assert meta["request_body_format"] == "json"

    def test_vendor_json_content_type_is_parsed(self):
        request = self.factory.post(
            "/x/", json.dumps({"token": "t"}), content_type="application/vnd.api+json"
        )
        assert "***REDACTED***" in self._record(request)["request_body"]

    def test_unparseable_json_is_not_stored(self):
        request = self.factory.post(
            "/login/", '{"password": "hunter2"', content_type="application/json"
        )
        meta = self._record(request)
        assert "hunter2" not in meta["request_body"]
        assert meta["request_body_format"] == "unparseable"

    def test_unparsed_multipart_body_is_not_stored(self):
        request = self.factory.post("/login/", {"username": "u", "password": "hunter2"})
        meta = self._record(request)
        assert "hunter2" not in meta["request_body"]
        assert meta["request_body_format"] == "multipart"

    def test_parsed_multipart_fields_are_masked_and_files_summarised(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("a.txt", b"file-bytes")
        request = self.factory.post("/up/", {"password": "hunter2", "note": "hi", "doc": upload})
        request.POST  # the view parsed the body
        meta = self._record(request)
        body = json.loads(meta["request_body"])
        assert body["fields"] == {"password": "***REDACTED***", "note": "hi"}
        assert body["files"] == {"doc": ["a.txt (10 bytes)"]}
        assert "file-bytes" not in meta["request_body"]

    def test_sensitive_post_parameters_are_masked(self):
        request = self.factory.post(
            "/pay/", "pin=1234&amount=5", content_type="application/x-www-form-urlencoded"
        )
        request.sensitive_post_parameters = ["pin"]
        body = json.loads(self._record(request)["request_body"])
        assert body == {"pin": "***REDACTED***", "amount": "5"}

    def test_sensitive_post_parameters_all_omits_the_body(self):
        request = self.factory.post(
            "/pay/", "pin=1234", content_type="application/x-www-form-urlencoded"
        )
        request.sensitive_post_parameters = "__ALL__"
        meta = self._record(request)
        assert "1234" not in meta["request_body"]
        assert meta["request_body_format"] == "omitted"

    def test_binary_request_body_is_summarised(self):
        request = self.factory.post("/img/", b"\x89PNG\r\n\x1a\n\x00\xff", content_type="image/png")
        meta = self._record(request)
        assert meta["request_body"] == "[binary body: 10 bytes, image/png]"
        assert meta["request_body_format"] == "binary"

    def test_binary_response_body_is_summarised(self):
        response = HttpResponse(b"%PDF-1.7\x00\xff", content_type="application/pdf")
        meta = self._record(self.factory.get("/doc/"), response)
        assert meta["response_body"] == "[binary body: 10 bytes, application/pdf]"

    def test_json_response_token_is_masked(self):
        response = HttpResponse(
            json.dumps({"access_token": "secret-value"}), content_type="application/json"
        )
        meta = self._record(self.factory.post("/token/"), response)
        assert "secret-value" not in meta["response_body"]

    def test_streaming_response_is_not_consumed(self):
        from django.http import StreamingHttpResponse

        response = StreamingHttpResponse(iter([b"a", b"b"]))
        meta = self._record(self.factory.get("/s/"), response)
        assert meta["response_body_format"] == "streaming"
        assert b"".join(response.streaming_content) == b"ab"

    @override_settings(DJANGO_INSPECTOR={"MAX_BODY_SIZE": 20})
    def test_structured_bodies_are_truncated_after_masking(self):
        request = self.factory.post(
            "/x/", json.dumps({"password": "p", "notes": "x" * 100}),
            content_type="application/json",
        )
        meta = self._record(request)
        assert len(meta["request_body"].encode()) <= 20
        assert meta["request_body_truncated"] is True
        assert meta["request_body"].startswith('{"password": "***')

    def test_full_url_query_string_is_masked(self):
        meta = self._record(self.factory.get("/cb/?token=abc123&page=2"))
        assert "abc123" not in meta["full_url"]
        assert meta["full_url"] == "http://testserver/cb/?token=***REDACTED***&page=2"

    def test_disallowed_host_still_records_the_request(self):
        meta = self._record(self.factory.get("/x/?a=1", HTTP_HOST="evil.example"))
        assert meta["full_url"] == "/x/?a=1"
        assert meta["path"] == "/x/"
