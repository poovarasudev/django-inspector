from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import (
    clear_trace_id,
    generate_trace_id,
    set_trace_id,
)


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

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not inspector_settings.is_enabled:
            return self.get_response(request)

        trace_id = generate_trace_id()
        token = set_trace_id(trace_id)
        request.inspector_trace_id = trace_id

        self._notify_request_start(request)

        try:
            response = self.get_response(request)
            self._notify_request_end(request, response)
        finally:
            self._flush()
            clear_trace_id(token)

        return response

    def _notify_request_start(self, request):
        """Notify the request watcher at start of request."""
        try:
            from django_inspector.watchers.request import RequestWatcher
            from django_inspector.watchers import registry

            watcher_cls = registry.get("request")
            if watcher_cls is not None:
                # Find the active instance from the app config
                from django.apps import apps
                app = apps.get_app_config("django_inspector")
                for inst in getattr(app, "_watcher_instances", []):
                    if isinstance(inst, RequestWatcher) and inst.is_enabled:
                        inst.on_request(request)
                        break
        except Exception:
            pass

    def _notify_request_end(self, request, response):
        """Notify the request watcher at end of request (before flush)."""
        try:
            from django_inspector.watchers.request import RequestWatcher
            from django.apps import apps
            app = apps.get_app_config("django_inspector")
            for inst in getattr(app, "_watcher_instances", []):
                if isinstance(inst, RequestWatcher) and inst.is_enabled:
                    inst.on_response(request, response)
                    break
        except Exception:
            pass

    def _flush(self):
        try:
            from django_inspector.storage.flush import flush_events
            flush_events()
        except Exception:
            pass
        try:
            from django_inspector.watchers.sql import clear_query_log
            clear_query_log()
        except Exception:
            pass
