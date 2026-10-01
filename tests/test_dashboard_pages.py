"""Tests for the dashboard's cache, template, log and signal pages and the request detail page."""

import logging

import pytest
from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from django_inspector.storage.columns import exception_fingerprint, sql_fingerprint
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


def log_event(level_name, message, logger_name="shop.checkout", **extra):
    data = {
        "logger": logger_name,
        "level": getattr(logging, level_name),
        "level_name": level_name,
        "message": message,
        "file": "/app/shop/checkout.py",
        "line": 42,
        "function": "place_order",
    }
    data.update(extra)
    return Event.objects.create(trace_id=TRACE, event_type="log.record", metadata=data)


def signal_event(signal, sender, receivers, **extra):
    data = {
        "signal": signal,
        "sender": sender,
        "receiver_count": len(receivers),
        "receivers": receivers,
        "duration_ms": 2.5,
        "method": "send",
    }
    data.update(extra)
    return Event.objects.create(trace_id=TRACE, event_type="signal.dispatched", metadata=data)


def mail_event(subject, to, trace_id=TRACE, **extra):
    data = {
        "subject": subject, "from_email": "shop@example.com", "to": to,
        "cc": [], "bcc": [], "reply_to": [], "text_body": "hi", "text_body_truncated": False,
        "html_body": None, "html_body_truncated": False, "attachments": [], "headers": {},
        "backend": "django.core.mail.backends.locmem.EmailBackend",
        "message_class": "django.core.mail.message.EmailMultiAlternatives",
        "status": "sent", "duration_ms": 1.0,
    }
    data.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="mail.sent", metadata=data, duration_ms=data["duration_ms"]
    )


class TestLogPages(DashboardTestCase):
    def setUp(self):
        self.warning = log_event("WARNING", "stock running low", logger_name="shop.inventory")
        self.error = log_event(
            "ERROR", "payment declined", exception_type="PaymentError",
            traceback="Traceback (most recent call last):\n  File x\nPaymentError: card declined",
        )
        self.critical = log_event("CRITICAL", "database unreachable", logger_name="db.pool")

    def test_list_shows_log_records(self):
        body = self.body("logs-list")
        for message in ("stock running low", "payment declined", "database unreachable"):
            assert message in body

    def test_filter_by_minimum_level(self):
        body = self.body("logs-list", params={"level": "40"})
        assert "payment declined" in body
        assert "database unreachable" in body
        assert "stock running low" not in body

    def test_filter_by_logger(self):
        body = self.body("logs-list", params={"logger": "SHOP"})
        assert "stock running low" in body
        assert "database unreachable" not in body

    def test_filter_by_message(self):
        body = self.body("logs-list", params={"message": "payment"})
        assert "payment declined" in body
        assert "stock running low" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("logs-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_shows_traceback_source_and_request_link(self):
        request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "POST", "path": "/checkout/", "status_code": 500},
        )
        body = self.body("log-detail", self.error.pk)
        assert "PaymentError: card declined" in body
        assert "/app/shop/checkout.py" in body
        assert "place_order" in body
        assert reverse("inspector:request-detail", args=[request_event.pk]) in body

    def test_detail_404s_for_non_log_event(self):
        other = cache_event("get", key="k", hit=True)
        with pytest.raises(Http404):
            self.get("log-detail", other.pk)

    def test_sidebar_links_to_logs(self):
        assert reverse("inspector:logs-list") in self.body("live-feed")


