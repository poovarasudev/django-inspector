"""
Request Watcher — captures HTTP request/response metadata for every
non-inspector request and stores it as a trace-correlated event.

Requirements: REQ-01..REQ-06
"""

import json
import logging
import time
from typing import Optional, Tuple
from urllib.parse import urlencode

from django.http import QueryDict
from django.utils import timezone

from django_inspector.client_ip import get_client_ip
from django_inspector.conf import inspector_settings
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")


class RequestWatcher(BaseWatcher):
    """
    Captures request metadata:
    - HTTP method, path, full URL, query parameters (REQ-01)
    - Request/response headers (REQ-02)
    - Request/response body with size truncation (REQ-03)
    - Status code and response latency (REQ-04)
    - Authenticated user, session ID, client IP (REQ-05)
    - Excludes inspector's own requests (REQ-06; the middleware skips dashboard paths)
    """

    watcher_name = "request"

    def install_hooks(self):
        """No-op — request recording is driven by middleware process_request/process_response."""
        pass

    def remove_hooks(self):
        """No-op — paired with install_hooks."""
        pass

    def on_request(self, request):
        """Called by middleware at start of request. Stores start time."""
        request._inspector_start_time = time.monotonic()
        request._inspector_started_at = timezone.now()

    def on_response(self, request, response):
        """
        Called by middleware after response is generated.
        Records the full request/response event.
        """
        latency_ms = None
        start_time = getattr(request, "_inspector_start_time", None)
        if start_time is not None:
            latency_ms = round((time.monotonic() - start_time) * 1000, 2)

        max_body = inspector_settings.MAX_BODY_SIZE

        # REQ-01: method, path, full URL, query parameters
        metadata = {
            "method": request.method,
            "path": request.path,
            "full_url": _full_url(request),
            "query_params": dict(request.GET),
        }

        # REQ-02: request and response headers
        metadata["request_headers"] = _extract_headers(request.META)
        metadata["response_headers"] = dict(response.items()) if response else {}

        # REQ-03: request and response body (masked, then truncated)
        _add_body(metadata, "request", _request_body(request), max_body)
        _add_body(metadata, "response", _response_body(response), max_body)

        # REQ-04: status code and latency
        metadata["status_code"] = getattr(response, "status_code", None)
        metadata["latency_ms"] = latency_ms

        # REQ-05: user, session, IP
        metadata["user"] = _get_user_info(request)
        metadata["session_id"] = _get_session_id(request)
        metadata["client_ip"] = get_client_ip(request)

        from django_inspector.storage.flush import dropped_count

        dropped = dropped_count()
        if dropped:
            metadata["events_dropped"] = dropped

        # Stamped with the request's start so it precedes its trace's events.
        self.record(
            "request.completed", metadata,
            timestamp=getattr(request, "_inspector_started_at", None),
        )


def _extract_headers(meta: dict) -> dict:
    """Extract HTTP headers from request.META (HTTP_* keys)."""
    headers = {}
    for key, value in meta.items():
        if key.startswith("HTTP_"):
            header_name = key[5:].replace("_", "-").title()
            headers[header_name] = value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            header_name = key.replace("_", "-").title()
            headers[header_name] = value
    return headers


# Bodies are masked before they are serialised: key-based masking can't see
# inside a raw body string, so form and JSON bodies are parsed first.
_TEXT_SUBTYPES = ("json", "xml", "javascript", "graphql", "yaml", "csv")


def _full_url(request) -> str:
    """The absolute URL with sensitive query parameters masked."""
    from django_inspector.masking import mask_value

    try:
        url = request.build_absolute_uri(request.path)
    except Exception:
        # DisallowedHost: the host isn't in ALLOWED_HOSTS. Keep the request event.
        url = request.path
    if not request.GET:
        return url
    params = mask_value({key: request.GET.getlist(key) for key in request.GET})
    return url + "?" + urlencode(params, doseq=True, safe="*")


