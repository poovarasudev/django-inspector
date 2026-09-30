"""Tests for the Mail Watcher (MAIL-01..03)."""

from email.mime.base import MIMEBase
from email.mime.text import MIMEText

import pytest
from django.core.mail import (
    EmailMessage,
    EmailMultiAlternatives,
    get_connection,
    mail_admins,
    send_mail,
    send_mass_mail,
)
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.test import TestCase, override_settings

from django_inspector import trace
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.mail import MailWatcher

LOCMEM = override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class RaisingBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise ConnectionError("smtp down")


class ZeroSendBackend(BaseEmailBackend):
    """Mimics fail_silently swallowing every message: no exception, nothing sent."""

    def send_messages(self, messages):
        return 0


def mail_events():
    return [e for e in _get_buffer() if e["event_type"] == "mail.sent"]


class TestMailWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert MailWatcher.watcher_name == "mail"

    def test_off_by_default(self):
        from django_inspector.conf import inspector_settings

        assert inspector_settings.watcher_enabled("mail") is False

    def test_install_replaces_get_connection_and_remove_restores_it(self):
        import django.core.mail as mail_module

        original = mail_module.get_connection
        watcher = MailWatcher()
        watcher.enable()
        try:
            assert mail_module.get_connection is not original
        finally:
            watcher.disable()
        assert mail_module.get_connection is original

    def test_a_second_instance_never_double_patches(self):
        import django.core.mail as mail_module

        original = mail_module.get_connection
        watcher1 = MailWatcher()
        watcher1.enable()
        try:
            patched = mail_module.get_connection
            watcher2 = MailWatcher()
            watcher2.enable()
            try:
                assert mail_module.get_connection is patched
            finally:
                watcher2.disable()
            assert mail_module.get_connection is patched
        finally:
            watcher1.disable()
        assert mail_module.get_connection is original

    @LOCMEM
    def test_disabled_watcher_is_inert_even_for_backends_created_while_it_was_on(self):
        watcher = MailWatcher()
        watcher.enable()
        connection = get_connection()  # wrapped while the watcher was enabled
        watcher.disable()

        trace_id = generate_trace_id()
        token = set_trace_id(trace_id)
        clear_buffer()
        try:
            connection.send_messages([EmailMessage("hi", "body", "from@example.com", ["to@example.com"])])
        finally:
            clear_trace_id(token)
            clear_buffer()
        assert mail_events() == []


class MailWatcherTestCase(TestCase):
    """Watcher enabled, an active trace, empty buffer."""

    def setUp(self):
        self.watcher = MailWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()


@LOCMEM
class TestSendingFunctions(MailWatcherTestCase):
    def test_send_mail_records_one_event(self):
        sent = send_mail("Hi there", "Just a note", "from@example.com", ["to@example.com"])
        assert sent == 1
        [event] = mail_events()
        meta = event["metadata"]
        assert event["event_type"] == "mail.sent"
        assert meta["subject"] == "Hi there"
        assert meta["from_email"] == "from@example.com"
        assert meta["to"] == ["to@example.com"]
        assert meta["text_body"] == "Just a note"
        assert meta["status"] == "sent"
        assert meta["backend"] == "django.core.mail.backends.locmem.EmailBackend"
        # send_mail() always builds an EmailMultiAlternatives under the hood.
        assert meta["message_class"] == "django.core.mail.message.EmailMultiAlternatives"
        assert meta["duration_ms"] >= 0
        assert meta["attachments"] == []
        assert meta["html_body"] is None
        assert meta["html_body_truncated"] is False

    def test_send_mass_mail_records_one_event_per_message(self):
        datatuple = (
            ("Subject 1", "Body 1", "from@example.com", ["one@example.com"]),
            ("Subject 2", "Body 2", "from@example.com", ["two@example.com"]),
        )
        sent = send_mass_mail(datatuple)
        assert sent == 2
        events = mail_events()
        assert len(events) == 2
        assert {e["metadata"]["subject"] for e in events} == {"Subject 1", "Subject 2"}

    def test_email_message_send(self):
        message = EmailMessage("Subject", "Body", "from@example.com", ["to@example.com"])
        sent = message.send()
        assert sent == 1
        [event] = mail_events()
        assert event["metadata"]["subject"] == "Subject"

    def test_email_multi_alternatives_with_html(self):
        message = EmailMultiAlternatives("Subject", "Text body", "from@example.com", ["to@example.com"])
        message.attach_alternative("<p>Hello</p>", "text/html")
        message.send()
        [event] = mail_events()
        meta = event["metadata"]
        assert meta["html_body"] == "<p>Hello</p>"
        assert meta["html_body_truncated"] is False
        assert meta["message_class"] == "django.core.mail.message.EmailMultiAlternatives"

    @override_settings(ADMINS=[("Admin", "admin@example.com")])
    def test_mail_admins(self):
        mail_admins("Server down", "Everything is on fire")
        [event] = mail_events()
        meta = event["metadata"]
        assert meta["subject"] == "[Django] Server down"
        assert meta["to"] == ["admin@example.com"]

    def test_sending_still_returns_the_same_count(self):
        assert send_mail("s", "b", "from@example.com", ["a@example.com", "b@example.com"]) == 1


