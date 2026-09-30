"""
Mail Watcher — records every email sent during a trace: recipients, subject,
text/HTML bodies, headers, backend, attachment names/sizes and the outcome.

Replaces django.core.mail.get_connection with a wrapper that returns the real
backend after marking its (bound) send_messages with an instance-level
wrapper. send_mail, send_mass_mail, mail_admins, mail_managers and
EmailMessage.send() all look get_connection() up at call time, so patching it
covers every one of them without touching backend classes (third-party ones
included).

Requirements: MAIL-01..03
"""

import logging
import time
from email.mime.base import MIMEBase

from django.core import mail as django_mail
from django.core.mail.message import RFC5322_EMAIL_LINE_LENGTH_LIMIT

from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms

logger = logging.getLogger("django_inspector")

MAX_MAIL_BODY_CHARS = 65536  # each of text_body/html_body, before masking

_PATCH_MARKER = "_inspector_mail_patch"


class MailWatcher(BaseWatcher):
    """
    Patches django.core.mail.get_connection so every backend it returns has
    its send_messages wrapped, recording one mail.sent event per message.

    - MAIL-01: recipients, subject, bodies, backend, attachments
    - MAIL-03: bodies masked and bounded before storage
    """

    watcher_name = "mail"

    def __init__(self):
        super().__init__()
        self._original_get_connection = None

    def install_hooks(self):
        current = django_mail.get_connection
        if getattr(current, _PATCH_MARKER, False):
            return  # another instance already patched it; never double-patch
        original = current

        def _wrapped_get_connection(*args, **kwargs):
            return _get_connection(self, original, *args, **kwargs)

        setattr(_wrapped_get_connection, _PATCH_MARKER, True)
        django_mail.get_connection = _wrapped_get_connection
        self._original_get_connection = original

    def remove_hooks(self):
        if self._original_get_connection is None:
            return  # this instance never installed the patch
        django_mail.get_connection = self._original_get_connection
        self._original_get_connection = None


def _get_connection(watcher, original, *args, **kwargs):
    backend = original(*args, **kwargs)
    try:
        _wrap_backend(watcher, backend)
    except Exception:
        from django_inspector.conf import inspector_settings

        if inspector_settings.INSPECTOR_RAISE_ERRORS:
            raise
        logger.warning(
            "inspector: failed to wrap mail backend %r", type(backend).__name__, exc_info=True
        )
    return backend


def _wrap_backend(watcher, backend):
    if "send_messages" in backend.__dict__:
        return  # this instance's send_messages is already wrapped
    original = backend.send_messages
    backend_path = _dotted(type(backend))

    def _wrapped_send_messages(messages, *args, **kwargs):
        if not watcher.is_capturing():
            return original(messages, *args, **kwargs)

        messages = list(messages)
        start = time.monotonic()
        try:
            result = original(messages, *args, **kwargs)
        except Exception as exc:
            _record_batch(
                watcher, backend_path, messages, "error", elapsed_ms(start), error=exc, protect=True
            )
            raise
        count = result or 0
        status = "sent" if count >= len(messages) else "failed"
        _record_batch(watcher, backend_path, messages, status, elapsed_ms(start))
        return result

    backend.send_messages = _wrapped_send_messages


def _record_batch(watcher, backend_path, messages, status, duration_ms, error=None, protect=False):
    """
    Record one mail.sent event per message. A failure is logged and never
    affects sending; while a send exception is already propagating
    (protect=True), it is always swallowed so an inspector failure can never
    replace the user's exception, whatever INSPECTOR_RAISE_ERRORS says.
    """
    from django_inspector.conf import inspector_settings

    try:
        for message in messages:
            metadata = _build_metadata(backend_path, message, status, duration_ms, error)
            watcher.record("mail.sent", metadata)
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS and not protect:
            raise
        logger.warning("inspector: error recording a mail.sent event", exc_info=True)


def _build_metadata(backend_path, message, status, duration_ms, error):
    text_body = message.body or ""
    html_body, html_truncated = _html_body(message)
    metadata = {
        "subject": (message.subject or "")[:RFC5322_EMAIL_LINE_LENGTH_LIMIT],
        "from_email": message.from_email,
        "to": list(message.to),
        "cc": list(message.cc),
        "bcc": list(message.bcc),
        "reply_to": list(message.reply_to),
        "text_body": text_body[:MAX_MAIL_BODY_CHARS],
        "text_body_truncated": len(text_body) > MAX_MAIL_BODY_CHARS,
        "html_body": html_body,
        "html_body_truncated": html_truncated,
        "attachments": _attachments(message),
        "headers": dict(message.extra_headers or {}),
        "backend": backend_path,
        "message_class": _dotted(type(message)),
        "status": status,
        "duration_ms": duration_ms,
    }
    if error is not None:
        metadata["error"] = "%s: %s" % (type(error).__name__, error)
    return metadata


def _html_body(message):
    """The first text/html alternative. ``alternatives`` entries may be plain
    tuples or EmailAlternative namedtuples depending on the Django version;
    both are indexable the same way."""
    for alternative in getattr(message, "alternatives", None) or []:
        content, mimetype = alternative[0], alternative[1]
        if mimetype == "text/html":
            return content[:MAX_MAIL_BODY_CHARS], len(content) > MAX_MAIL_BODY_CHARS
    return None, False


def _attachments(message):
    """Filename, MIME type and size only — contents are never stored."""
    return [_attachment_fields(a) for a in getattr(message, "attachments", None) or []]


def _attachment_fields(attachment) -> dict:
    if isinstance(attachment, MIMEBase):
        payload = attachment.get_payload(decode=True)
        return {
            "filename": attachment.get_filename(),
            "mimetype": attachment.get_content_type(),
            "size": len(payload) if payload is not None else None,
        }
    content = attachment[1] if len(attachment) > 1 else None
    return {
        "filename": attachment[0],
        "mimetype": attachment[2] if len(attachment) > 2 else None,
        "size": _content_size(content),
    }


def _content_size(content):
    if isinstance(content, (bytes, bytearray)):
        return len(content)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return None


def _dotted(cls) -> str:
    return cls.__module__ + "." + cls.__qualname__


register("mail", MailWatcher)
