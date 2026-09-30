import asyncio
import logging

from asgiref.sync import sync_to_async

from django_inspector.conf import inspector_settings
from django_inspector.sampling import (
    compute_sampling_decision,
    early_pick,
    reset_detail,
    start_detail,
)
from django_inspector.tracing.context import (
    clear_trace_id,
    generate_trace_id,
    set_trace_id,
)

try:
    from asgiref.sync import iscoroutinefunction, markcoroutinefunction
except ImportError:  # asgiref < 3.6 (Django 4.0/4.1)
    from asyncio import iscoroutinefunction

    def markcoroutinefunction(func):
        func._is_coroutine = asyncio.coroutines._is_coroutine
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

        trace_id = generate_trace_id()
        token = set_trace_id(trace_id)
        request.inspector_trace_id = trace_id
        pick = early_pick()
        state_tokens = self._start_request_state(pick)

        self._notify_request_start(request)

        try:
            response = self.get_response(request)
            self._notify_request_end(request, response)
            sampled = compute_sampling_decision(request, response, pick)
            request.inspector_sampled = sampled
            if not sampled:
                from django_inspector.storage.flush import clear_buffer
                clear_buffer()
        finally:
            try:
                self._flush()
            finally:
                self._reset_request_state(state_tokens)
                clear_trace_id(token)

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

        trace_id = generate_trace_id()
        token = set_trace_id(trace_id)
        request.inspector_trace_id = trace_id
        pick = early_pick()
        state_tokens = self._start_request_state(pick)

        self._notify_request_start(request)

        try:
            response = await self.get_response(request)
            await sync_to_async(self._notify_request_end)(request, response)
            sampled = compute_sampling_decision(request, response, pick)
            request.inspector_sampled = sampled
            if not sampled:
                from django_inspector.storage.flush import clear_buffer
                clear_buffer()
        finally:
            try:
                await sync_to_async(self._flush)()
            finally:
                self._reset_request_state(state_tokens)
                clear_trace_id(token)

        return response

    @staticmethod
    def _start_request_state(pick=None):
        """
        Fresh per-request event buffer and SQL query log. Without this, requests
        whose context inherits a buffer share one list, and one request's flush
        can clear another's events mid-flight. Also records whether detailed
        watchers capture (False when early sampling didn't pick the request).
        """
        from django_inspector.storage.flush import start_buffer
        from django_inspector.watchers.sql import start_query_log

        return start_buffer(), start_query_log(), start_detail(pick is not False)

    @staticmethod
    def _reset_request_state(tokens):
        from django_inspector.storage.flush import reset_buffer
        from django_inspector.watchers.sql import reset_query_log

        buffer_token, query_log_token, detail_token = tokens
        reset_detail(detail_token)
        reset_query_log(query_log_token)
        reset_buffer(buffer_token)

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

    def _flush(self):
        try:
            from django_inspector.storage.flush import flush_events
            flush_events()
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: flush_events failed", exc_info=True)
        try:
            from django_inspector.watchers.sql import clear_query_log
            clear_query_log()
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: clear_query_log failed", exc_info=True)
