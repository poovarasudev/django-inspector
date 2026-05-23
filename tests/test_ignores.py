"""
Tests for ignore lists — IGNORE_PATHS and IGNORE_EXCEPTIONS.

Requirements: IGN-01..04
"""

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from django_inspector.ignores import (
    invalidate_ignore_caches,
    should_ignore_exception,
    should_ignore_path,
)
from django_inspector.storage.models import Event


class TestIgnorePaths:
    def setup_method(self):
        invalidate_ignore_caches()

    def teardown_method(self):
        invalidate_ignore_caches()

    @override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ["/healthz"]})
    def test_exact_path_ignored(self):
        invalidate_ignore_caches()
        assert should_ignore_path("/healthz") is True

    @override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ["/health"]})
    def test_regex_prefix_match(self):
        invalidate_ignore_caches()
        assert should_ignore_path("/healthz") is True
        assert should_ignore_path("/health_check") is True

    @override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ["/healthz"]})
    def test_non_matching_path_not_ignored(self):
        invalidate_ignore_caches()
        assert should_ignore_path("/api/users/") is False

    @override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": []})
    def test_empty_list_ignores_nothing(self):
        invalidate_ignore_caches()
        assert should_ignore_path("/healthz") is False

    @override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ["/metrics", "/readyz"]})
    def test_multiple_patterns(self):
        invalidate_ignore_caches()
        assert should_ignore_path("/metrics") is True
        assert should_ignore_path("/readyz") is True
        assert should_ignore_path("/api/") is False


class TestIgnoreExceptions:
    def setup_method(self):
        invalidate_ignore_caches()

    def teardown_method(self):
        invalidate_ignore_caches()

    @override_settings(DJANGO_INSPECTOR={"IGNORE_EXCEPTIONS": ["django.http.Http404"]})
    def test_http404_ignored(self):
        invalidate_ignore_caches()
        assert should_ignore_exception(Http404()) is True

    @override_settings(DJANGO_INSPECTOR={"IGNORE_EXCEPTIONS": ["django.http.Http404"]})
    def test_other_exception_not_ignored(self):
        invalidate_ignore_caches()
        assert should_ignore_exception(ValueError("oops")) is False

    @override_settings(DJANGO_INSPECTOR={"IGNORE_EXCEPTIONS": []})
    def test_empty_list_ignores_nothing(self):
        invalidate_ignore_caches()
        assert should_ignore_exception(Http404()) is False

    @override_settings(DJANGO_INSPECTOR={"IGNORE_EXCEPTIONS": ["builtins.ValueError", "django.http.Http404"]})
    def test_multiple_exception_classes(self):
        invalidate_ignore_caches()
        assert should_ignore_exception(ValueError()) is True
        assert should_ignore_exception(Http404()) is True
        assert should_ignore_exception(RuntimeError()) is False


@pytest.mark.django_db
class TestIgnorePathsMiddlewareIntegration(TestCase):
    """Verify ignored paths produce zero events via middleware."""

    def _run_middleware(self, path, ignore_paths):
        from django_inspector.ignores import invalidate_ignore_caches
        from django_inspector.middleware import InspectorMiddleware

        invalidate_ignore_caches()
        factory = RequestFactory()
        request = factory.get(path)

        def stub_view(req):
            return HttpResponse("ok", status=200)

        with override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ignore_paths}):
            invalidate_ignore_caches()
            middleware = InspectorMiddleware(stub_view)
            middleware(request)

        invalidate_ignore_caches()
        return request

    def test_ignored_path_produces_no_events(self):
        Event.objects.all().delete()
        self._run_middleware("/healthz", ["/healthz"])
        assert Event.objects.count() == 0

    def test_ignored_path_has_no_trace_id(self):
        request = self._run_middleware("/healthz", ["/healthz"])
        assert not hasattr(request, "inspector_trace_id")

    def test_non_ignored_path_produces_events(self):
        Event.objects.all().delete()
        self._run_middleware("/api/users/", ["/healthz"])
        assert Event.objects.count() > 0
