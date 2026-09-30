"""Tests for the Signal Watcher (SIGL-01..SIGL-06)."""

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group
from django.db.models.signals import post_save
from django.dispatch import Signal
from django.test import TestCase, override_settings

from django_inspector.conf import inspector_settings
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.signal import SignalWatcher

order_placed = Signal()
order_shipped = Signal()
not_watched = Signal()
NOT_A_SIGNAL = object()

WATCH = [
    "tests.test_signal_watcher.order_placed",
    "tests.test_signal_watcher.order_shipped",
    "django.db.models.signals.post_save",
]
HAS_ASYNC_SIGNALS = hasattr(Signal, "asend")


class Order:
    pass


def first_receiver(sender, **kwargs):
    return "first"


def second_receiver(sender, **kwargs):
    return "second"


def failing_receiver(sender, **kwargs):
    raise ValueError("receiver failed")


def shipped_receiver(sender, **kwargs):
    return "shipped"


def cascading_receiver(sender, **kwargs):
    order_shipped.send(sender=sender)
    return "cascaded"


async def async_receiver(sender, **kwargs):
    return "async"


def inspector_receiver(sender, **kwargs):
    return "inspector"


inspector_receiver.__module__ = "django_inspector.watchers.fake"


def signal_events():
    return [e for e in _get_buffer() if e["event_type"] == "signal.dispatched"]


def receiver_names(event):
    return [r["receiver"].rsplit(".", 1)[1] for r in event["metadata"]["receivers"]]


@override_settings(DJANGO_INSPECTOR={"SIGNAL_WATCH_LIST": WATCH})
class TestSignalWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert SignalWatcher.watcher_name == "signal"

    def test_off_by_default(self):
        assert inspector_settings.watcher_enabled("signal") is False

    def test_enable_patches_watched_signals_and_disable_restores(self):
        watcher = SignalWatcher()
        watcher.enable()
        try:
            assert "send" in order_placed.__dict__
            assert "_live_receivers" in order_placed.__dict__
            assert "send" not in not_watched.__dict__
        finally:
            watcher.disable()
        for attr in ("send", "send_robust", "_live_receivers", "_inspector_signal_name"):
            assert attr not in order_placed.__dict__

    def test_second_watcher_does_not_double_patch_or_unpatch(self):
        first, second = SignalWatcher(), SignalWatcher()
        first.enable()
        second.enable()
        second.disable()
        try:
            assert "send" in order_placed.__dict__
        finally:
            first.disable()
        assert "send" not in order_placed.__dict__

    def test_bad_watch_list_entries_are_skipped_with_warning(self):
        watch = ["no.such.signal", "tests.test_signal_watcher.NOT_A_SIGNAL", WATCH[0]]
        watcher = SignalWatcher()
        with override_settings(DJANGO_INSPECTOR={"SIGNAL_WATCH_LIST": watch}):
            with self.assertLogs("django_inspector", level="WARNING") as logs:
                watcher.enable()
        try:
            assert len(logs.output) == 2
            assert "send" in order_placed.__dict__
        finally:
            watcher.disable()


@override_settings(DJANGO_INSPECTOR={"SIGNAL_WATCH_LIST": WATCH})
class SignalWatcherTestCase(TestCase):
    def setUp(self):
        self.watcher = SignalWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()

    def connect(self, signal, receiver, **kwargs):
        signal.connect(receiver, **kwargs)
        self.addCleanup(signal.disconnect, receiver, **kwargs)


