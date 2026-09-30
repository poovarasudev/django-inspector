"""Tests for the Cache Watcher (CACHE-01..CACHE-07)."""

import pytest
from asgiref.sync import async_to_sync
from django.core.cache import caches
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase, override_settings

from django_inspector.conf import inspector_settings
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.cache import CacheWatcher

LOCMEM = "django.core.cache.backends.locmem.LocMemCache"


class BrokenCache(LocMemCache):
    """A backend whose get() fails, like a cache server that is down."""

    def get(self, key, default=None, version=None):
        raise ConnectionError("cache down")


class KwargsCache(LocMemCache):
    """Mimics third-party backends (e.g. django-redis) whose methods take extra arguments."""

    received_nx = None

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None, nx=False):
        KwargsCache.received_nx = nx
        return super().set(key, value, timeout=timeout, version=version)


TEST_CACHES = {
    "default": {"BACKEND": LOCMEM, "LOCATION": "inspector-tests-default"},
    "other": {"BACKEND": LOCMEM, "LOCATION": "inspector-tests-other"},
    "broken": {"BACKEND": "tests.test_cache_watcher.BrokenCache", "LOCATION": "inspector-tests-broken"},
    "kwargs": {"BACKEND": "tests.test_cache_watcher.KwargsCache", "LOCATION": "inspector-tests-kwargs"},
}


def cache_events():
    return [e for e in _get_buffer() if e["event_type"].startswith("cache.")]


@override_settings(CACHES=TEST_CACHES)
class TestCacheWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert CacheWatcher.watcher_name == "cache"

    def test_off_by_default(self):
        assert inspector_settings.watcher_enabled("cache") is False

    def test_enable_wraps_and_disable_restores_own_methods(self):
        original = LocMemCache.__dict__["get"]
        watcher = CacheWatcher()
        watcher.enable()
        try:
            assert LocMemCache.__dict__["get"] is not original
            assert LocMemCache.__dict__["get"].__wrapped__ is original
        finally:
            watcher.disable()
        assert LocMemCache.__dict__["get"] is original

    def test_enable_and_disable_are_idempotent(self):
        original = LocMemCache.__dict__["set"]
        watcher = CacheWatcher()
        watcher.enable()
        watcher.enable()
        try:
            assert LocMemCache.__dict__["set"].__wrapped__ is original
        finally:
            watcher.disable()
        watcher.disable()
        assert LocMemCache.__dict__["set"] is original

    def test_second_watcher_does_not_double_wrap(self):
        original = LocMemCache.__dict__["get"]
        first, second = CacheWatcher(), CacheWatcher()
        first.enable()
        second.enable()
        try:
            assert LocMemCache.__dict__["get"].__wrapped__ is original
        finally:
            second.disable()
            first.disable()
        assert LocMemCache.__dict__["get"] is original

    def test_inherited_methods_are_removed_on_disable(self):
        watcher = CacheWatcher()
        with override_settings(CACHES={"default": TEST_CACHES["kwargs"]}):
            watcher.enable()
        try:
            assert "get" in KwargsCache.__dict__
        finally:
            watcher.disable()
        assert "get" not in KwargsCache.__dict__
        assert KwargsCache.get is LocMemCache.get

    def test_unimportable_backend_is_skipped_with_warning(self):
        config = {**TEST_CACHES, "missing": {"BACKEND": "no.such.CacheBackend"}}
        watcher = CacheWatcher()
        with override_settings(CACHES=config):
            with self.assertLogs("django_inspector", level="WARNING") as logs:
                watcher.enable()
        try:
            assert any("missing" in line for line in logs.output)
            assert getattr(LocMemCache.__dict__["get"], "_inspector_wrapped", False)
        finally:
            watcher.disable()


@override_settings(CACHES=TEST_CACHES)
class CacheWatcherTestCase(TestCase):
    """Watcher enabled, an active trace, empty caches and an empty buffer."""

    def setUp(self):
        for alias in ("default", "other", "kwargs"):
            caches[alias].clear()
        self.watcher = CacheWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()