class TestSignalPages(DashboardTestCase):
    def setUp(self):
        self.saved = signal_event(
            "django.db.models.signals.post_save", "shop.models.Order",
            [
                {"receiver": "shop.receivers.notify_warehouse", "duration_ms": 1.2},
                {"receiver": "shop.receivers.bust_cache", "duration_ms": 0.3, "error": "KeyError: missing"},
            ],
        )
        self.deleted = signal_event(
            "django.db.models.signals.post_delete", "shop.models.Coupon",
            [{"receiver": "shop.receivers.audit", "duration_ms": 0.1}],
        )

    def test_list_shows_dispatches(self):
        body = self.body("signals-list")
        assert "shop.models.Order" in body
        assert "shop.models.Coupon" in body

    def test_filter_by_signal(self):
        body = self.body("signals-list", params={"signal": "django.db.models.signals.post_delete"})
        assert "shop.models.Coupon" in body
        assert "shop.models.Order" not in body

    def test_filter_by_sender(self):
        body = self.body("signals-list", params={"sender": "order"})
        assert "shop.models.Order" in body
        assert "shop.models.Coupon" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("signals-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_lists_receivers_in_call_order_with_errors(self):
        body = self.body("signal-detail", self.saved.pk)
        assert body.index("shop.receivers.notify_warehouse") < body.index("shop.receivers.bust_cache")
        assert "KeyError: missing" in body

    def test_detail_notes_receivers_that_did_not_run(self):
        halted = signal_event(
            "django.db.models.signals.pre_save", "shop.models.Order",
            [{"receiver": "shop.receivers.validate", "duration_ms": 0.2, "error": "ValueError: bad"}],
            receiver_count=3, error="ValueError: bad",
        )
        body = self.body("signal-detail", halted.pk)
        assert "2 receivers did not run" in body

    def test_sidebar_links_to_signals(self):
        assert reverse("inspector:signals-list") in self.body("live-feed")


class TestMailPages(DashboardTestCase):
    def setUp(self):
        self.sent = mail_event("Welcome aboard", ["alice@example.com", "bob@example.com"])
        self.failed = mail_event(
            "Weekly digest", ["carol@example.com"], status="failed",
            backend="django.core.mail.backends.smtp.EmailBackend",
        )
        self.sql = Event.objects.create(trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"})

    def test_list_shows_only_mail_events(self):
        body = self.body("mail-list")
        assert "Welcome aboard" in body
        assert "Weekly digest" in body
        assert "SELECT 1" not in body

    def test_list_shows_first_two_recipients_and_a_count(self):
        mail_event("Group send", ["a@example.com", "b@example.com", "c@example.com"])
        body = self.body("mail-list")
        assert "a@example.com, b@example.com" in body
        assert "+1 more" in body

    def test_filter_by_to(self):
        body = self.body("mail-list", params={"to": "carol"})
        assert "Weekly digest" in body
        assert "Welcome aboard" not in body

    def test_filter_by_subject(self):
        body = self.body("mail-list", params={"subject": "welcome"})
        assert "Welcome aboard" in body
        assert "Weekly digest" not in body

    def test_filter_by_status(self):
        body = self.body("mail-list", params={"status": "failed"})
        assert "Weekly digest" in body
        assert "Welcome aboard" not in body

    def test_htmx_request_returns_table_partial(self):
        body = self.body("mail-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_shows_headers_and_trace_link(self):
        body = self.body("mail-detail", self.sent.pk)
        assert "Welcome aboard" in body
        assert "alice@example.com" in body
        assert reverse("inspector:trace-detail", args=[self.sent.trace_id]) in body
        assert reverse("inspector:mail-eml", args=[self.sent.pk]) in body

    def test_detail_404s_for_non_mail_event(self):
        with pytest.raises(Http404):
            self.get("mail-detail", self.sql.pk)

    def test_detail_embeds_preview_in_a_sandboxed_iframe(self):
        with_html = mail_event("Has HTML", ["to@example.com"], html_body="<p>hi</p>")
        body = self.body("mail-detail", with_html.pk)
        assert '<iframe sandbox src="%s"' % reverse("inspector:mail-preview", args=[with_html.pk]) in body
        assert "allow-scripts" not in body
        assert "allow-same-origin" not in body

    def test_detail_without_html_has_no_iframe(self):
        body = self.body("mail-detail", self.sent.pk)
        assert "<iframe" not in body

    def test_detail_lists_attachments(self):
        with_attachment = mail_event(
            "Invoice", ["to@example.com"],
            attachments=[{"filename": "invoice.pdf", "mimetype": "application/pdf", "size": 1024}],
        )
        body = self.body("mail-detail", with_attachment.pk)
        assert "invoice.pdf" in body
        assert "1024 bytes" in body

    def test_sidebar_links_to_mail(self):
        assert reverse("inspector:mail-list") in self.body("live-feed")


class TestRequestDetailLogsAndSignals(DashboardTestCase):
    def setUp(self):
        self.request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "POST", "path": "/checkout/", "status_code": 200, "latency_ms": 20.0},
        )
        log_event("WARNING", "stock running low")
        log_event("ERROR", "payment declined")
        self.dispatch = signal_event(
            "django.db.models.signals.post_save", "shop.models.Order",
            [{"receiver": "shop.receivers.notify_warehouse", "duration_ms": 1.2}],
        )

    def test_summary_counts_logs_and_signals(self):
        import re

        body = self.body("request-detail", self.request_event.pk)
        assert re.search(r'__value">2</div>\s*<div class="inspector-summary-card__label">Log Records', body)
        assert re.search(r'__value">1</div>\s*<div class="inspector-summary-card__label">Signal Dispatches', body)

    def test_timeline_labels_log_and_signal_events(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "inspector-timeline-item--log" in body
        assert "inspector-timeline-item--signal" in body
        assert "stock running low" in body
        assert "shop.models.Order" in body

    def test_tabs_view_has_logs_and_signals_tabs(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "tabs"})
        assert "Logs (2)" in body
        assert "Signals (1)" in body
        assert reverse("inspector:signal-detail", args=[self.dispatch.pk]) in body

    def test_waterfall_colours_log_and_signal_bars(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "waterfall"})
        assert "inspector-waterfall-bar--log" in body
        assert "inspector-waterfall-bar--signal" in body