@LOCMEM
class TestFieldsAndCorrelation(MailWatcherTestCase):
    def test_cc_bcc_reply_to_and_headers(self):
        message = EmailMessage(
            "Subject", "Body", "from@example.com", ["to@example.com"],
            cc=["cc@example.com"], bcc=["bcc@example.com"], reply_to=["reply@example.com"],
            headers={"X-Custom": "value"},
        )
        message.send()
        meta = mail_events()[0]["metadata"]
        assert meta["cc"] == ["cc@example.com"]
        assert meta["bcc"] == ["bcc@example.com"]
        assert meta["reply_to"] == ["reply@example.com"]
        assert meta["headers"] == {"X-Custom": "value"}

    def test_correlated_with_the_current_trace_id(self):
        send_mail("s", "b", "from@example.com", ["to@example.com"])
        assert mail_events()[0]["trace_id"] == self.trace_id

    def test_no_active_trace_records_nothing(self):
        clear_trace_id(self.token)
        self.token = None
        send_mail("s", "b", "from@example.com", ["to@example.com"])
        assert mail_events() == []

    def test_correlated_inside_trace(self):
        clear_trace_id(self.token)
        self.token = None
        clear_buffer()
        with trace("nightly-report") as outer_id:
            send_mail("s", "b", "from@example.com", ["to@example.com"])
        from django_inspector.storage.models import Event

        events = Event.objects.filter(trace_id=outer_id)
        assert events.filter(event_type="mail.sent").exists()

    def test_correlated_inside_a_request(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from django_inspector.middleware import InspectorMiddleware
        from django_inspector.storage.models import Event

        clear_trace_id(self.token)
        self.token = None
        clear_buffer()

        def view(request):
            send_mail("s", "b", "from@example.com", ["to@example.com"])
            return HttpResponse("ok")

        InspectorMiddleware(view)(RequestFactory().get("/x/"))
        request_event = Event.objects.get(event_type="request.completed")
        mail_event = Event.objects.get(event_type="mail.sent")
        assert mail_event.trace_id == request_event.trace_id


@LOCMEM
class TestMasking(MailWatcherTestCase):
    def test_body_is_masked(self):
        send_mail("s", "card 4111111111111111 here", "from@example.com", ["to@example.com"])
        meta = mail_events()[0]["metadata"]
        assert "4111111111111111" not in meta["text_body"]

    def test_headers_masked_by_key(self):
        message = EmailMessage(
            "s", "b", "from@example.com", ["to@example.com"], headers={"Authorization": "secret-token"}
        )
        message.send()
        meta = mail_events()[0]["metadata"]
        assert meta["headers"]["Authorization"] == "***REDACTED***"

    def test_recipients_are_not_key_masked(self):
        # "to"/"cc"/"bcc"/"from_email"/"reply_to" aren't sensitive keys, so an
        # ordinary recipient list passes straight through.
        send_mail("s", "b", "from@example.com", ["to@example.com"])
        meta = mail_events()[0]["metadata"]
        assert meta["to"] == ["to@example.com"]

    def test_a_card_shaped_recipient_is_still_caught_by_value_scanning(self):
        # Recipients skip key-based redaction, but every string — including a
        # recipient — is still scanned for a Luhn-valid card number (MASK-04).
        send_mail("s", "b", "from@example.com", ["4111111111111111@example.com"])
        meta = mail_events()[0]["metadata"]
        assert meta["to"] == ["***REDACTED***"]


@LOCMEM
class TestTruncation(MailWatcherTestCase):
    def test_text_body_truncated_past_the_cap(self):
        from django_inspector.watchers.mail import MAX_MAIL_BODY_CHARS

        text = "x" * (MAX_MAIL_BODY_CHARS + 100)
        send_mail("s", text, "from@example.com", ["to@example.com"])
        meta = mail_events()[0]["metadata"]
        assert len(meta["text_body"]) == MAX_MAIL_BODY_CHARS
        assert meta["text_body_truncated"] is True

    def test_html_body_truncated_past_the_cap(self):
        from django_inspector.watchers.mail import MAX_MAIL_BODY_CHARS

        message = EmailMultiAlternatives("s", "text", "from@example.com", ["to@example.com"])
        message.attach_alternative("y" * (MAX_MAIL_BODY_CHARS + 100), "text/html")
        message.send()
        meta = mail_events()[0]["metadata"]
        assert len(meta["html_body"]) == MAX_MAIL_BODY_CHARS
        assert meta["html_body_truncated"] is True

    def test_subject_truncated_to_the_rfc_line_limit(self):
        from django_inspector.watchers.mail import RFC5322_EMAIL_LINE_LENGTH_LIMIT

        message = EmailMessage("s" * 2000, "b", "from@example.com", ["to@example.com"])
        message.send()
        meta = mail_events()[0]["metadata"]
        assert len(meta["subject"]) == RFC5322_EMAIL_LINE_LENGTH_LIMIT


@LOCMEM
class TestAttachments(MailWatcherTestCase):
    def test_tuple_attachment(self):
        message = EmailMessage("s", "b", "from@example.com", ["to@example.com"])
        message.attach("notes.txt", "hello world", "text/plain")
        message.send()
        [attachment] = mail_events()[0]["metadata"]["attachments"]
        assert attachment["filename"] == "notes.txt"
        assert attachment["mimetype"] == "text/plain"
        assert attachment["size"] == len(b"hello world")

    def test_mimebase_attachment_never_stores_contents(self):
        mime = MIMEText("binary-ish content", "plain")
        mime.add_header("Content-Disposition", "attachment", filename="report.txt")
        message = EmailMessage("s", "b", "from@example.com", ["to@example.com"])
        message.attach(mime)
        message.send()
        [attachment] = mail_events()[0]["metadata"]["attachments"]
        assert attachment["filename"] == "report.txt"
        assert attachment["size"] is not None
        import json

        assert "binary-ish content" not in json.dumps(mail_events()[0]["metadata"])

    def test_raw_mimebase_with_no_filename(self):
        mime = MIMEBase("application", "octet-stream")
        mime.set_payload(b"raw-bytes")
        message = EmailMessage("s", "b", "from@example.com", ["to@example.com"])
        message.attach(mime)
        message.send()
        [attachment] = mail_events()[0]["metadata"]["attachments"]
        assert attachment["filename"] is None
        assert attachment["mimetype"] == "application/octet-stream"


@LOCMEM
class TestOutcomes(MailWatcherTestCase):
    def test_backend_raising_records_error_and_reraises(self):
        with override_settings(EMAIL_BACKEND="tests.test_mail_watcher.RaisingBackend"):
            with pytest.raises(ConnectionError, match="smtp down"):
                send_mail("s", "b", "from@example.com", ["to@example.com"])
        meta = mail_events()[0]["metadata"]
        assert meta["status"] == "error"
        assert meta["error"] == "ConnectionError: smtp down"

    def test_zero_sent_is_failed(self):
        with override_settings(EMAIL_BACKEND="tests.test_mail_watcher.ZeroSendBackend"):
            sent = send_mail("s", "b", "from@example.com", ["to@example.com"])
        assert sent == 0
        meta = mail_events()[0]["metadata"]
        assert meta["status"] == "failed"


class TestDisabledInspector(MailWatcherTestCase):
    @LOCMEM
    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_ENABLED": False})
    def test_mail_still_sends_with_inspector_disabled(self):
        sent = send_mail("s", "b", "from@example.com", ["to@example.com"])
        assert sent == 1
        assert mail_events() == []


@LOCMEM
class TestMetadataBuildFailure(MailWatcherTestCase):
    def test_a_failing_metadata_build_does_not_break_sending(self):
        import logging
        from unittest import mock

        import django_inspector.watchers.mail as mail_watcher_module

        with mock.patch.object(mail_watcher_module, "_build_metadata", side_effect=RuntimeError("boom")):
            with self.assertLogs("django_inspector", level=logging.WARNING):
                sent = send_mail("s", "b", "from@example.com", ["to@example.com"])
        assert sent == 1
        assert mail_events() == []


class TestUnwrappedBackend(TestCase):
    """A backend built directly (not through get_connection()) is never captured (assumption 5)."""

    def setUp(self):
        self.watcher = MailWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        clear_trace_id(self.token)
        clear_buffer()

    def test_direct_backend_instantiation_is_not_captured(self):
        backend = LocmemBackend()
        backend.send_messages([EmailMessage("s", "b", "from@example.com", ["to@example.com"])])
        assert mail_events() == []