def _request_body(request) -> Tuple[str, object]:
    """(format, payload) for the request body; see _add_body."""
    sensitive = getattr(request, "sensitive_post_parameters", None)
    if sensitive == "__ALL__":
        return "omitted", "[omitted: sensitive_post_parameters]"
    extra_keys = list(sensitive or [])
    content_type = (getattr(request, "content_type", None) or "").lower()
    if content_type.startswith("multipart/"):
        return _multipart_payload(request, extra_keys)
    try:
        raw = request.body
    except Exception:
        logger.debug("inspector: could not read request body", exc_info=True)
        return "unreadable", "[request body not readable]"
    return _payload(raw, content_type, extra_keys)


def _response_body(response) -> Tuple[str, object]:
    """(format, payload) for the response body; streaming bodies are never consumed."""
    if response is None:
        return "empty", ""
    if getattr(response, "streaming", False):
        return "streaming", "[streaming response not captured]"
    try:
        raw = response.content
    except Exception:
        logger.debug("inspector: could not read response body", exc_info=True)
        return "unreadable", "[response body not readable]"
    content_type = (response.get("Content-Type") or "").lower()
    return _payload(raw, content_type, [])


def _multipart_payload(request, extra_keys) -> Tuple[str, object]:
    """
    Form fields and file summaries, but only when the view already parsed the
    body: parsing it here would read (and maybe spool) every upload.
    """
    if not hasattr(request, "_post"):
        return "multipart", "[multipart body not parsed by the view; not captured]"
    fields = {key: _single_or_list(request.POST.getlist(key)) for key in request.POST}
    files = {
        key: ["%s (%s bytes)" % (f.name, f.size) for f in request.FILES.getlist(key)]
        for key in request.FILES
    }
    return "multipart", _masked({"fields": fields, "files": files}, extra_keys)


def _payload(raw, content_type, extra_keys) -> Tuple[str, object]:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not raw:
        return "empty", ""
    mime = content_type.split(";")[0].strip()
    if mime == "application/x-www-form-urlencoded":
        form = QueryDict(raw)
        return "form", _masked({key: _single_or_list(form.getlist(key)) for key in form}, extra_keys)
    if mime == "application/json" or mime.endswith("+json"):
        try:
            data = json.loads(raw)
        except ValueError:
            return "unparseable", "[unparseable JSON body: %d bytes]" % len(raw)
        return "json", _masked(data, extra_keys)
    if mime.startswith("text/") or any(sub in mime for sub in _TEXT_SUBTYPES):
        return "text", raw.decode("utf-8", errors="replace")
    if not mime:
        try:
            return "text", raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return "binary", "[binary body: %d bytes, %s]" % (len(raw), mime or "no content type")


def _masked(data, extra_keys):
    """Mask ``data``, then serialise it: truncating afterwards can't expose a secret."""
    from django_inspector.masking import mask_value

    return json.dumps(mask_value(data, extra_keys), ensure_ascii=False, default=str)


def _single_or_list(values):
    return values[0] if len(values) == 1 else values


def _add_body(metadata, prefix, body, max_size):
    """Store ``<prefix>_body`` truncated to max_size bytes, with its format."""
    body_format, text = body
    encoded = text.encode("utf-8")
    if len(encoded) > max_size:
        text = encoded[:max_size].decode("utf-8", errors="ignore")
        metadata[prefix + "_body_truncated"] = True
    metadata[prefix + "_body"] = text
    metadata[prefix + "_body_format"] = body_format


def _get_user_info(request) -> Optional[str]:
    """Extract user identifier from request."""
    user = getattr(request, "user", None)
    if user is None:
        return None
    if hasattr(user, "is_authenticated"):
        try:
            if not user.is_authenticated:
                return None
        except Exception:
            return None
    return str(getattr(user, "pk", None) or getattr(user, "username", str(user)))


def _get_session_id(request) -> Optional[str]:
    """Extract session key from request if available."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return getattr(session, "session_key", None)


# Auto-register on import
register("request", RequestWatcher)