class TestRequestDetailMail(DashboardTestCase):
    def setUp(self):
        self.request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "POST", "path": "/signup/", "status_code": 200, "latency_ms": 20.0},
        )
        self.mail = mail_event("Verify your email", ["new-user@example.com"])

    def test_summary_counts_mail_sent(self):
        import re

        body = self.body("request-detail", self.request_event.pk)
        assert re.search(r'__value">1</div>\s*<div class="inspector-summary-card__label">Mail Sent', body)

    def test_timeline_labels_mail_events(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "inspector-timeline-item--mail" in body
        assert "Verify your email" in body

    def test_tabs_view_has_a_mail_tab_linking_to_mail_detail(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "tabs"})
        assert "Mail (1)" in body
        assert reverse("inspector:mail-detail", args=[self.mail.pk]) in body

    def test_waterfall_colours_mail_bars(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "waterfall"})
        assert "inspector-waterfall-bar--mail" in body


def http_event(url="https://api.example.com/x", host="api.example.com", status_code=200,
                duration_ms=15.0, trace_id=TRACE, **extra):
    data = {
        "library": "requests", "method": "GET", "url": url, "host": host,
        "status_code": status_code, "duration_ms": duration_ms, "redirects": 0,
        "response_bytes": 10, "streamed": False,
        "origin_file": "/app/services/billing.py", "origin_line": 42, "origin_function": "charge",
    }
    data.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="http.request", metadata=data,
        method=data["method"], status_code=data.get("status_code"),
        path=data["url"], duration_ms=data["duration_ms"],
    )