class TestSingleKeyOperations(CacheWatcherTestCase):
    def test_get_hit(self):
        caches["default"].set("greeting", "hello")
        clear_buffer()
        assert caches["default"].get("greeting") == "hello"
        [event] = cache_events()
        meta = event["metadata"]
        assert event["event_type"] == "cache.get"
        assert meta["operation"] == "get"
        assert meta["key"] == "greeting"
        assert meta["hit"] is True
        assert meta["alias"] == "default"
        assert meta["backend"] == LOCMEM
        assert meta["duration_ms"] >= 0

    def test_get_miss_returns_callers_default(self):
        assert caches["default"].get("absent", "fallback") == "fallback"
        [event] = cache_events()
        assert event["metadata"]["hit"] is False

    def test_get_with_positional_default_and_version(self):
        caches["default"].set("k", "v2", version=2)
        clear_buffer()
        assert caches["default"].get("k", "fallback", 1) == "fallback"
        assert caches["default"].get("k", "fallback", 2) == "v2"
        assert [e["metadata"]["hit"] for e in cache_events()] == [False, True]

    def test_stored_none_is_a_hit(self):
        caches["default"].set("nothing", None)
        clear_buffer()
        assert caches["default"].get("nothing", "fallback") is None
        assert cache_events()[0]["metadata"]["hit"] is True

    def test_set_records_ttl_and_size_but_never_the_value(self):
        caches["default"].set("name", "héllo", timeout=30)
        [event] = cache_events()
        meta = event["metadata"]
        assert event["event_type"] == "cache.set"
        assert meta["key"] == "name"
        assert meta["ttl_seconds"] == 30
        assert meta["value_type"] == "str"
        assert meta["value_size_bytes"] == 6
        assert "héllo" not in str(meta)

    def test_set_default_timeout_uses_backend_default(self):
        caches["default"].set("k", b"abc")
        meta = cache_events()[0]["metadata"]
        assert meta["ttl_seconds"] == 300
        assert meta["value_size_bytes"] == 3

    def test_set_timeout_none_means_never_expires(self):
        caches["default"].set("k", 1, timeout=None)
        assert cache_events()[0]["metadata"]["ttl_seconds"] is None

    def test_value_size_is_none_for_other_types(self):
        caches["default"].set("k", {"a": 1})
        meta = cache_events()[0]["metadata"]
        assert meta["value_type"] == "dict"
        assert meta["value_size_bytes"] is None

    def test_add_records_stored_flag(self):
        caches["default"].add("k", "first")
        caches["default"].add("k", "second")
        assert [e["metadata"]["stored"] for e in cache_events()] == [True, False]

    def test_delete_records_deleted_flag(self):
        caches["default"].set("k", 1)
        clear_buffer()
        caches["default"].delete("k")
        caches["default"].delete("k")
        events = cache_events()
        assert [e["event_type"] for e in events] == ["cache.delete", "cache.delete"]
        assert [e["metadata"]["deleted"] for e in events] == [True, False]

    def test_clear_records_star_key(self):
        caches["default"].clear()
        [event] = cache_events()
        assert event["event_type"] == "cache.clear"
        assert event["metadata"]["key"] == "*"

    def test_records_origin_in_app_code(self):
        caches["default"].get("k")
        meta = cache_events()[0]["metadata"]
        assert "test_cache_watcher" in meta["origin_file"]
        assert meta["origin_line"] is not None

    def test_get_or_set_records_the_underlying_get_and_add(self):
        assert caches["default"].get_or_set("k", "computed") == "computed"
        ops = [e["metadata"]["operation"] for e in cache_events()]
        assert ops[:2] == ["get", "add"]


