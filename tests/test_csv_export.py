"""Tests for CSV export of dashboard lists (DASH-03)."""

import csv
import io

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from django_inspector.storage.columns import exception_fingerprint, sql_fingerprint
from django_inspector.storage.models import Event

TRACE = "c" * 32


def _as_dicts(rows):
    header, *body = rows
    return [dict(zip(header, r)) for r in body]


class CsvClient(TestCase):
    def get(self, url_name, *args, params=None, user=None):
        path = reverse("inspector:" + url_name, args=args)
        request = RequestFactory().get(path, params or {})
        request.user = user or User(username="staff", is_staff=True, is_active=True)
        match = resolve(path)
        return match.func(request, *match.args, **match.kwargs)

    def csv(self, url_name, *args, params=None, user=None):
        """(rows, response) for a ?format=csv request; rows includes the header row."""
        params = dict(params or {})
        params["format"] = "csv"
        response = self.get(url_name, *args, params=params, user=user)
        assert response.status_code == 200, response.status_code
        content = b"".join(response.streaming_content).decode()
        return list(csv.reader(io.StringIO(content))), response


def request_event(trace=TRACE, **metadata):
    data = {"method": "GET", "path": "/x/", "status_code": 200, "latency_ms": 5.0, "user": "42"}
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="request.completed", metadata=data,
        method=data["method"], path=data["path"], status_code=data["status_code"],
        duration_ms=data["latency_ms"],
    )


def sql_event(trace=TRACE, **metadata):
    data = {
        "sql": "SELECT 1", "db_alias": "default", "duration_ms": 3.0,
        "is_slow": False, "n_plus_one": False, "is_duplicate": False,
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="sql.query", metadata=data,
        fingerprint=sql_fingerprint(data["sql"], data["db_alias"]), duration_ms=data["duration_ms"],
    )


def exception_event(trace=TRACE, **metadata):
    frames = [{"file": "/app/views.py", "line": 1, "function": "view"}]
    data = {
        "exception_type": "app.errors.Boom", "exception_message": "boom",
        "request_path": "/x/", "frames": frames,
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="exception.raised", metadata=data,
        fingerprint=exception_fingerprint(data["exception_type"], frames),
    )


def cache_event(trace=TRACE, **metadata):
    data = {
        "operation": "get", "key": "k", "hit": True, "backend": "locmem",
        "duration_ms": 0.1, "alias": "default",
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="cache." + data["operation"], metadata=data,
        duration_ms=data["duration_ms"],
    )


def template_event(trace=TRACE, **metadata):
    data = {"name": "shop/page.html", "origin_path": "/app/templates/shop/page.html", "duration_ms": 1.0}
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="template.rendered", metadata=data, duration_ms=data["duration_ms"]
    )


def log_event(trace=TRACE, **metadata):
    data = {"level": 30, "level_name": "WARNING", "logger": "shop", "message": "low stock"}
    data.update(metadata)
    return Event.objects.create(trace_id=trace, event_type="log.record", metadata=data)


def signal_event(trace=TRACE, **metadata):
    data = {
        "signal": "django.db.models.signals.post_save", "sender": "shop.Order",
        "receiver_count": 2, "duration_ms": 1.0,
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="signal.dispatched", metadata=data, duration_ms=data["duration_ms"]
    )


def trace_completed_event(trace=TRACE, **metadata):
    data = {"name": "nightly-report", "status": "ok", "duration_ms": 5.0}
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="trace.completed", metadata=data, duration_ms=data["duration_ms"]
    )


def mail_event(trace=TRACE, **metadata):
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
        trace_id=trace, event_type="mail.sent", metadata=data, duration_ms=data["duration_ms"]
    )


def http_event(trace=TRACE, **metadata):
    data = {
        "library": "requests", "method": "GET", "url": "https://api.example.com/x",
        "host": "api.example.com", "status_code": 200, "duration_ms": 15.0,
        "redirects": 0, "response_bytes": 10, "streamed": False,
    }
    data.update(metadata)
    return Event.objects.create(
        trace_id=trace, event_type="http.request", metadata=data,
        method=data["method"], status_code=data.get("status_code"),
        path=data["url"], duration_ms=data["duration_ms"],
    )


