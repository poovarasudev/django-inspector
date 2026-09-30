"""ASGI support: async-capable middleware and end-to-end watcher coverage (ASYNC-03)."""

import logging

from asgiref.sync import sync_to_async
from django.http import HttpResponse
from django.test import AsyncRequestFactory, TestCase, override_settings

from django_inspector.ignores import invalidate_ignore_caches
from django_inspector.middleware import InspectorMiddleware
from django_inspector.storage.models import Event

try:
    from asgiref.sync import iscoroutinefunction
except ImportError:  # asgiref < 3.6
    from asyncio import iscoroutinefunction


def trace_event_types(trace_id):
    return sorted(Event.objects.filter(trace_id=trace_id).values_list("event_type", flat=True))


class TestMiddlewareModes:
    def test_capability_flags(self):
        assert InspectorMiddleware.sync_capable is True
        assert InspectorMiddleware.async_capable is True

    def test_sync_get_response_gives_a_sync_middleware(self):
        middleware = InspectorMiddleware(lambda request: HttpResponse("ok"))
        assert not iscoroutinefunction(middleware)

    def test_async_get_response_gives_an_async_middleware(self):
        async def get_response(request):
            return HttpResponse("ok")

        assert iscoroutinefunction(InspectorMiddleware(get_response))


class TestAsyncMiddleware(TestCase):
    """Drive the middleware directly in async mode."""

    def setUp(self):
        invalidate_ignore_caches()
        self.addCleanup(invalidate_ignore_caches)

    async def run_async(self, path, view=None):
        async def default_view(request):
            logging.getLogger("shop.async").warning("from an async view")
            return HttpResponse("ok")

        request = AsyncRequestFactory().get(path)
        response = await InspectorMiddleware(view or default_view)(request)
        return request, response

    async def test_async_request_is_traced_and_flushed(self):
        request, response = await self.run_async("/orders/")
        assert response.status_code == 200
        types = await sync_to_async(trace_event_types)(request.inspector_trace_id)
        assert types == ["log.record", "request.completed"]

    async def test_view_exception_still_flushes_and_propagates(self):
        async def failing_view(request):
            raise RuntimeError("async boom")

        request = AsyncRequestFactory().get("/orders/")
        with self.assertRaises(RuntimeError):
            await InspectorMiddleware(failing_view)(request)
        assert hasattr(request, "inspector_trace_id")

    async def test_ignored_path_is_not_traced(self):
        with override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ["^/healthz"]}):
            invalidate_ignore_caches()
            request, response = await self.run_async("/healthz")
        assert response.status_code == 200
        assert not hasattr(request, "inspector_trace_id")
        assert await sync_to_async(Event.objects.count)() == 0

    async def test_dashboard_path_is_not_traced(self):
        request, _ = await self.run_async("/inspector/logs/")
        assert not hasattr(request, "inspector_trace_id")
        assert await sync_to_async(Event.objects.count)() == 0

    async def test_unsampled_request_is_discarded(self):
        with override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0}):
            request, _ = await self.run_async("/orders/")
        assert request.inspector_sampled is False
        assert await sync_to_async(Event.objects.count)() == 0
