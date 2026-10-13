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
class TestTraceScope:
    def test_start_generates_a_trace_id_and_sets_context(self):
        from django_inspector.tracing.scope import TraceScope

        scope = TraceScope.start()
        try:
            assert get_current_trace_id() == scope.trace_id
            assert len(scope.trace_id) == 32
        finally:
            scope.close()

    def test_start_accepts_an_explicit_trace_id(self):
        from django_inspector.tracing.scope import TraceScope

        scope = TraceScope.start("explicit-id")
        try:
            assert scope.trace_id == "explicit-id"
        finally:
            scope.close()

    def test_elapsed_ms_grows(self):
        from django_inspector.tracing.scope import TraceScope

        scope = TraceScope.start()
        try:
            first = scope.elapsed_ms()
            second = scope.elapsed_ms()
            assert second >= first >= 0
        finally:
            scope.close()

    def test_flush_true_writes_buffered_events(self):
        from django_inspector.storage.flush import buffer_event
        from django_inspector.storage.models import Event
        from django_inspector.tracing.scope import TraceScope

        scope = TraceScope.start()
        try:
            buffer_event(scope.trace_id, "sql.query", {"sql": "SELECT 1"})
            scope.flush(keep=True)
            assert Event.objects.filter(trace_id=scope.trace_id).count() == 1
        finally:
            scope.close()

    def test_flush_false_writes_nothing(self):
        from django_inspector.storage.flush import buffer_event
        from django_inspector.storage.models import Event
        from django_inspector.tracing.scope import TraceScope

        scope = TraceScope.start()
        try:
            buffer_event(scope.trace_id, "sql.query", {"sql": "SELECT 1"})
            scope.flush(keep=False)
            assert Event.objects.filter(trace_id=scope.trace_id).count() == 0
        finally:
            scope.close()

    def test_close_resets_context_fully(self):
        from django_inspector.storage.flush import _get_buffer
        from django_inspector.tracing.scope import TraceScope

        scope = TraceScope.start()
        scope.close()
        assert get_current_trace_id() is None
        assert len(_get_buffer()) == 0

    def test_close_after_a_sync_to_async_flush_restores_the_context(self):
        import asyncio

        from asgiref.sync import sync_to_async

        from django_inspector.tracing.scope import TraceScope

        async def run():
            scope = TraceScope.start()
            trace_id = scope.trace_id
            await sync_to_async(scope.flush)(keep=True)
            scope.close()
            return trace_id

        trace_id = asyncio.run(run())
        assert get_current_trace_id() is None
        assert trace_id is not None


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