class TestRequestsCsv(CsvClient):
    def test_header_content_type_and_filename(self):
        request_event(path="/orders/")
        rows, response = self.csv("requests-list")
        assert response["Content-Type"] == "text/csv"
        assert 'filename="requests.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms", "method", "path", "status_code", "user",
        ]

    def test_row_values(self):
        request_event(path="/orders/", method="POST", status_code=201, latency_ms=8.0, user="7")
        row = _as_dicts(self.csv("requests-list")[0])[0]
        assert row["trace_id"] == TRACE
        assert row["event_type"] == "request.completed"
        assert row["duration_ms"] == "8.0"
        assert row["method"] == "POST"
        assert row["path"] == "/orders/"
        assert row["status_code"] == "201"
        assert row["user"] == "7"

    def test_honours_filters(self):
        request_event(trace="1" * 32, method="GET", path="/get/")
        request_event(trace="2" * 32, method="POST", path="/post/")
        rows, _ = self.csv("requests-list", params={"method": "POST"})
        assert len(rows) == 2  # header + 1 matching row
        assert rows[1][4] == "POST"

    def test_requires_access(self):
        response = self.get("requests-list", params={"format": "csv"}, user=AnonymousUser())
        assert response.status_code == 403


class TestQueriesCsv(CsvClient):
    def test_header_and_row(self):
        sql_event(sql="SELECT * FROM orders", is_slow=True, n_plus_one=True)
        rows, response = self.csv("queries-list")
        assert response["Content-Type"] == "text/csv"
        assert 'filename="queries.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms",
            "sql", "db_alias", "is_slow", "n_plus_one", "is_duplicate", "fingerprint",
        ]
        row = _as_dicts(rows)[0]
        assert row["sql"] == "SELECT * FROM orders"
        assert row["is_slow"] == "True"
        assert row["fingerprint"] == sql_fingerprint("SELECT * FROM orders", "default")


class TestExceptionsCsv(CsvClient):
    def test_header_and_row(self):
        exception_event(exception_message="card declined")
        rows, response = self.csv("exceptions-list")
        assert 'filename="exceptions.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms",
            "exception_type", "exception_message", "path", "fingerprint",
        ]
        row = _as_dicts(rows)[0]
        assert row["exception_type"] == "app.errors.Boom"
        assert row["exception_message"] == "card declined"
        assert row["path"] == "/x/"

    def test_masked_values_stay_masked(self):
        exception_event(exception_message="***REDACTED***")
        row = _as_dicts(self.csv("exceptions-list")[0])[0]
        assert row["exception_message"] == "***REDACTED***"


class TestCacheCsv(CsvClient):
    def test_header_and_row(self):
        cache_event(key="profile:1", hit=True, backend="locmem")
        rows, response = self.csv("cache-list")
        assert 'filename="cache.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms", "operation", "key", "hit", "backend",
        ]
        row = _as_dicts(rows)[0]
        assert row["operation"] == "get"
        assert row["key"] == "profile:1"
        assert row["hit"] == "True"
        assert row["backend"] == "locmem"


class TestTemplatesCsv(CsvClient):
    def test_header_and_row(self):
        template_event(name="shop/page.html")
        rows, response = self.csv("templates-list")
        assert 'filename="templates.csv"' in response["Content-Disposition"]
        assert rows[0] == ["timestamp", "trace_id", "event_type", "duration_ms", "name", "path"]
        row = _as_dicts(rows)[0]
        assert row["name"] == "shop/page.html"
        assert row["path"] == "/app/templates/shop/page.html"


class TestLogsCsv(CsvClient):
    def test_header_and_row(self):
        log_event(message="stock running low")
        rows, response = self.csv("logs-list")
        assert 'filename="logs.csv"' in response["Content-Disposition"]
        assert rows[0] == ["timestamp", "trace_id", "event_type", "duration_ms", "level", "logger", "message"]
        row = _as_dicts(rows)[0]
        assert row["level"] == "WARNING"
        assert row["logger"] == "shop"
        assert row["message"] == "stock running low"


class TestSignalsCsv(CsvClient):
    def test_header_and_row(self):
        signal_event(sender="shop.Order", receiver_count=3)
        rows, response = self.csv("signals-list")
        assert 'filename="signals.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms", "signal", "sender", "receiver_count",
        ]
        row = _as_dicts(rows)[0]
        assert row["sender"] == "shop.Order"
        assert row["receiver_count"] == "3"


