import pytest

from django_inspector.storage.flush import _get_buffer, buffer_event, clear_buffer, flush_events
from django_inspector.storage.models import Event


@pytest.fixture(autouse=True)
def clean_buffer():
    clear_buffer()
    yield
    clear_buffer()


def test_buffer_event_adds_to_buffer():
    buffer_event("abc123", "sql.query", {"sql": "SELECT 1"})
    buf = _get_buffer()
    assert len(buf) == 1
    assert buf[0]["trace_id"] == "abc123"
    assert buf[0]["event_type"] == "sql.query"


def test_flush_events_empty_buffer_returns_zero():
    count = flush_events()
    assert count == 0


@pytest.mark.django_db
def test_flush_events_writes_to_db():
    buffer_event("aaa", "sql.query", {"sql": "SELECT 1"})
    buffer_event("aaa", "sql.query", {"sql": "SELECT 2"})
    count = flush_events()
    assert count == 2
    assert Event.objects.filter(trace_id="aaa").count() == 2


@pytest.mark.django_db
def test_flush_events_clears_buffer():
    buffer_event("bbb", "request.started", {})
    flush_events()
    assert len(_get_buffer()) == 0


@pytest.mark.django_db
def test_event_model_str():
    e = Event(trace_id="abcdef012345" + "0" * 20, event_type="sql.query", metadata={})
    assert "sql.query" in str(e)
    assert "abcdef01" in str(e)


@pytest.mark.django_db
def test_event_metadata_is_json():
    buffer_event("ccc", "exception.raised", {"type": "ValueError", "msg": "bad"})
    flush_events()
    event = Event.objects.get(trace_id="ccc")
    assert event.metadata["type"] == "ValueError"
    assert event.metadata["msg"] == "bad"


@pytest.mark.django_db
def test_events_keep_the_time_they_were_captured():
    import datetime

    from django.utils import timezone

    first = timezone.now() - datetime.timedelta(seconds=3)
    buffer_event("ddd", "sql.query", {"n": 1}, timestamp=first)
    buffer_event("ddd", "sql.query", {"n": 2})
    flush_events()
    events = list(Event.objects.filter(trace_id="ddd").order_by("timestamp"))
    assert events[0].timestamp == first
    assert (events[1].timestamp - events[0].timestamp).total_seconds() >= 2.9


def test_buffer_event_stamps_capture_time():
    from django.utils import timezone

    before = timezone.now()
    buffer_event("eee", "sql.query", {})
    assert before <= _get_buffer()[0]["timestamp"] <= timezone.now()


class TestMaxEventsPerTrace:
    def test_events_beyond_the_limit_are_dropped_and_counted(self, settings):
        from django_inspector.storage.flush import dropped_count

        settings.DJANGO_INSPECTOR = {"MAX_EVENTS_PER_TRACE": 3}
        for i in range(5):
            buffer_event("fff", "sql.query", {"n": i})
        assert len(_get_buffer()) == 3
        assert dropped_count() == 2

    def test_the_request_event_is_never_dropped(self, settings):
        settings.DJANGO_INSPECTOR = {"MAX_EVENTS_PER_TRACE": 1}
        buffer_event("ggg", "sql.query", {})
        buffer_event("ggg", "request.completed", {})
        assert [e["event_type"] for e in _get_buffer()] == ["sql.query", "request.completed"]

    def test_clear_resets_the_dropped_count(self, settings):
        from django_inspector.storage.flush import dropped_count

        settings.DJANGO_INSPECTOR = {"MAX_EVENTS_PER_TRACE": 1}
        buffer_event("hhh", "sql.query", {})
        buffer_event("hhh", "sql.query", {})
        clear_buffer()
        assert dropped_count() == 0


@pytest.mark.django_db
def test_request_event_records_start_time_and_dropped_events(client, settings):
    settings.DJANGO_INSPECTOR = {"MAX_EVENTS_PER_TRACE": 2, "WATCHERS": {"log": True}}
    import logging

    from django.http import HttpResponse
    from django.test import RequestFactory

    from django_inspector.middleware import InspectorMiddleware

    def view(request):
        for i in range(4):
            logging.getLogger("app").warning("w%d", i)
        return HttpResponse("ok")

    InspectorMiddleware(view)(RequestFactory().get("/x/"))
    request_event = Event.objects.get(event_type="request.completed")
    others = Event.objects.exclude(event_type="request.completed")
    assert others.count() == 2
    assert request_event.metadata["events_dropped"] == 2
    # The request event is stamped with the request's start, before its children.
    assert request_event.timestamp <= min(e.timestamp for e in others)