class TestSignalCapture(SignalWatcherTestCase):
    def test_records_signal_sender_and_receivers_in_order(self):
        self.connect(order_placed, first_receiver)
        self.connect(order_placed, second_receiver)
        responses = order_placed.send(sender=Order, order_id=1)
        assert responses == [(first_receiver, "first"), (second_receiver, "second")]
        [event] = signal_events()
        meta = event["metadata"]
        assert meta["signal"] == "tests.test_signal_watcher.order_placed"
        assert meta["sender"] == "tests.test_signal_watcher.Order"
        assert meta["receiver_count"] == 2
        assert receiver_names(event) == ["first_receiver", "second_receiver"]
        assert meta["receivers"][0]["receiver"] == "tests.test_signal_watcher.first_receiver"
        assert all(r["duration_ms"] >= 0 for r in meta["receivers"])
        assert meta["method"] == "send"
        assert meta["duration_ms"] >= 0
        assert event["trace_id"] == self.trace_id

    def test_receiver_error_under_send_is_recorded_and_reraised(self):
        self.connect(order_placed, failing_receiver)
        self.connect(order_placed, second_receiver)
        with pytest.raises(ValueError, match="receiver failed"):
            order_placed.send(sender=Order)
        [event] = signal_events()
        meta = event["metadata"]
        assert meta["receiver_count"] == 2
        assert receiver_names(event) == ["failing_receiver"]  # second never ran
        assert meta["receivers"][0]["error"] == "ValueError: receiver failed"
        assert meta["error"] == "ValueError: receiver failed"

    def test_send_robust_records_receiver_error_and_keeps_responses(self):
        self.connect(order_placed, failing_receiver)
        self.connect(order_placed, second_receiver)
        responses = order_placed.send_robust(sender=Order)
        assert responses[0][0] is failing_receiver
        assert isinstance(responses[0][1], ValueError)
        assert responses[1] == (second_receiver, "second")
        [event] = signal_events()
        meta = event["metadata"]
        assert meta["method"] == "send_robust"
        assert receiver_names(event) == ["failing_receiver", "second_receiver"]
        assert meta["receivers"][0]["error"] == "ValueError: receiver failed"
        assert "error" not in meta

    def test_inspector_receivers_are_excluded(self):
        self.connect(order_placed, inspector_receiver)
        self.connect(order_placed, first_receiver)
        responses = order_placed.send(sender=Order)
        assert (inspector_receiver, "inspector") in responses
        [event] = signal_events()
        assert event["metadata"]["receiver_count"] == 1
        assert receiver_names(event) == ["first_receiver"]

    def test_dispatch_with_only_inspector_receivers_is_not_recorded(self):
        self.connect(order_placed, inspector_receiver)
        order_placed.send(sender=Order)
        assert signal_events() == []

    def test_dispatch_without_receivers_is_not_recorded(self):
        assert order_placed.send(sender=Order) == []
        assert signal_events() == []

    def test_unwatched_signal_is_not_recorded(self):
        self.connect(not_watched, first_receiver)
        assert not_watched.send(sender=Order) == [(first_receiver, "first")]
        assert signal_events() == []

    def test_nested_dispatch_gets_its_own_event(self):
        self.connect(order_placed, cascading_receiver)
        self.connect(order_shipped, shipped_receiver)
        order_placed.send(sender=Order)
        inner, outer = signal_events()  # the inner dispatch finishes first
        assert inner["metadata"]["signal"] == "tests.test_signal_watcher.order_shipped"
        assert receiver_names(inner) == ["shipped_receiver"]
        assert outer["metadata"]["signal"] == "tests.test_signal_watcher.order_placed"
        assert receiver_names(outer) == ["cascading_receiver"]

    def test_sender_forms(self):
        self.connect(order_placed, first_receiver)
        order_placed.send(sender=None)
        order_placed.send(sender="checkout")
        order_placed.send(sender=Order())
        assert [e["metadata"]["sender"] for e in signal_events()] == [
            None,
            "checkout",
            "tests.test_signal_watcher.Order",
        ]

    def test_has_listeners_is_unaffected(self):
        self.connect(order_placed, first_receiver)
        assert order_placed.has_listeners(Order) is True
        assert signal_events() == []

    def test_no_events_without_active_trace(self):
        self.connect(order_placed, first_receiver)
        clear_trace_id(self.token)
        self.token = None
        assert order_placed.send(sender=Order) == [(first_receiver, "first")]
        assert signal_events() == []

    def test_real_post_save_from_model_save(self):
        self.connect(post_save, first_receiver, sender=Group)
        Group.objects.create(name="editors")
        events = [e for e in signal_events() if e["metadata"]["signal"] == "django.db.models.signals.post_save"]
        [event] = events
        assert event["metadata"]["sender"] == "django.contrib.auth.models.Group"
        assert "first_receiver" in receiver_names(event)


@pytest.mark.skipif(not HAS_ASYNC_SIGNALS, reason="async signal dispatch needs Django >= 5.0")
class TestAsyncSignalCapture(SignalWatcherTestCase):
    def test_asend_times_sync_and_async_receivers(self):
        self.connect(order_placed, first_receiver)
        self.connect(order_placed, async_receiver)
        responses = async_to_sync(order_placed.asend)(sender=Order)
        assert sorted(responses, key=lambda r: r[1]) == [
            (async_receiver, "async"),
            (first_receiver, "first"),
        ]
        [event] = signal_events()
        assert event["metadata"]["method"] == "asend"
        assert event["metadata"]["receiver_count"] == 2
        assert sorted(receiver_names(event)) == ["async_receiver", "first_receiver"]

    def test_send_with_an_async_receiver(self):
        self.connect(order_placed, async_receiver)
        assert order_placed.send(sender=Order) == [(async_receiver, "async")]
        [event] = signal_events()
        assert receiver_names(event) == ["async_receiver"]
        assert event["metadata"]["receivers"][0]["duration_ms"] >= 0
