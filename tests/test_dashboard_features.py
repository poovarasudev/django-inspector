"""Dashboard hardening and features: filters, trace search, export, pagination, assets, CSP."""

import datetime
import json

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from django_inspector.storage.models import Event

TRACE = "b" * 32


def _always_allowed(request):
    return True


def request_event(trace=TRACE, when=None, **metadata):
    data = {"method": "GET", "path": "/x/", "status_code": 200, "latency_ms": 5}
    data.update(metadata)
    # Mirrors what flush_events() derives at write time: the real columns,
    # not just the metadata dict, since requests-list filters (SCHEMA-06)
    # and pinning act on the columns.
    return Event.objects.create(
        trace_id=trace, event_type="request.completed", metadata=data,
        method=data.get("method"), path=data.get("path"), status_code=data.get("status_code"),
        duration_ms=data.get("latency_ms"),
        timestamp=when or timezone.now(),
    )


def trace_event(trace_id, name, status="ok", when=None, **metadata):
    data = {"name": name, "duration_ms": 5.0, "status": status, "error_type": None}
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace_id, event_type="trace.completed", metadata=data,
        duration_ms=data.get("duration_ms"), timestamp=when or timezone.now(),
    )


def command_trace_event(trace_id, name="backfill", status="ok", when=None, **metadata):
    data = {
        "name": name, "command": name, "duration_ms": 5.0, "status": status, "error_type": None,
        "args": [], "options": {}, "exit_code": 0,
        "stdout": "", "stdout_truncated": False, "stderr": "", "stderr_truncated": False,
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace_id, event_type="command.completed", metadata=data,
        duration_ms=data.get("duration_ms"), timestamp=when or timezone.now(),
    )


class DashboardClient(TestCase):
    def get(self, url_name, *args, params=None, user=None):
        path = reverse("inspector:" + url_name, args=args)
        request = RequestFactory().get(path, params or {})
        request.user = user or User(username="staff", is_staff=True, is_active=True)
        match = resolve(path)
        return match.func(request, *match.args, **match.kwargs)

    def post(self, url_name, *args, data=None, user=None):
        path = reverse("inspector:" + url_name, args=args)
        request = RequestFactory().post(path, data or {})
        request.user = user or User(username="staff", is_staff=True, is_active=True)
        # Mirrors django.test.Client's default (enforce_csrf_checks=False):
        # CSRF enforcement itself is covered by a dedicated test below.
        request._dont_enforce_csrf_checks = True
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

    def test_trace_export_downloads_a_non_request_trace(self):
        trace_id = "c" * 32
        trace_event(trace_id, "nightly-report")
        Event.objects.create(trace_id=trace_id, event_type="sql.query", metadata={"sql": "SELECT 1"})
        Event.objects.create(trace_id="e" * 32, event_type="sql.query", metadata={"sql": "OTHER"})
        response = self.get("trace-export", trace_id)
        assert response["Content-Type"] == "application/json"
        data = json.loads(response.content)
        assert data["trace_id"] == trace_id
        assert sorted(e["event_type"] for e in data["events"]) == ["sql.query", "trace.completed"]

    def test_trace_export_404s_for_an_unknown_trace(self):
        from django.http import Http404

        with pytest.raises(Http404):
            self.get("trace-export", "d" * 32)

    def test_trace_export_requires_access(self):
        trace_id = "c" * 32
        trace_event(trace_id, "nightly-report")
        assert self.get("trace-export", trace_id, user=AnonymousUser()).status_code == 403


class TestTracePage(DashboardClient):
    def test_renders_a_trace_completed_trace(self):
        trace_id = "c" * 32
        trace_event(trace_id, "nightly-report", status="ok", duration_ms=42.5)
        body = self.body("trace-detail", trace_id)
        assert "nightly-report" in body
        assert "42.5" in body or "42.50" in body

    def test_error_status_and_error_type_are_shown(self):
        trace_id = "c" * 32
        trace_event(trace_id, "send-digest", status="error", error_type="builtins.ValueError")
        body = self.body("trace-detail", trace_id)
        assert "builtins.ValueError" in body

    def test_missing_anchor_still_renders_with_a_notice(self):
        trace_id = "c" * 32
        Event.objects.create(trace_id=trace_id, event_type="sql.query", metadata={"sql": "SELECT 1"})
        body = self.body("trace-detail", trace_id)
        assert "Incomplete trace" in body

    def test_unknown_trace_id_is_404(self):
        from django.http import Http404

        with pytest.raises(Http404):
            self.get("trace-detail", "d" * 32)

    def test_pin_button_is_shown(self):
        trace_id = "c" * 32
        trace_event(trace_id, "nightly-report")
        body = self.body("trace-detail", trace_id)
        assert reverse("inspector:trace-pin", args=[trace_id]) in body

    def test_requires_access(self):
        trace_id = "c" * 32
        trace_event(trace_id, "nightly-report")
        assert self.get("trace-detail", trace_id, user=AnonymousUser()).status_code == 403