class TestTracesCsv(CsvClient):
    def test_header_and_row(self):
        trace_completed_event(name="nightly-report", status="ok")
        rows, response = self.csv("traces-list")
        assert 'filename="traces.csv"' in response["Content-Disposition"]
        assert rows[0] == ["timestamp", "trace_id", "event_type", "duration_ms", "name", "status"]
        row = _as_dicts(rows)[0]
        assert row["name"] == "nightly-report"
        assert row["status"] == "ok"


class TestMailCsv(CsvClient):
    def test_header_and_row(self):
        mail_event(subject="Reset your password", to=["alice@example.com"], status="failed")
        rows, response = self.csv("mail-list")
        assert 'filename="mail.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms", "subject", "to", "status", "backend",
        ]
        row = _as_dicts(rows)[0]
        assert row["subject"] == "Reset your password"
        assert row["to"] == "['alice@example.com']"
        assert row["status"] == "failed"
        assert row["backend"] == "django.core.mail.backends.locmem.EmailBackend"


class TestHttpCsv(CsvClient):
    def test_header_and_row(self):
        http_event(host="payments.example.com", url="https://payments.example.com/charge", status_code=500,
                   error="ConnectionError: timed out")
        rows, response = self.csv("http-list")
        assert 'filename="http.csv"' in response["Content-Disposition"]
        assert rows[0] == [
            "timestamp", "trace_id", "event_type", "duration_ms",
            "library", "method", "url", "host", "status_code", "error",
        ]
        row = _as_dicts(rows)[0]
        assert row["host"] == "payments.example.com"
        assert row["status_code"] == "500"
        assert row["error"] == "ConnectionError: timed out"


class TestCsvBehaviour(CsvClient):
    """Behaviour shared by every list's export, exercised once via requests."""

    def test_row_cap(self):
        from django_inspector.dashboard import export

        for i in range(5):
            request_event(trace=str(i) * 32)
        original = export.CSV_MAX_ROWS
        export.CSV_MAX_ROWS = 2
        try:
            rows, _ = self.csv("requests-list")
        finally:
            export.CSV_MAX_ROWS = original
        assert len(rows) == 3  # header + 2 rows

    def test_formula_injection_is_neutralised(self):
        request_event(path="=cmd|' /C calc'!A1")
        row = _as_dicts(self.csv("requests-list")[0])[0]
        assert row["path"] == "'=cmd|' /C calc'!A1"

    def test_plus_minus_at_tab_and_cr_prefixes_are_neutralised(self):
        for prefix in ("+", "-", "@", "\t", "\r"):
            Event.objects.all().delete()
            request_event(path=prefix + "1+1")
            row = _as_dicts(self.csv("requests-list")[0])[0]
            assert row["path"] == "'" + prefix + "1+1"

    def test_ordinary_values_are_not_prefixed(self):
        request_event(path="/orders/")
        row = _as_dicts(self.csv("requests-list")[0])[0]
        assert row["path"] == "/orders/"


class TestExportLinksOnListPages(CsvClient):
    def body(self, *args, **kwargs):
        response = self.get(*args, **kwargs)
        assert response.status_code == 200
        return response.content.decode()

    def test_requests_list_export_link_keeps_current_filters(self):
        body = self.body("requests-list", params={"method": "GET"})
        assert "Export CSV" in body
        assert "method=GET" in body
        assert "format=csv" in body

    def test_queries_list_has_an_export_link(self):
        assert "Export CSV" in self.body("queries-list")

    def test_exceptions_list_has_an_export_link(self):
        assert "Export CSV" in self.body("exceptions-list")

    def test_cache_list_has_an_export_link(self):
        assert "Export CSV" in self.body("cache-list")

    def test_templates_list_has_an_export_link(self):
        assert "Export CSV" in self.body("templates-list")

    def test_logs_list_has_an_export_link(self):
        assert "Export CSV" in self.body("logs-list")

    def test_signals_list_has_an_export_link(self):
        assert "Export CSV" in self.body("signals-list")

    def test_traces_list_has_an_export_link(self):
        assert "Export CSV" in self.body("traces-list")

    def test_mail_list_has_an_export_link(self):
        assert "Export CSV" in self.body("mail-list")

    def test_http_list_has_an_export_link(self):
        assert "Export CSV" in self.body("http-list")
