"""
HTTP Client Watcher — records outgoing requests/httpx calls made during a
trace: method, masked URL, host, status, duration, redirect count, response
size, streaming and error. Neither library becomes a dependency: each is
imported lazily, and a missing one simply isn't hooked — the watcher still
enables for whichever one is installed.

Patches requests.Session.send and httpx.Client.send / httpx.AsyncClient.send
on the class, so every session and every get/post/stream/request call on
either library — including the requests.get()-style module helpers, which
build a Session internally — goes through one hook per library.

Requirements: HTTP-01..03
"""

import logging
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

from django_inspector.masking import mask_url, mask_value
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms, extract_origin

logger = logging.getLogger("django_inspector")

MAX_URL_LENGTH = 2048  # characters of the masked URL stored per call

_PATCH_MARKER = "_inspector_http_patch"

# requests follows redirects by calling Session.send again for each hop.
# Only the outermost call is the user's call: inner hops pass straight
# through, and the outer event counts them in `redirects`.
_in_requests_send: ContextVar[bool] = ContextVar("inspector_in_requests_send", default=False)


class HTTPClientWatcher(BaseWatcher):
    """
    - HTTP-01: method, masked URL, host, status, duration, redirects, error
    - HTTP-02: requests/httpx imported lazily; a no-op when neither is installed
    """

    watcher_name = "http"

    def __init__(self):
        super().__init__()
        self._original_requests_send = None
        self._original_httpx_send = None
        self._original_httpx_async_send = None

    def install_hooks(self):
        self._install_requests()
        self._install_httpx()

    def remove_hooks(self):
        if self._original_requests_send is not None:
            import requests

            requests.Session.send = self._original_requests_send  # type: ignore[method-assign]
            self._original_requests_send = None
        if self._original_httpx_send is not None:
            import httpx

            httpx.Client.send = self._original_httpx_send  # type: ignore[method-assign]
            self._original_httpx_send = None
        if self._original_httpx_async_send is not None:
            import httpx

            httpx.AsyncClient.send = self._original_httpx_async_send  # type: ignore[method-assign]
            self._original_httpx_async_send = None

    def _install_requests(self):
        try:
            import requests
        except ImportError:
            logger.debug("django-inspector: requests is not installed; the http watcher skips it")
            return

        current = requests.Session.__dict__.get("send")
        if getattr(current, _PATCH_MARKER, False):
            return  # another instance already patched it; never double-patch
        original = requests.Session.send

        def _traced_send(session_self, request, **kwargs):
            if _in_requests_send.get() or not self.is_capturing():
                return original(session_self, request, **kwargs)
            token = _in_requests_send.set(True)
            try:
                return _sync_call(self, "requests", original, session_self, request, kwargs)
            finally:
                _in_requests_send.reset(token)

        setattr(_traced_send, _PATCH_MARKER, True)
        requests.Session.send = _traced_send  # type: ignore[method-assign, assignment]
        self._original_requests_send = original

    def _install_httpx(self):
        try:
            import httpx
        except ImportError:
            logger.debug("django-inspector: httpx is not installed; the http watcher skips it")
            return
        self._install_httpx_sync(httpx)
        self._install_httpx_async(httpx)

    def _install_httpx_sync(self, httpx):
        current = httpx.Client.__dict__.get("send")
        if getattr(current, _PATCH_MARKER, False):
            return
        original = httpx.Client.send

        def _traced_send(client_self, request, **kwargs):
            if not self.is_capturing():
                return original(client_self, request, **kwargs)
            return _sync_call(self, "httpx", original, client_self, request, kwargs)

        setattr(_traced_send, _PATCH_MARKER, True)
        httpx.Client.send = _traced_send
        self._original_httpx_send = original

    def _install_httpx_async(self, httpx):
        current = httpx.AsyncClient.__dict__.get("send")
        if getattr(current, _PATCH_MARKER, False):
            return
        original = httpx.AsyncClient.send

        async def _traced_send(client_self, request, **kwargs):
            if not self.is_capturing():
                return await original(client_self, request, **kwargs)
            return await _async_call(self, original, client_self, request, kwargs)

        setattr(_traced_send, _PATCH_MARKER, True)
        httpx.AsyncClient.send = _traced_send
        self._original_httpx_async_send = original


def _sync_call(watcher, library, original, owner_self, request, kwargs):
    streamed = bool(kwargs.get("stream"))
    method, url = request.method, str(request.url)
    start = time.monotonic()
    try:
        response = original(owner_self, request, **kwargs)
    except Exception as exc:
        _record_error(watcher, library, method, url, streamed, start, exc)
        raise
    _record_response(watcher, library, method, url, streamed, start, response)
    return response


async def _async_call(watcher, original, owner_self, request, kwargs):
    streamed = bool(kwargs.get("stream"))
    method, url = request.method, str(request.url)
    start = time.monotonic()
    try:
        response = await original(owner_self, request, **kwargs)
    except Exception as exc:
        _record_error(watcher, "httpx", method, url, streamed, start, exc)
        raise
    _record_response(watcher, "httpx", method, url, streamed, start, response)
    return response


def _response_bytes(response, streamed):
    """Content-Length when present; otherwise the already-read body's length
    (never forced — a streaming response's body may not be read yet)."""
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            return int(content_length)
        except (TypeError, ValueError):
            pass
    if streamed:
        return None
    try:
        return len(response.content)
    except Exception:
        return None


def _host(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:  # IPv6 literal
            host = "[%s]" % host
        if parts.port:
            host += ":%s" % parts.port
        return host or None
    except Exception:
        return None


def _base_metadata(library, method, url, streamed, start):
    origin = extract_origin()
    return {
        "library": library,
        "method": (method or "").upper(),
        "url": mask_url(url)[:MAX_URL_LENGTH],
        "host": _host(url),
        "status_code": None,
        "duration_ms": elapsed_ms(start),
        "redirects": 0,
        "response_bytes": None,
        "streamed": streamed,
        "origin_file": origin.get("file"),
        "origin_line": origin.get("line"),
        "origin_function": origin.get("function"),
    }


def _response_metadata(library, method, url, streamed, start, response):
    metadata = _base_metadata(library, method, url, streamed, start)
    metadata["status_code"] = response.status_code
    metadata["redirects"] = len(response.history)
    metadata["response_bytes"] = _response_bytes(response, streamed)
    return metadata


def _error_metadata(library, method, url, streamed, start, exc):
    metadata = _base_metadata(library, method, url, streamed, start)
    metadata["error"] = "%s: %s" % (type(exc).__name__, mask_value(str(exc)))
    return metadata


def _record_response(watcher, library, method, url, streamed, start, response):
    """Build and record the event for a successful call. Guarded end to end
    (metadata building included): a failure here must never break the call
    whose response has already been received."""
    from django_inspector.conf import inspector_settings

    try:
        metadata = _response_metadata(library, method, url, streamed, start, response)
        watcher.record("http.request", metadata)
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS:
            raise
        logger.warning("inspector: error recording an http.request event", exc_info=True)


def _record_error(watcher, library, method, url, streamed, start, exc):
    """Build and record the event for a failed call. Always swallows its own
    failures, whatever INSPECTOR_RAISE_ERRORS says: the user's exception is
    already propagating by the time this runs, and an inspector failure must
    never replace it."""
    try:
        metadata = _error_metadata(library, method, url, streamed, start, exc)
        watcher.record("http.request", metadata)
    except Exception:
        logger.warning("inspector: error recording a failed http.request event", exc_info=True)


register("http", HTTPClientWatcher)
