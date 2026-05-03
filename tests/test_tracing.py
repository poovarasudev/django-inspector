import pytest
from django.test import RequestFactory

from django_inspector.tracing.context import (
    clear_trace_id,
    generate_trace_id,
    get_current_trace_id,
    set_trace_id,
)


def test_generate_trace_id_returns_32_char_hex():
    trace_id = generate_trace_id()
    assert len(trace_id) == 32
    assert all(c in "0123456789abcdef" for c in trace_id)


def test_generate_trace_id_is_unique():
    assert generate_trace_id() != generate_trace_id()


def test_set_and_get_trace_id():
    token = set_trace_id("abc123")
    assert get_current_trace_id() == "abc123"
    clear_trace_id(token)
    assert get_current_trace_id() is None


def test_clear_trace_id_with_token():
    set_trace_id("first")
    token = set_trace_id("second")
    clear_trace_id(token)
    assert get_current_trace_id() == "first"
    clear_trace_id()


def test_clear_trace_id_without_token():
    set_trace_id("something")
    clear_trace_id()
    assert get_current_trace_id() is None


@pytest.mark.django_db
def test_middleware_sets_trace_id_on_request():
    from django_inspector.middleware import InspectorMiddleware

    factory = RequestFactory()
    request = factory.get("/")
    captured = {}

    def get_response(req):
        captured["trace_id"] = get_current_trace_id()
        captured["request_attr"] = getattr(req, "inspector_trace_id", None)
        from django.http import HttpResponse
        return HttpResponse("ok")

    middleware = InspectorMiddleware(get_response)
    middleware(request)

    assert captured["trace_id"] is not None
    assert len(captured["trace_id"]) == 32
    assert captured["request_attr"] == captured["trace_id"]


@pytest.mark.django_db
def test_middleware_clears_trace_id_after_response():
    from django_inspector.middleware import InspectorMiddleware

    factory = RequestFactory()
    request = factory.get("/")

    def get_response(req):
        from django.http import HttpResponse
        return HttpResponse("ok")

    middleware = InspectorMiddleware(get_response)
    middleware(request)

    assert get_current_trace_id() is None


@pytest.mark.django_db
def test_middleware_skips_when_disabled(settings):
    settings.DJANGO_INSPECTOR = {"INSPECTOR_ENABLED": False}

    from django_inspector.middleware import InspectorMiddleware

    factory = RequestFactory()
    request = factory.get("/")
    called = []

    def get_response(req):
        called.append(True)
        from django.http import HttpResponse
        return HttpResponse("ok")

    middleware = InspectorMiddleware(get_response)
    middleware(request)

    assert get_current_trace_id() is None
    assert called  # get_response still called, just no trace set
