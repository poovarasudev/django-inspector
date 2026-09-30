"""Tests for the dashboard's cache and template pages and the request detail page."""

import pytest
from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from django_inspector.storage.models import Event

TRACE = "a" * 32


def cache_event(operation, **metadata):
    data = {
        "operation": operation,
        "alias": "default",
        "backend": "django.core.cache.backends.locmem.LocMemCache",
        "duration_ms": 0.1,
        "key": "k",
    }
    data.update(metadata)
    return Event.objects.create(trace_id=TRACE, event_type="cache." + operation, metadata=data)


class DashboardTestCase(TestCase):
    def get(self, url_name, *args, params=None, htmx=False):
        """Call a dashboard view through its URL (so inspector_required applies) as a staff user."""
        path = reverse("inspector:" + url_name, args=args)
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        request = RequestFactory().get(path, params or {}, **headers)
        request.user = User(username="staff", is_staff=True, is_active=True)
        match = resolve(path)
        return match.func(request, *match.args, **match.kwargs)

    def body(self, *args, **kwargs):
        response = self.get(*args, **kwargs)
        assert response.status_code == 200
        return response.content.decode()


class TestCachePages(DashboardTestCase):
    def setUp(self):
        self.hit = cache_event("get", key="profile:alpha", hit=True)
        self.miss = cache_event("get", key="profile:beta", hit=False, alias="other")
        self.write = cache_event(
            "set", key="settings-blob", ttl_seconds=None, value_type="str", value_size_bytes=12
        )
        self.bulk = cache_event(
            "get_many", key=None, keys=["k-one", "k-two", "k-three"],
            key_count=3, hit_count=2, miss_count=1,
        )
        self.sql = Event.objects.create(
            trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"}
        )

    def test_list_shows_only_cache_events(self):
        body = self.body("cache-list")
        assert "profile:alpha" in body
        assert "settings-blob" in body
        assert "3 keys" in body
        assert "SELECT 1" not in body

    def test_filter_by_operation(self):
        body = self.body("cache-list", params={"operation": "set"})
        assert "settings-blob" in body
        assert "profile:alpha" not in body

    def test_filter_by_result(self):
        body = self.body("cache-list", params={"result": "miss"})
        assert "profile:beta" in body
        assert "profile:alpha" not in body

    def test_filter_by_alias(self):
        body = self.body("cache-list", params={"alias": "other"})
        assert "profile:beta" in body
        assert "profile:alpha" not in body

    def test_filter_by_key_is_case_insensitive(self):
        body = self.body("cache-list", params={"key": "PROFILE"})
        assert "profile:alpha" in body
        assert "settings-blob" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("cache-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_shows_metadata_and_request_link(self):
        request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "GET", "path": "/menu/", "status_code": 200},
        )
        body = self.body("cache-detail", self.write.pk)
        assert "settings-blob" in body
        assert "Never expires" in body
        assert "12 bytes" in body
        assert reverse("inspector:request-detail", args=[request_event.pk]) in body

    def test_detail_lists_keys_of_multi_key_operation(self):
        body = self.body("cache-detail", self.bulk.pk)
        assert "Keys (3)" in body
        assert "k-three" in body
        assert "2 / 3 hit" in body

    def test_detail_404s_for_non_cache_event(self):
        with pytest.raises(Http404):
            self.get("cache-detail", self.sql.pk)

    def test_sidebar_links_to_cache(self):
        assert reverse("inspector:cache-list") in self.body("live-feed")
