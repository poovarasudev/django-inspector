"""
Request Watcher — captures HTTP request/response metadata for every
non-inspector request and stores it as a trace-correlated event.

Requirements: REQ-01..REQ-06
"""

import time
import logging

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
    - Excludes inspector's own requests (REQ-06)
    """

    watcher_name = "request"

    def install_hooks(self):
        """No-op — request recording is driven by middleware process_request/process_response."""
        pass

    def remove_hooks(self):
        """No-op — paired with install_hooks."""
        pass

    def should_ignore_request(self, request) -> bool:
        """Return True if this request should not be recorded (REQ-06)."""
        path = getattr(request, "path", "")
        prefix = inspector_settings.DASHBOARD_URL_PREFIX
        # Normalize: ensure both have leading slash for comparison
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return path.startswith(prefix)

    def on_request(self, request):
        """Called by middleware at start of request. Stores start time."""
        request._inspector_start_time = time.monotonic()

    def on_response(self, request, response):
        """
        Called by middleware after response is generated.
        Records the full request/response event.
        """
        if self.should_ignore_request(request):
            return

        latency_ms = None
        start_time = getattr(request, "_inspector_start_time", None)
        if start_time is not None:
            latency_ms = round((time.monotonic() - start_time) * 1000, 2)

        max_body = inspector_settings.MAX_BODY_SIZE

        # REQ-01: method, path, full URL, query parameters
        metadata = {
            "method": request.method,
            "path": request.path,
            "full_url": request.build_absolute_uri(),
            "query_params": dict(request.GET),
        }

        # REQ-02: request and response headers
        metadata["request_headers"] = _extract_headers(request.META)
        metadata["response_headers"] = dict(response.items()) if response else {}

        # REQ-03: request and response body (truncated)
        metadata["request_body"] = _safe_body(request, max_body)
        metadata["response_body"] = _safe_response_body(response, max_body)

        # REQ-04: status code and latency
        metadata["status_code"] = getattr(response, "status_code", None)
        metadata["latency_ms"] = latency_ms

        # REQ-05: user, session, IP
        metadata["user"] = _get_user_info(request)
        metadata["session_id"] = _get_session_id(request)
        metadata["client_ip"] = _get_client_ip(request)

        self.record("request.completed", metadata)


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


def _safe_body(request, max_size: int) -> str:
    """Read request body, truncate to max_size bytes."""
    try:
        body = request.body
        if isinstance(body, bytes):
            body = body[:max_size].decode("utf-8", errors="replace")
        else:
            body = str(body)[:max_size]
        return body
    except Exception:
        return ""


def _safe_response_body(response, max_size: int) -> str:
    """Read response content, truncate to max_size bytes."""
    try:
        if not hasattr(response, "content"):
            return ""
        content = response.content
        if isinstance(content, bytes):
            content = content[:max_size].decode("utf-8", errors="replace")
        else:
            content = str(content)[:max_size]
        return content
    except Exception:
        return ""


def _get_user_info(request) -> str | None:
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


def _get_session_id(request) -> str | None:
    """Extract session key from request if available."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return getattr(session, "session_key", None)


def _get_client_ip(request) -> str:
    """Extract client IP, checking X-Forwarded-For first."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


# Auto-register on import
register("request", RequestWatcher)
