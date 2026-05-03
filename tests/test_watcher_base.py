import pytest

from django_inspector.watchers.base import BaseWatcher


class ConcreteWatcher(BaseWatcher):
    watcher_name = "test"
    hooks_installed = False

    def install_hooks(self):
        ConcreteWatcher.hooks_installed = True

    def remove_hooks(self):
        ConcreteWatcher.hooks_installed = False


def test_watcher_name_required():
    class NoNameWatcher(BaseWatcher):
        watcher_name = ""
        def install_hooks(self): pass
        def remove_hooks(self): pass

    with pytest.raises(ValueError, match="watcher_name"):
        NoNameWatcher()


def test_watcher_enable_installs_hooks():
    w = ConcreteWatcher()
    assert not w.is_enabled
    w.enable()
    assert w.is_enabled
    assert ConcreteWatcher.hooks_installed


def test_watcher_disable_removes_hooks():
    w = ConcreteWatcher()
    w.enable()
    w.disable()
    assert not w.is_enabled
    assert not ConcreteWatcher.hooks_installed


def test_watcher_enable_idempotent():
    w = ConcreteWatcher()
    w.enable()
    w.enable()
    assert w.is_enabled


def test_watcher_record_no_trace_does_nothing():
    from django_inspector.tracing.context import clear_trace_id
    from django_inspector.storage.flush import clear_buffer, _get_buffer

    clear_trace_id()
    clear_buffer()
    w = ConcreteWatcher()
    w.enable()
    w.record("test.event", {"key": "value"})
    assert len(_get_buffer()) == 0


def test_watcher_record_with_trace_buffers_event():
    from django_inspector.tracing.context import set_trace_id, clear_trace_id
    from django_inspector.storage.flush import clear_buffer, _get_buffer

    clear_buffer()
    token = set_trace_id("abc123def456" + "0" * 20)
    try:
        w = ConcreteWatcher()
        w.enable()
        w.record("test.query", {"sql": "SELECT 1"})
        buf = _get_buffer()
        assert len(buf) == 1
        assert buf[0]["event_type"] == "test.query"
        assert buf[0]["metadata"]["sql"] == "SELECT 1"
    finally:
        clear_trace_id(token)
        clear_buffer()
