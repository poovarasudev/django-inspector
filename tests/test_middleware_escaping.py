"""
An exception escaping get_response (e.g. DEBUG_PROPAGATE_EXCEPTIONS) must
still flush the trace's events, as the middleware did before TraceScope
(TRACE-02: no behaviour change).
"""

import logging

import pytest
from asgiref.sync import async_to_sync
from django.test import RequestFactory

from django_inspector.middleware import InspectorMiddleware
from django_inspector.storage.models import Event
from django_inspector.tracing.context import get_current_trace_id

app_logger = logging.getLogger("tests.escaping")


def _failing_view(request):
    app_logger.warning("before the crash")
    raise ValueError("escaped")


async def _failing_async_view(request):
    app_logger.warning("before the crash")
    raise ValueError("escaped")


@pytest.mark.django_db
def test_events_are_flushed_when_an_exception_escapes():
    request = RequestFactory().get("/boom/")
    with pytest.raises(ValueError):
        InspectorMiddleware(_failing_view)(request)

    events = Event.objects.filter(trace_id=request.inspector_trace_id)
    assert events.filter(event_type="log.record").exists()
    assert get_current_trace_id() is None


@pytest.mark.django_db(transaction=True)
def test_events_are_flushed_when_an_exception_escapes_async():
    request = RequestFactory().get("/boom/")
    middleware = InspectorMiddleware(_failing_async_view)
    with pytest.raises(ValueError):
        async_to_sync(middleware)(request)

    events = Event.objects.filter(trace_id=request.inspector_trace_id)
    assert events.filter(event_type="log.record").exists()
    assert get_current_trace_id() is None