class TestTracesList(DashboardClient):
    def test_lists_non_request_traces(self):
        trace_event("c" * 32, "nightly-report")
        request_event(path="/requests-not-here/")
        body = self.body("traces-list")
        assert "nightly-report" in body
        assert "/requests-not-here/" not in body

    def test_filter_by_name_is_case_insensitive(self):
        trace_event("c" * 32, "nightly-report")
        trace_event("d" * 32, "send-digest")
        body = self.body("traces-list", params={"name": "NIGHTLY"})
        assert "nightly-report" in body
        assert "send-digest" not in body

    def test_filter_by_status(self):
        trace_event("c" * 32, "nightly-report", status="ok")
        trace_event("d" * 32, "send-digest", status="error")
        body = self.body("traces-list", params={"status": "error"})
        assert "send-digest" in body
        assert "nightly-report" not in body

    def test_htmx_request_returns_table_partial(self):
        trace_event("c" * 32, "nightly-report")
        path = reverse("inspector:traces-list")
        request = RequestFactory().get(path, HTTP_HX_REQUEST="true")
        request.user = User(username="staff", is_staff=True, is_active=True)
        match = resolve(path)
        response = match.func(request, *match.args, **match.kwargs)
        body = response.content.decode()
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_sidebar_links_to_traces(self):
        assert reverse("inspector:traces-list") in self.body("live-feed")

    def test_requires_access(self):
        assert self.get("traces-list", user=AnonymousUser()).status_code == 403


class TestCommandTracePage(DashboardClient):
    def test_renders_the_command_section(self):
        trace_id = "c" * 32
        command_trace_event(
            trace_id, name="backfill", exit_code=0,
            args=["--dry-run"], options={"verbosity": 1},
            stdout="did the thing", stderr="",
        )
        body = self.body("trace-detail", trace_id)
        assert "backfill" in body
        assert "did the thing" in body
        assert "--dry-run" in body

    def test_nonzero_exit_code_and_truncated_output_are_shown(self):
        trace_id = "c" * 32
        command_trace_event(
            trace_id, status="error", exit_code=1, stdout="partial", stdout_truncated=True,
        )
        body = self.body("trace-detail", trace_id)
        assert "partial" in body
        assert "truncat" in body.lower()

    def test_command_traces_appear_on_the_generic_trace_page(self):
        # /trace/<id>/ handles command.completed the same way it already
        # handles trace.completed: no redirect, just the trace's own page.
        trace_id = "c" * 32
        command_trace_event(trace_id, name="backfill")
        response = self.get("trace-detail", trace_id)
        assert response.status_code == 200


class TestTracesListTypeFilter(DashboardClient):
    def test_lists_command_traces_alongside_trace_runs(self):
        trace_event("c" * 32, "nightly-report")
        command_trace_event("d" * 32, "backfill")
        body = self.body("traces-list")
        assert "nightly-report" in body
        assert "backfill" in body

    def test_filter_by_type_trace(self):
        trace_event("c" * 32, "nightly-report")
        command_trace_event("d" * 32, "backfill")
        body = self.body("traces-list", params={"type": "trace"})
        assert "nightly-report" in body
        assert "backfill" not in body

    def test_filter_by_type_command(self):
        trace_event("c" * 32, "nightly-report")
        command_trace_event("d" * 32, "backfill")
        body = self.body("traces-list", params={"type": "command"})
        assert "backfill" in body
        assert "nightly-report" not in body


