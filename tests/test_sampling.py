"""
Tests for sampling — SAMPLING_RATE and SLOW_REQUEST_THRESHOLD_MS behavior.

Requirements: SAMP-01..05
"""

import time

import pytest
from django.test import TestCase, override_settings

from django_inspector.sampling import compute_sampling_decision, is_sampled, set_sampled
from django_inspector.storage.models import Event


class MockResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class MockRequest:
    def __init__(self, latency_ms=None):
        if latency_ms is not None:
            self._inspector_start_time = time.monotonic() - latency_ms / 1000.0


class TestComputeSamplingDecision:
    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 1.0})
    def test_rate_1_always_captures(self):
        req = MockRequest()
        assert compute_sampling_decision(req, MockResponse(200)) is True

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_rate_0_rejects_200(self):
        req = MockRequest()
        assert compute_sampling_decision(req, MockResponse(200)) is False

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_rate_0_captures_500(self):
        req = MockRequest()
        assert compute_sampling_decision(req, MockResponse(500)) is True

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_rate_0_captures_503(self):
        req = MockRequest()
        assert compute_sampling_decision(req, MockResponse(503)) is True

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0, "SLOW_REQUEST_THRESHOLD_MS": 0})
    def test_slow_threshold_0_always_captures(self):
        req = MockRequest(latency_ms=1)
        assert compute_sampling_decision(req, MockResponse(200)) is True

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0, "SLOW_REQUEST_THRESHOLD_MS": 5000})
    def test_fast_request_not_captured_at_rate_0(self):
        req = MockRequest(latency_ms=10)
        assert compute_sampling_decision(req, MockResponse(200)) is False


class TestSampledContextVar:
    def test_default_is_true(self):
        set_sampled(True)
        assert is_sampled() is True

    def test_set_false(self):
        set_sampled(False)
        assert is_sampled() is False
        set_sampled(True)


@pytest.mark.django_db
class TestSamplingIntegration(TestCase):
    """Integration tests using InspectorMiddleware directly via RequestFactory."""

    def _make_response_via_middleware(self, status_code, sampling_rate):
        """Run a request through InspectorMiddleware with a stub view."""
        from django.http import HttpResponse
        from django.test import RequestFactory
        from django_inspector.middleware import InspectorMiddleware

        factory = RequestFactory()
        request = factory.get("/test/")

        def stub_view(req):
            return HttpResponse("ok", status=status_code)

        with override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": sampling_rate}):
            middleware = InspectorMiddleware(stub_view)
            middleware(request)
        return request

    def test_rate_0_no_events_for_200(self):
        Event.objects.all().delete()
        self._make_response_via_middleware(status_code=200, sampling_rate=0.0)
        assert Event.objects.count() == 0

    def test_rate_0_events_captured_for_500(self):
        Event.objects.all().delete()
        self._make_response_via_middleware(status_code=500, sampling_rate=0.0)
        assert Event.objects.count() > 0

    def test_rate_1_events_captured_for_200(self):
        Event.objects.all().delete()
        self._make_response_via_middleware(status_code=200, sampling_rate=1.0)
        assert Event.objects.count() > 0

    def test_sampled_flag_on_request_false_when_discarded(self):
        request = self._make_response_via_middleware(status_code=200, sampling_rate=0.0)
        assert getattr(request, "inspector_sampled", True) is False

    def test_sampled_flag_on_request_true_when_captured(self):
        request = self._make_response_via_middleware(status_code=200, sampling_rate=1.0)
        assert getattr(request, "inspector_sampled", False) is True
