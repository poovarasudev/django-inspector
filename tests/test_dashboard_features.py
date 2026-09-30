"""Dashboard hardening and features: filters, trace search, export, pagination, assets, CSP."""

import datetime
import json

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from django_inspector.storage.models import Event

TRACE = "b" * 32


def request_event(trace=TRACE, when=None, **metadata):
    data = {"method": "GET", "path": "/x/", "status_code": 200, "latency_ms": 5}
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="request.completed", metadata=data,
        timestamp=when or timezone.now(),
    )


class DashboardClient(TestCase):
    def get(self, url_name, *args, params=None, user=None):
        path = reverse("inspector:" + url_name, args=args)
        request = RequestFactory().get(path, params or {})
        request.user = user or User(username="staff", is_staff=True, is_active=True)
        match = resolve(path)
        return match.func(request, *match.args, **match.kwargs)

    def body(self, *args, **kwargs):
        response = self.get(*args, **kwargs)
        assert response.status_code == 200, response.status_code
        return response.content.decode()


class TestDateFilters(DashboardClient):
    def test_invalid_dates_are_ignored_instead_of_crashing(self):
        request_event()
        for params in ({"from_date": "not-a-date"}, {"to_date": "2026-02-30"}):
            assert self.get("requests-list", params=params).status_code == 200
            assert self.get("exceptions-list", params=params).status_code == 200

    def test_to_date_includes_the_whole_day(self):
        noon = timezone.make_aware(datetime.datetime(2026, 5, 3, 12, 0)) if timezone.is_aware(
            timezone.now()
        ) else datetime.datetime(2026, 5, 3, 12, 0)
        request_event(when=noon, path="/on-the-day/")
        body = self.body("requests-list", params={"to_date": "2026-05-03"})
        assert "/on-the-day/" in body
        body = self.body("requests-list", params={"from_date": "2026-05-04"})
        assert "/on-the-day/" not in body


class TestTraceSearch(DashboardClient):
    def test_requests_list_filters_by_trace_id(self):
        request_event(path="/wanted/")
        request_event(trace="c" * 32, path="/other/")
        body = self.body("requests-list", params={"trace": " " + TRACE.upper() + " "})
        assert "/wanted/" in body
        assert "/other/" not in body

    def test_trace_url_redirects_to_the_request(self):
        event = request_event()
        response = self.get("trace-detail", TRACE)
        assert response.status_code == 302
        assert response["Location"] == reverse("inspector:request-detail", args=[event.pk])

    def test_unknown_trace_is_404(self):
        from django.http import Http404

        try:
            self.get("trace-detail", "d" * 32)
        except Http404:
            return
        raise AssertionError("expected Http404")


class TestExport(DashboardClient):
    def test_exports_the_whole_trace_as_json(self):
        event = request_event()
        Event.objects.create(trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"})
        Event.objects.create(trace_id="e" * 32, event_type="sql.query", metadata={"sql": "OTHER"})
        response = self.get("request-export", event.pk)
        assert response["Content-Type"] == "application/json"
        assert "attachment" in response["Content-Disposition"]
        data = json.loads(response.content)
        assert data["trace_id"] == TRACE
        assert [e["event_type"] for e in data["events"]] == ["request.completed", "sql.query"]
        assert "OTHER" not in response.content.decode()

    def test_detail_page_links_to_the_export(self):
        event = request_event()
        assert reverse("inspector:request-export", args=[event.pk]) in self.body("request-detail", event.pk)

    def test_export_requires_access(self):
        event = request_event()
        assert self.get("request-export", event.pk, user=AnonymousUser()).status_code == 403


class TestDetailCap(DashboardClient):
    def test_huge_traces_are_truncated_with_a_notice(self):
        from django_inspector.dashboard import views

        event = request_event()
        Event.objects.bulk_create(
            Event(trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT %d" % i})
            for i in range(10)
        )
        with self.settings():
            original = views.MAX_DETAIL_EVENTS
            views.MAX_DETAIL_EVENTS = 5
            try:
                body = self.body("request-detail", event.pk)
            finally:
                views.MAX_DETAIL_EVENTS = original
        assert "Showing the first 5 events" in body


class TestCappedPagination(DashboardClient):
    def test_count_is_capped(self):
        from django_inspector.dashboard.views import CappedPaginator

        for _ in range(7):
            request_event()
        paginator = CappedPaginator(Event.objects.all(), 2, max_count=5)
        assert paginator.count == 5
        assert paginator.count_capped is True
        assert paginator.num_pages == 3

    def test_small_counts_are_exact(self):
        from django_inspector.dashboard.views import CappedPaginator

        request_event()
        paginator = CappedPaginator(Event.objects.all(), 2, max_count=5)
        assert paginator.count == 1
        assert paginator.count_capped is False

    def test_page_links_encode_filter_values(self):
        for _ in range(30):
            request_event(path="/a&b/")
        body = self.body("requests-list", params={"path": "a&b"})
        assert "path=a%26b&amp;page=2" in body


class TestAssetsAndCsp(DashboardClient):
    def test_pages_load_no_third_party_or_inline_scripts(self):
        request_event()
        body = self.body("live-feed")
        assert "unpkg.com" not in body
        assert "<script>" not in body
        assert "onclick=" not in body
        assert reverse("inspector:asset", args=["htmx.min.js"]) in body

    def test_detail_tabs_have_no_inline_handlers(self):
        event = request_event()
        body = self.body("request-detail", event.pk, params={"view": "tabs"})
        assert "onclick=" not in body
        assert "<script>" not in body
        assert 'data-inspector-tab="queries-tab"' in body

    def test_bundled_htmx_is_served(self):
        response = self.get("asset", "htmx.min.js")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/javascript")
        assert response["X-Content-Type-Options"] == "nosniff"
        assert b"htmx" in response.content

    def test_stylesheet_is_served(self):
        response = self.get("asset", "inspector.css")
        assert response["Content-Type"].startswith("text/css")
        assert b"--ins-accent" in response.content

    def test_unknown_asset_is_404(self):
        from django.http import Http404

        try:
            self.get("asset", "settings.py")
        except Http404:
            return
        raise AssertionError("expected Http404")

    def test_assets_require_access(self):
        assert self.get("asset", "htmx.min.js", user=AnonymousUser()).status_code == 403

    def test_dashboard_responses_carry_a_strict_csp(self):
        csp = self.get("live-feed")["Content-Security-Policy"]
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "unsafe-eval" not in csp

    @override_settings(DEBUG=False)
    def test_forbidden_response_also_has_csp(self):
        response = self.get("live-feed", user=AnonymousUser())
        assert response.status_code == 403
        assert "Content-Security-Policy" in response
