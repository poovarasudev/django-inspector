import pytest

from django_inspector.storage.flush import buffer_event, clear_buffer, flush_events, _get_buffer
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