class TestHttpPages(DashboardTestCase):
    def setUp(self):
        self.ok = http_event(url="https://api.example.com/orders", host="api.example.com", status_code=200)
        self.failed = http_event(
            url="https://payments.example.com/charge", host="payments.example.com",
            status_code=500, error="ConnectionError: timed out",
        )
        self.sql = Event.objects.create(trace_id=TRACE, event_type="sql.query", metadata={"sql": "SELECT 1"})

    def test_list_shows_only_http_events(self):
        body = self.body("http-list")
        assert "api.example.com" in body
        assert "payments.example.com" in body
        assert "SELECT 1" not in body

    def test_filter_by_host(self):
        body = self.body("http-list", params={"host": "payments"})
        assert "payments.example.com" in body
        assert "api.example.com" not in body

    def test_filter_by_status_class(self):
        body = self.body("http-list", params={"status": "5xx"})
        assert "payments.example.com" in body
        assert "api.example.com" not in body

    def test_filter_by_errors_only(self):
        body = self.body("http-list", params={"errors": "on"})
        assert "payments.example.com" in body
        assert "api.example.com" not in body

    def test_host_filter_uses_no_json_operator(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection):
            self.get("http-list", params={"host": "payments"})
        sql = " ".join(q["sql"] for q in connection.queries).lower()
        assert "json_extract" not in sql
        assert "->>" not in sql

    def test_htmx_request_returns_table_partial(self):
        body = self.body("http-list", htmx=True)
        assert "<table" in body
        assert "inspector-sidebar" not in body

    def test_detail_shows_fields_and_request_link(self):
        request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "POST", "path": "/checkout/", "status_code": 200},
        )
        body = self.body("http-detail", self.ok.pk)
        assert "api.example.com" in body
        assert "/app/services/billing.py" in body
        assert reverse("inspector:request-detail", args=[request_event.pk]) in body

    def test_detail_404s_for_non_http_event(self):
        with pytest.raises(Http404):
            self.get("http-detail", self.sql.pk)

    def test_sidebar_links_to_http(self):
        assert reverse("inspector:http-list") in self.body("live-feed")


class TestRequestDetailHttp(DashboardTestCase):
    def setUp(self):
        self.request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "POST", "path": "/checkout/", "status_code": 200, "latency_ms": 20.0},
        )
        self.http = http_event(url="https://payments.example.com/charge", host="payments.example.com")

    def test_summary_counts_http_calls(self):
        import re

        body = self.body("request-detail", self.request_event.pk)
        assert re.search(r'__value">1</div>\s*<div class="inspector-summary-card__label">HTTP Calls', body)

    def test_timeline_labels_http_events(self):
        body = self.body("request-detail", self.request_event.pk)
        assert "inspector-timeline-item--http" in body
        assert "payments.example.com" in body

    def test_tabs_view_has_an_http_tab_linking_to_http_detail(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "tabs"})
        assert "HTTP (1)" in body
        assert reverse("inspector:http-detail", args=[self.http.pk]) in body

    def test_waterfall_colours_http_bars(self):
        body = self.body("request-detail", self.request_event.pk, params={"view": "waterfall"})
        assert "inspector-waterfall-bar--http" in body


class TestSidebarVersion(DashboardTestCase):
    def test_sidebar_shows_the_package_version(self):
        from django_inspector import __version__

        assert __version__ == "0.2.0"
        assert "v0.2.0" in self.body("live-feed")


class TestWaterfallOffsets(TestCase):
    """Events keep their capture time, so offsets now mean something."""

    def test_offsets_start_at_the_request_and_subtract_the_duration(self):
        import datetime

        from django.utils import timezone

        from django_inspector.dashboard.views import _waterfall

        start = timezone.now()
        request_event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"latency_ms": 100}, timestamp=start,
        )
        # A 20 ms query recorded when it finished, 50 ms into the request.
        query = Event.objects.create(
            trace_id=TRACE, event_type="sql.query", metadata={"duration_ms": 20},
            timestamp=start + datetime.timedelta(milliseconds=50),
        )
        rows = {row["event"].pk: row for row in _waterfall([request_event, query], request_event)}
        assert rows[request_event.pk]["offset_ms"] == 0
        assert rows[request_event.pk]["width_pct"] == 100
        assert rows[query.pk]["offset_ms"] == 30
        assert rows[query.pk]["offset_pct"] == 30
        assert rows[query.pk]["width_pct"] == 20


class TestDroppedEventsNotice(DashboardTestCase):
    def test_request_detail_says_how_many_events_were_dropped(self):
        event = Event.objects.create(
            trace_id=TRACE, event_type="request.completed",
            metadata={"method": "GET", "path": "/x/", "status_code": 200, "events_dropped": 7},
        )
        body = self.body("request-detail", event.pk)
        assert "7 more events happened in this request" in body