class TestCacheEventContext(CacheWatcherTestCase):
    def test_events_carry_trace_id(self):
        caches["default"].get("k")
        assert cache_events()[0]["trace_id"] == self.trace_id

    def test_no_events_without_active_trace(self):
        clear_trace_id(self.token)
        self.token = None
        assert caches["default"].get("k", "d") == "d"
        caches["default"].set("k", 1)
        assert cache_events() == []

    def test_alias_resolved_when_two_aliases_share_a_class(self):
        caches["default"].get("k")
        caches["other"].get("k")
        assert [e["metadata"]["alias"] for e in cache_events()] == ["default", "other"]

    def test_backend_error_is_recorded_and_reraised(self):
        with pytest.raises(ConnectionError, match="cache down"):
            caches["broken"].get("k")
        [event] = cache_events()
        assert event["metadata"]["error"] == "ConnectionError: cache down"
        assert event["metadata"]["alias"] == "broken"
        assert "hit" not in event["metadata"]

    def test_extra_backend_arguments_pass_through(self):
        KwargsCache.received_nx = None
        caches["kwargs"].set("k", "v", nx=True)
        assert KwargsCache.received_nx is True
        [event] = cache_events()
        assert event["event_type"] == "cache.set"
        assert event["metadata"]["alias"] == "kwargs"
        assert event["metadata"]["backend"] == "tests.test_cache_watcher.KwargsCache"

    def test_async_aget_is_recorded(self):
        caches["default"].set("k", "v")
        clear_buffer()

        async def run():
            return await caches["default"].aget("k")

        assert async_to_sync(run)() == "v"
        [event] = cache_events()
        assert event["event_type"] == "cache.get"
        assert event["trace_id"] == self.trace_id

    def test_card_number_in_key_is_masked(self):
        caches["default"].get("card:4111111111111111")
        assert cache_events()[0]["metadata"]["key"] == "***REDACTED***"


class TestMultiKeyOperations(CacheWatcherTestCase):
    def test_get_many_is_one_event_with_hit_counts(self):
        caches["default"].set("a", 1)
        caches["default"].set("b", 2)
        clear_buffer()
        assert caches["default"].get_many(["a", "b", "c"]) == {"a": 1, "b": 2}
        [event] = cache_events()  # BaseCache.get_many calls get() per key; those are not recorded
        meta = event["metadata"]
        assert event["event_type"] == "cache.get_many"
        assert meta["keys"] == ["a", "b", "c"]
        assert meta["key_count"] == 3
        assert meta["hit_count"] == 2
        assert meta["miss_count"] == 1

    def test_get_many_accepts_a_generator(self):
        caches["default"].set("a", 1)
        caches["default"].set("b", 2)
        clear_buffer()
        assert caches["default"].get_many(k for k in ["a", "b"]) == {"a": 1, "b": 2}
        assert cache_events()[0]["metadata"]["keys"] == ["a", "b"]

    def test_set_many_is_one_event(self):
        assert caches["default"].set_many({"a": 1, "b": 2}, timeout=60) == []
        [event] = cache_events()
        meta = event["metadata"]
        assert event["event_type"] == "cache.set_many"
        assert meta["keys"] == ["a", "b"]
        assert meta["key_count"] == 2
        assert meta["ttl_seconds"] == 60
        assert meta["failed_keys"] == []

    def test_delete_many_accepts_a_generator(self):
        caches["default"].set_many({"a": 1, "b": 2})
        clear_buffer()
        caches["default"].delete_many(k for k in ["a", "b"])
        [event] = cache_events()
        assert event["event_type"] == "cache.delete_many"
        assert event["metadata"]["keys"] == ["a", "b"]
        assert event["metadata"]["key_count"] == 2
        assert caches["default"].get("a") is None

    def test_recorded_keys_are_capped(self):
        keys = ["k%d" % i for i in range(150)]
        caches["default"].get_many(keys)
        meta = cache_events()[0]["metadata"]
        assert len(meta["keys"]) == 100
        assert meta["key_count"] == 150
        assert meta["miss_count"] == 150


@override_settings(CACHES=TEST_CACHES)
class TestMultiKeyLifecycle(TestCase):
    def test_inherited_many_methods_are_wrapped_then_removed(self):
        from django.core.cache.backends.base import BaseCache

        assert "get_many" not in LocMemCache.__dict__
        watcher = CacheWatcher()
        watcher.enable()
        try:
            assert "get_many" in LocMemCache.__dict__
        finally:
            watcher.disable()
        assert "get_many" not in LocMemCache.__dict__
        assert LocMemCache.get_many is BaseCache.get_many
