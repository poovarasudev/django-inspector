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
    3. Flush buffered watcher events to the database at end-of-request.
    4. Clean up the context variable after the response is returned.

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

        try:
            response = self.get_response(request)
        finally:
            self._flush()
            clear_trace_id(token)

        return response

    def _flush(self):
        try:
            from django_inspector.storage.flush import flush_events
            flush_events()
        except Exception:
            pass
