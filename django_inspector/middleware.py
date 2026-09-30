import asyncio
import logging

from asgiref.sync import sync_to_async

from django_inspector.conf import inspector_settings
from django_inspector.sampling import compute_sampling_decision
from django_inspector.tracing.scope import TraceScope

try:
    from asgiref.sync import iscoroutinefunction, markcoroutinefunction
except ImportError:  # asgiref < 3.6 (Django 4.0/4.1)
    from asyncio import iscoroutinefunction  # type: ignore[assignment]

    def markcoroutinefunction(func):
        func._is_coroutine = asyncio.coroutines._is_coroutine  # type: ignore[attr-defined]
        return func

logger = logging.getLogger("django_inspector")


class InspectorMiddleware:
    """
    Core middleware for django-inspector.

    Responsibilities:
    1. Generate a unique trace_id (UUID4 hex) for every incoming request.
    2. Store that trace_id in contextvars (async-safe) for the duration
       of the request so any code — watchers, views, signal handlers —
       can call get_current_trace_id() to retrieve it.
    3. Notify watchers (request watcher on_request/on_response).
    4. Flush buffered watcher events to the database at end-of-request.
    5. Clean up the context variable after the response is returned.

    Built on TraceScope, the same trace lifecycle the public trace() API
    uses (TRACE-02): start() begins the trace, flush() writes or discards
    its events depending on the sampling decision, and close() resets the
    context, all in the same order this middleware always used.

    Add to MIDDLEWARE *before* any other middleware that needs trace context:
        MIDDLEWARE = [
            "django_inspector.middleware.InspectorMiddleware",
            ...
        ]
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        # Under ASGI, Django passes an async get_response; serve it natively
        # instead of making Django adapt this middleware with a thread hop.
        self.async_mode = iscoroutinefunction(get_response)
        if self.async_mode:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.async_mode:
            return self.__acall__(request)

        if not inspector_settings.is_enabled:
            return self.get_response(request)

        from django_inspector.ignores import is_dashboard_path, should_ignore_path
        if should_ignore_path(request.path) or is_dashboard_path(request.path):
            return self.get_response(request)

        scope = TraceScope.start()
        request.inspector_trace_id = scope.trace_id

        self._notify_request_start(request)

        try:
            response = self.get_response(request)
            self._notify_request_end(request, response)
            sampled = compute_sampling_decision(request, response, scope.pick)
            request.inspector_sampled = sampled
            scope.flush(sampled)
            self._set_trace_header(response, sampled, scope.trace_id)
        finally:
            scope.close()

        return response

    async def __acall__(self, request):
        """
        Async twin of __call__. The request watcher's on_response (which may
        evaluate the lazy request.user) and the DB flush run through
        sync_to_async; everything else matches the sync path.
        """
        if not inspector_settings.is_enabled:
            return await self.get_response(request)

        from django_inspector.ignores import is_dashboard_path, should_ignore_path
        if should_ignore_path(request.path) or is_dashboard_path(request.path):
            return await self.get_response(request)

        scope = TraceScope.start()
        request.inspector_trace_id = scope.trace_id

        self._notify_request_start(request)

        try:
            response = await self.get_response(request)
            await sync_to_async(self._notify_request_end)(request, response)
            sampled = compute_sampling_decision(request, response, scope.pick)
            request.inspector_sampled = sampled
            await sync_to_async(scope.flush)(sampled)
            self._set_trace_header(response, sampled, scope.trace_id)
        finally:
            scope.close()

        return response

    @staticmethod
    def _request_watcher():
        from django_inspector.watchers import registry

        watcher = registry.get_instance("request")
        if watcher is not None and watcher.is_enabled:
            return watcher
        return None

    def _notify_request_start(self, request):
        """Notify the request watcher at start of request."""
        try:
            watcher = self._request_watcher()
            if watcher is not None:
                watcher.on_request(request)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error in _notify_request_start", exc_info=True)

    def _notify_request_end(self, request, response):
        """Notify the request watcher at end of request (before flush)."""
        try:
            watcher = self._request_watcher()
            if watcher is not None:
                watcher.on_response(request, response)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error in _notify_request_end", exc_info=True)

    def _set_trace_header(self, response, sampled, trace_id):
        """
        TRACE-05: X-Inspector-Trace on a kept trace's response, when
        TRACE_HEADER allows it. An unkept trace never gets the header: it
        would point at a trace that doesn't exist.
        """
        try:
            if sampled and self._trace_header_enabled():
                response["X-Inspector-Trace"] = trace_id
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error setting X-Inspector-Trace", exc_info=True)

    @staticmethod
    def _trace_header_enabled():
        from django.conf import settings

        value = inspector_settings.TRACE_HEADER
        if value == "debug":
            return bool(settings.DEBUG)
        return bool(value)