def issue_exception_event(trace_id=TRACE, **extra):
    frames = [{"file": "/app/orders/views.py", "line": 12, "function": "checkout"}]
    metadata = {
        "exception_type": "app.errors.PaymentFailed",
        "exception_message": "card declined",
        "frames": frames,
    }
    metadata.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="exception.raised", metadata=metadata,
        fingerprint=exception_fingerprint(metadata["exception_type"], frames),
    )


def issue_sql_event(trace_id=TRACE, **extra):
    metadata = {
        "sql": "SELECT * FROM orders WHERE id = 1", "db_alias": "default",
        "duration_ms": 12.0, "origin_file": "/app/orders/views.py", "origin_line": 20,
    }
    metadata.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="sql.query", metadata=metadata,
        fingerprint=sql_fingerprint(metadata["sql"], metadata["db_alias"]),
        duration_ms=metadata["duration_ms"],
    )


def issue_request_event(trace_id=TRACE, **extra):
    metadata = {
        "method": "GET", "path": "/orders/1/", "route": "orders/<int:pk>/",
        "status_code": 200, "latency_ms": 25.0,
    }
    metadata.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="request.completed", metadata=metadata,
        method=metadata["method"], path=metadata["path"], status_code=metadata["status_code"],
        duration_ms=metadata["latency_ms"],
    )


class TestIssuesPage(DashboardTestCase):
    def setUp(self):
        self.exc = issue_exception_event()
        self.sql = issue_sql_event()
        self.req = issue_request_event()

    def test_renders_exceptions_queries_and_endpoints(self):
        body = self.body("issues")
        assert "app.errors.PaymentFailed" in body
        assert "card declined" in body
        assert "orders" in body
        assert "orders/&lt;int:pk&gt;/" in body
        assert reverse("inspector:trace-detail", args=[TRACE]) in body

    def test_empty_states(self):
        Event.objects.all().delete()
        body = self.body("issues")
        assert "No exceptions in this window" in body
        assert "No query shapes in this window" in body
        assert "No endpoints in this window" in body

    def test_window_selector_links(self):
        body = self.body("issues")
        assert 'href="?window=1h"' in body
        assert 'href="?window=7d"' in body

    def test_bad_window_falls_back_to_24h(self):
        body = self.body("issues", params={"window": "bogus"})
        assert (
            'href="?window=24h" class="inspector-view-switcher__link '
            'inspector-view-switcher__link--active"' in body
        )

    def test_window_param_selects_the_active_window(self):
        body = self.body("issues", params={"window": "1h"})
        assert (
            'href="?window=1h" class="inspector-view-switcher__link '
            'inspector-view-switcher__link--active"' in body
        )

    def test_scan_cap_notice_shown_when_hit(self):
        from django_inspector.dashboard import issues

        for i in range(5):
            issue_exception_event(trace_id=str(i) * 32)
        original = issues.ISSUES_SCAN_LIMIT
        issues.ISSUES_SCAN_LIMIT = 2
        try:
            body = self.body("issues")
        finally:
            issues.ISSUES_SCAN_LIMIT = original
        assert "Based on the latest 2" in body

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.5})
    def test_sampling_notice_shown_when_rate_below_one(self):
        body = self.body("issues")
        assert "sampl" in body.lower()

    def test_sampling_notice_hidden_at_full_rate(self):
        body = self.body("issues")
        assert "relative, not totals" not in body

    def test_sidebar_lists_issues_first(self):
        body = self.body("live-feed")
        issues_href = 'href="%s"' % reverse("inspector:issues")
        live_href = 'href="%s"' % reverse("inspector:live-feed")
        assert body.index(issues_href) < body.index(live_href)


class TestIssuesPageSlowHosts(DashboardTestCase):
    def test_renders_slow_hosts(self):
        http_event(url="https://payments.example.com/charge", host="payments.example.com", duration_ms=250.0)
        body = self.body("issues")
        assert "Slow hosts" in body
        assert "payments.example.com" in body

    def test_empty_state(self):
        body = self.body("issues")
        assert "No slow hosts in this window" in body