class TestIssuesPageAccessAndCost(DashboardClient):
    def test_requires_access(self):
        assert self.get("issues", user=AnonymousUser()).status_code == 403

    def test_query_count_is_bounded(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        request_event()
        Event.objects.create(trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"})
        with CaptureQueriesContext(connection):
            self.get("issues")
        assert len(connection.queries) <= 12


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


class TestRequestsListColumnFilters(DashboardClient):
    """SCHEMA-06: method/status/path filters run against the real columns."""

    def setUp(self):
        request_event(trace="1" * 32, method="GET", path="/get-only/", status_code=200)
        request_event(trace="2" * 32, method="POST", path="/post-only/", status_code=404)
        request_event(trace="3" * 32, method="GET", path="/other/", status_code=500)

    def test_method_filter_returns_the_right_rows(self):
        body = self.body("requests-list", params={"method": "POST"})
        assert "/post-only/" in body
        assert "/get-only/" not in body
        assert "/other/" not in body

    def test_status_class_filter_returns_the_right_rows(self):
        body = self.body("requests-list", params={"status": "5xx"})
        assert "/other/" in body
        assert "/get-only/" not in body
        assert "/post-only/" not in body

    def test_path_filter_returns_the_right_rows(self):
        body = self.body("requests-list", params={"path": "post"})
        assert "/post-only/" in body
        assert "/get-only/" not in body
        assert "/other/" not in body

    def test_method_filter_uses_no_json_operator(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection):
            self.get("requests-list", params={"method": "GET"})
        sql = " ".join(q["sql"] for q in connection.queries).lower()
        assert "json_extract" not in sql
        assert "->>" not in sql

    def test_status_filter_uses_no_json_operator(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection):
            self.get("requests-list", params={"status": "4xx"})
        sql = " ".join(q["sql"] for q in connection.queries).lower()
        assert "json_extract" not in sql
        assert "->>" not in sql

    def test_path_filter_uses_no_json_operator(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection):
            self.get("requests-list", params={"path": "other"})
        sql = " ".join(q["sql"] for q in connection.queries).lower()
        assert "json_extract" not in sql
        assert "->>" not in sql


class TestPinning(DashboardClient):
    def setUp(self):
        self.event = request_event()
        Event.objects.create(trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"})

    def test_pin_updates_every_row_of_the_trace(self):
        response = self.post("trace-pin", TRACE, data={"pinned": "1"})
        assert response.status_code == 200
        assert list(Event.objects.filter(trace_id=TRACE).values_list("pinned", flat=True)) == [True, True]

    def test_unpin_updates_every_row_of_the_trace(self):
        Event.objects.filter(trace_id=TRACE).update(pinned=True)
        response = self.post("trace-pin", TRACE, data={"pinned": "0"})
        assert response.status_code == 200
        assert not Event.objects.filter(trace_id=TRACE, pinned=True).exists()

    def test_returns_the_pin_button_partial(self):
        response = self.post("trace-pin", TRACE, data={"pinned": "1"})
        assert "Unpin trace" in response.content.decode()

    def test_non_staff_gets_403(self):
        response = self.post("trace-pin", TRACE, data={"pinned": "1"}, user=AnonymousUser())
        assert response.status_code == 403
        assert not Event.objects.filter(trace_id=TRACE, pinned=True).exists()

    def test_unknown_trace_is_404(self):
        from django.http import Http404

        try:
            self.post("trace-pin", "f" * 32, data={"pinned": "1"})
        except Http404:
            return
        raise AssertionError("expected Http404")

    def test_empty_trace_is_404(self):
        from django.http import Http404

        try:
            self.post("trace-pin", "0" * 32, data={"pinned": "1"})
        except Http404:
            return
        raise AssertionError("expected Http404")

    def test_bad_pinned_value_is_400(self):
        response = self.post("trace-pin", TRACE, data={"pinned": "maybe"})
        assert response.status_code == 400

    def test_missing_pinned_value_is_400(self):
        response = self.post("trace-pin", TRACE, data={})
        assert response.status_code == 400

    def test_get_is_405(self):
        response = self.get("trace-pin", TRACE)
        assert response.status_code == 405

    @override_settings(
        DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_PERMISSION": "tests.test_dashboard_features._always_allowed"}
    )
    def test_csrf_is_enforced_even_without_csrfviewmiddleware(self):
        from django.test import Client

        # Test settings' MIDDLEWARE has no CsrfViewMiddleware (just
        # InspectorMiddleware): csrf_protect on the view is the only thing
        # that can reject this, proving it works standalone. The permission
        # override stands in for staff auth so no session backend is needed.
        c = Client(enforce_csrf_checks=True)
        response = c.post(reverse("inspector:trace-pin", args=[TRACE]), {"pinned": "1"})
        assert response.status_code == 403
        assert not Event.objects.filter(trace_id=TRACE, pinned=True).exists()

    @override_settings(
        DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_PERMISSION": "tests.test_dashboard_features._always_allowed"}
    )
    def test_pin_button_works_without_csrfviewmiddleware(self):
        from django.test import Client

        # The detail page must set the CSRF cookie itself: without
        # CsrfViewMiddleware nothing else would, and the button's token
        # would be rejected.
        c = Client(enforce_csrf_checks=True)
        c.get(reverse("inspector:request-detail", args=[self.event.pk]))
        token = c.cookies["csrftoken"].value
        response = c.post(
            reverse("inspector:trace-pin", args=[TRACE]), {"pinned": "1"}, HTTP_X_CSRFTOKEN=token
        )
        assert response.status_code == 200
        assert Event.objects.filter(trace_id=TRACE, pinned=True).exists()

    def test_pinned_badge_and_button_show_on_the_detail_page(self):
        body = self.body("request-detail", self.event.pk)
        assert "Pin trace" in body
        assert "inspector-badge--pinned" not in body

        Event.objects.filter(trace_id=TRACE).update(pinned=True)
        body = self.body("request-detail", self.event.pk)
        assert "Unpin trace" in body
        assert "inspector-badge--pinned" in body

    def test_pinned_badge_shows_in_the_requests_list(self):
        body = self.body("requests-list")
        assert "inspector-badge--pinned" not in body

        Event.objects.filter(trace_id=TRACE).update(pinned=True)
        body = self.body("requests-list")
        assert "inspector-badge--pinned" in body


def mail_event(trace_id=TRACE, **metadata):
    data = {
        "subject": "Welcome", "from_email": "from@example.com", "to": ["to@example.com"],
        "cc": [], "bcc": [], "reply_to": [], "text_body": "hi", "text_body_truncated": False,
        "html_body": None, "html_body_truncated": False, "attachments": [], "headers": {},
        "backend": "django.core.mail.backends.locmem.EmailBackend",
        "message_class": "django.core.mail.message.EmailMultiAlternatives",
        "status": "sent", "duration_ms": 1.0,
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace_id, event_type="mail.sent", metadata=data, duration_ms=data["duration_ms"]
    )


class TestMailPreview(DashboardClient):
    def test_preview_returns_the_html_body_with_the_exact_locked_down_csp(self):
        event = mail_event(html_body="<p>Hello <script>alert(1)</script></p>")
        response = self.get("mail-preview", event.pk)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert response.content.decode() == "<p>Hello <script>alert(1)</script></p>"
        assert response["Content-Security-Policy"] == (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
            "sandbox; frame-ancestors 'self'"
        )

    def test_preview_sets_x_frame_options_sameorigin(self):
        event = mail_event(html_body="<p>hi</p>")
        assert self.get("mail-preview", event.pk)["X-Frame-Options"] == "SAMEORIGIN"

    def test_preview_404s_without_an_html_body(self):
        from django.http import Http404

        event = mail_event(html_body=None)
        with pytest.raises(Http404):
            self.get("mail-preview", event.pk)

    def test_preview_404s_for_a_non_mail_event(self):
        from django.http import Http404

        other = request_event()
        with pytest.raises(Http404):
            self.get("mail-preview", other.pk)

    def test_preview_requires_access(self):
        event = mail_event(html_body="<p>hi</p>")
        assert self.get("mail-preview", event.pk, user=AnonymousUser()).status_code == 403


class TestMailEml(DashboardClient):
    def test_eml_round_trips_subject_recipients_and_bodies(self):
        import email

        event = mail_event(
            subject="Reset your password", from_email="noreply@example.com",
            to=["user@example.com"], text_body="Click here", html_body="<p>Click here</p>",
            attachments=[{"filename": "terms.pdf", "mimetype": "application/pdf", "size": 2048}],
        )
        response = self.get("mail-eml", event.pk)
        assert response.status_code == 200
        assert response["Content-Type"] == "message/rfc822"
        assert 'filename="reset-your-password.eml"' in response["Content-Disposition"]

        parsed = email.message_from_bytes(response.content)
        assert parsed["Subject"] == "Reset your password"
        assert parsed["To"] == "user@example.com"
        assert "terms.pdf" in parsed["X-Inspector-Omitted-Attachments"]

        text_body = html_body = None
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                text_body = part.get_payload(decode=True).decode()
            elif part.get_content_type() == "text/html":
                html_body = part.get_payload(decode=True).decode()
        assert text_body == "Click here"
        assert html_body == "<p>Click here</p>"

    def test_eml_never_includes_attachment_contents(self):
        import email

        event = mail_event(
            attachments=[{"filename": "terms.pdf", "mimetype": "application/pdf", "size": 2048}],
        )
        response = self.get("mail-eml", event.pk)
        parsed = email.message_from_bytes(response.content)
        # The attachment is only ever named in the omitted-attachments header,
        # never as a MIME part: its content was never stored to include.
        assert parsed["X-Inspector-Omitted-Attachments"] == "terms.pdf"
        assert not any(part.get_filename() == "terms.pdf" for part in parsed.walk())

    def test_eml_falls_back_to_a_mail_pk_filename_without_a_subject(self):
        event = mail_event(subject="")
        response = self.get("mail-eml", event.pk)
        assert 'filename="mail-%s.eml"' % event.pk in response["Content-Disposition"]

    def test_eml_requires_access(self):
        event = mail_event()
        assert self.get("mail-eml", event.pk, user=AnonymousUser()).status_code == 403


class TestMailListAndDetailAccess(DashboardClient):
    def test_list_requires_access(self):
        assert self.get("mail-list", user=AnonymousUser()).status_code == 403

    def test_detail_requires_access(self):
        event = mail_event()
        assert self.get("mail-detail", event.pk, user=AnonymousUser()).status_code == 403
