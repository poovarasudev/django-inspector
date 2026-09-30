"""
Tests for the X-Inspector-Trace response header (TRACE-05).
"""

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from django_inspector.middleware import InspectorMiddleware

HEADER = "X-Inspector-Trace"


def _run(settings_override=None, status=200):
    factory = RequestFactory()
    request = factory.get("/test/")

    def stub_view(req):
        return HttpResponse("ok", status=status)

    with override_settings(DJANGO_INSPECTOR=settings_override or {}):
        middleware = InspectorMiddleware(stub_view)
        response = middleware(request)
    return request, response


class TestTraceHeaderDebugDefault(TestCase):
    @override_settings(DEBUG=True)
    def test_debug_on_adds_the_header(self):
        request, response = _run()
        assert response[HEADER] == request.inspector_trace_id

    @override_settings(DEBUG=False)
    def test_debug_off_omits_the_header(self):
        _, response = _run()
        assert HEADER not in response


class TestTraceHeaderExplicit(TestCase):
    @override_settings(DEBUG=False)
    def test_true_always_adds_the_header(self):
        request, response = _run(settings_override={"TRACE_HEADER": True})
        assert response[HEADER] == request.inspector_trace_id

    @override_settings(DEBUG=True)
    def test_false_never_adds_the_header(self):
        _, response = _run(settings_override={"TRACE_HEADER": False})
        assert HEADER not in response


class TestTraceHeaderOnlyOnKeptTraces(TestCase):
    @override_settings(DEBUG=True)
    def test_unkept_trace_never_gets_the_header(self):
        _, response = _run(settings_override={"TRACE_HEADER": True, "SAMPLING_RATE": 0.0})
        assert HEADER not in response

    @override_settings(DEBUG=True)
    def test_a_failing_5xx_response_is_kept_and_gets_the_header(self):
        request, response = _run(
            settings_override={"TRACE_HEADER": True, "SAMPLING_RATE": 0.0}, status=500
        )
        assert response[HEADER] == request.inspector_trace_id


@pytest.mark.django_db
class TestTraceHeaderAsync(TestCase):
    async def test_async_path_adds_the_header_with_debug_on(self):
        from django.test import AsyncRequestFactory

        async def get_response(request):
            return HttpResponse("ok")

        with override_settings(DEBUG=True):
            middleware = InspectorMiddleware(get_response)
            request = AsyncRequestFactory().get("/async/")
            response = await middleware(request)
        assert response[HEADER] == request.inspector_trace_id

    async def test_async_path_omits_the_header_with_debug_off(self):
        from django.test import AsyncRequestFactory

        async def get_response(request):
            return HttpResponse("ok")

        with override_settings(DEBUG=False):
            middleware = InspectorMiddleware(get_response)
            request = AsyncRequestFactory().get("/async/")
            response = await middleware(request)
        assert HEADER not in response
