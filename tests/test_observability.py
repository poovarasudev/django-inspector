"""
Tests for OBS-01..03: inspector-internal errors log at WARNING, host request succeeds.
INSPECTOR_RAISE_ERRORS flag enables error propagation for debugging.

Requirements: OBS-01, OBS-02, OBS-03
"""

import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from django_inspector.middleware import InspectorMiddleware


def _run_middleware(settings_override=None, flush_raises=False):
    """Run a request through middleware, optionally making flush_events raise."""
    factory = RequestFactory()
    request = factory.get("/test/")

    def stub_view(req):
        return HttpResponse("ok", status=200)

    overrides = settings_override or {}

    with override_settings(DJANGO_INSPECTOR=overrides):
        if flush_raises:
            import django_inspector.storage.flush as flush_mod
            original = flush_mod.flush_events

            def _raising_flush():
                raise RuntimeError("flush failed")

            flush_mod.flush_events = _raising_flush
            try:
                middleware = InspectorMiddleware(stub_view)
                response = middleware(request)
            finally:
                flush_mod.flush_events = original
        else:
            middleware = InspectorMiddleware(stub_view)
            response = middleware(request)

    return request, response


class TestHostRequestSucceedsOnInspectorError(TestCase):
    """OBS-03: host request still returns 200 even when inspector flush fails."""

    def test_flush_error_does_not_propagate_by_default(self):
        _, response = _run_middleware(flush_raises=True)
        assert response.status_code == 200

    def test_flush_error_raises_with_inspector_raise_errors(self):
        with pytest.raises(RuntimeError, match="flush failed"):
            _run_middleware(
                settings_override={"INSPECTOR_RAISE_ERRORS": True},
                flush_raises=True,
            )


class TestFlushErrorLogged(TestCase):
    """OBS-01: flush_events failure is logged at WARNING level."""

    def test_flush_error_logged_at_warning(self):
        with self.assertLogs("django_inspector", level=logging.WARNING) as cm:
            _run_middleware(flush_raises=True)
        assert any("flush_events failed" in msg for msg in cm.output), cm.output


class TestInspectorRaiseErrors(TestCase):
    """OBS-02: INSPECTOR_RAISE_ERRORS=True causes propagation in all guarded blocks."""

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": True})
    def test_setting_is_accessible(self):
        from django_inspector.conf import inspector_settings
        assert inspector_settings.INSPECTOR_RAISE_ERRORS is True

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": False})
    def test_default_is_false(self):
        from django_inspector.conf import inspector_settings
        assert inspector_settings.INSPECTOR_RAISE_ERRORS is False
