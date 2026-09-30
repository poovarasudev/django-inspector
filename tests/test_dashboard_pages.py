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


def template_event(render_id, name, parent_id=None, depth=0, relation=None, trace_id=TRACE, **extra):
    data = {
        "render_id": render_id,
        "parent_id": parent_id,
        "depth": depth,
        "relation": relation,
        "name": name,
        "origin_path": "/app/templates/%s" % name,
        "loader": "django.template.loaders.filesystem.Loader",
        "duration_ms": 1.5,
        "context_key_count": 2,
        "context_keys": ["title", "user"],
    }
    data.update(extra)
    return Event.objects.create(trace_id=trace_id, event_type="template.rendered", metadata=data)


class TestTemplatePages(DashboardTestCase):
    def setUp(self):
        self.page = template_event(1, "shop/page.html")
        self.base = template_event(2, "shop/base.html", parent_id=1, depth=1, relation="extends")
        self.card = template_event(3, "shop/card.html", parent_id=2, depth=2, relation="include")
        # Same render ids in another trace: must never be linked to this trace's tree.
        self.foreign = template_event(
            3, "elsewhere/card.html", parent_id=2, depth=2, relation="include", trace_id="b" * 32
        )

    def test_list_shows_renders(self):
        body = self.body("templates-list")
        assert "shop/page.html" in body
        assert "shop/card.html" in body

    def test_filter_by_name_is_case_insensitive(self):
        body = self.body("templates-list", params={"name": "CARD"})
        assert "shop/card.html" in body
        assert "shop/page.html" not in body

    def test_top_level_only(self):
        body = self.body("templates-list", params={"top_level": "on"})
        assert "shop/page.html" in body
        assert "shop/card.html" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("templates-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_links_parent_and_children_in_same_trace_only(self):
        body = self.body("template-detail", self.base.pk)
        assert reverse("inspector:template-detail", args=[self.page.pk]) in body
        assert reverse("inspector:template-detail", args=[self.card.pk]) in body
        assert reverse("inspector:template-detail", args=[self.foreign.pk]) not in body
        assert "/app/templates/shop/base.html" in body
        assert "title" in body

    def test_detail_of_top_level_render(self):
        body = self.body("template-detail", self.page.pk)
        assert "Top-level render" in body
        assert reverse("inspector:template-detail", args=[self.base.pk]) in body

    def test_detail_shows_error(self):
        failed = template_event(9, "shop/broken.html", error="ValueError: boom")
        assert "ValueError: boom" in self.body("template-detail", failed.pk)

    def test_sidebar_links_to_templates(self):
        assert reverse("inspector:templates-list") in self.body("live-feed")


class TestRequestDetailIntegration(DashboardTestCase):
    def setUp(self):
        self.request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "GET", "path": "/shop/", "status_code": 200, "latency_ms": 12.0},
        )
        cache_event("get", key="profile:alpha", hit=True)
        cache_event("get", key="profile:beta", hit=False)
        cache_event("get_many", key=None, keys=["k-one", "k-two", "k-three"],
                    key_count=3, hit_count=2, miss_count=1)
        template_event(1, "shop/page.html")
        template_event(2, "shop/card.html", parent_id=1, depth=1, relation="include")

    def test_summary_counts_cache_and_templates(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "Cache Ops" in body
        assert ">3 / 2<" in body  # hits 1 + 2 from get_many, misses 1 + 1
        assert "Template Renders" in body

    def test_timeline_labels_cache_and_template_events(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "inspector-timeline-item--cache" in body
        assert "inspector-timeline-item--template" in body
        assert "profile:alpha" in body
        assert "shop/card.html" in body

    def test_tabs_view_has_cache_and_templates_tabs(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "tabs"})
        assert "Cache (3)" in body
        assert "Templates (2)" in body

    def test_waterfall_colours_cache_and_template_bars(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "waterfall"})
        assert "inspector-waterfall-bar--cache" in body
        assert "inspector-waterfall-bar--template" in body
