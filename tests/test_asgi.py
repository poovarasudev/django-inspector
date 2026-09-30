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


def events_by_trace():
    traces = {}
    for event in Event.objects.order_by("pk"):
        traces.setdefault(event.trace_id, []).append(event)
    return traces


def assert_trace_is_self_contained(events):
    """Every event in the trace belongs to the one order its request handled."""
    [request] = [e for e in events if e.event_type == "request.completed"]
    order_id = int(request.metadata["path"].rstrip("/").rsplit("/", 1)[1])

    counts = {}
    for e in events:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
    assert counts == {
        "request.completed": 1,
        "cache.set": 1,
        "cache.get": 2,
        "template.rendered": 3,
        "signal.dispatched": 1,
        "log.record": 1,
    }

    cache_keys = sorted(e.metadata["key"] for e in events if e.event_type.startswith("cache."))
    assert cache_keys == sorted(["order:%d" % order_id] * 2 + ["audit:%d" % order_id])

    renders = sorted(
        (e.metadata["render_id"], e.metadata["name"], e.metadata["parent_id"])
        for e in events if e.event_type == "template.rendered"
    )
    assert renders == [
        (1, "inspector_tests/checkout.html", None),
        (2, "inspector_tests/layout.html", 1),
        (3, "inspector_tests/line.html", 2),
    ]

    [signal] = [e for e in events if e.event_type == "signal.dispatched"]
    assert [r["receiver"] for r in signal.metadata["receivers"]] == ["tests.asgi_urls.audit"]

    [log] = [e for e in events if e.event_type == "log.record"]
    assert log.metadata["message"] == "order %d checked out" % order_id


@override_settings(
    ROOT_URLCONF="tests.asgi_urls",
    DJANGO_INSPECTOR={"SIGNAL_WATCH_LIST": ["tests.asgi_urls.order_checked_out"]},
)
class TestEndToEnd(TestCase):
    """Every v1.1 watcher under ASGI (AsyncClient → ASGIHandler) and WSGI (Client)."""

    def setUp(self):
        from django.core.cache import cache

        from tests.asgi_urls import audit, order_checked_out
        from django_inspector.watchers.cache import CacheWatcher
        from django_inspector.watchers.signal import SignalWatcher
        from django_inspector.watchers.template import TemplateWatcher

        cache.clear()
        for watcher in (CacheWatcher(), TemplateWatcher(), SignalWatcher()):
            watcher.enable()
            self.addCleanup(watcher.disable)
        order_checked_out.connect(audit)
        self.addCleanup(order_checked_out.disconnect, audit)

    async def test_concurrent_asgi_requests_keep_their_own_events(self):
        import asyncio

        responses = await asyncio.gather(
            *(self.async_client.get("/checkout/%d/" % i) for i in range(1, 6))
        )
        assert [r.status_code for r in responses] == [200] * 5
        traces = await sync_to_async(events_by_trace)()
        assert len(traces) == 5
        for events in traces.values():
            assert_trace_is_self_contained(events)

    def test_wsgi_request_records_the_same_events(self):
        response = self.client.get("/sync/checkout/7/")
        assert response.status_code == 200
        [events] = events_by_trace().values()
        assert_trace_is_self_contained(events)
