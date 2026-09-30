"""
Tests for sampling — SAMPLING_RATE and SLOW_REQUEST_THRESHOLD_MS behavior.

Requirements: SAMP-01..05
"""

import time

import pytest
from django.test import TestCase, override_settings

from django_inspector.sampling import compute_sampling_decision, detail_enabled
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


class TestEarlyPickDecision:
    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_early_pick_keeps_the_request(self):
        assert compute_sampling_decision(MockRequest(), MockResponse(200), early_pick=True) is True

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.99})
    def test_early_reject_drops_a_fast_success_whatever_the_rate(self):
        assert compute_sampling_decision(MockRequest(), MockResponse(200), early_pick=False) is False

    @override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.0})
    def test_early_reject_still_keeps_errors(self):
        assert compute_sampling_decision(MockRequest(), MockResponse(500), early_pick=False) is True


def test_detail_is_enabled_outside_requests():
    assert detail_enabled() is True


@pytest.mark.django_db
class TestEarlySampling(TestCase):
    """EARLY_SAMPLING: requests not picked at the start skip detailed capture."""

    def _run(self, view, rate, pick):
        from unittest import mock

        from django.test import RequestFactory

        from django_inspector.middleware import InspectorMiddleware

        Event.objects.all().delete()
        settings = {"SAMPLING_RATE": rate, "EARLY_SAMPLING": True}
        with override_settings(DJANGO_INSPECTOR=settings), \
                mock.patch("django_inspector.sampling.random.random", return_value=pick):
            InspectorMiddleware(view)(RequestFactory().get("/early/"))
        return sorted(Event.objects.values_list("event_type", flat=True))

    @staticmethod
    def _querying_view(status=200):
        from django.db import connection
        from django.http import HttpResponse

        def view(request):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return HttpResponse("ok", status=status)

        return view

    def test_unpicked_success_records_nothing_and_skips_sql_work(self):
        from unittest import mock

        with mock.patch("django_inspector.watchers.sql.extract_origin") as origin:
            events = self._run(self._querying_view(), rate=0.5, pick=0.9)
        assert events == []
        origin.assert_not_called()

    def test_unpicked_error_keeps_the_request_but_not_the_detail(self):
        events = self._run(self._querying_view(status=500), rate=0.5, pick=0.9)
        assert events == ["request.completed"]

    def test_picked_request_is_captured_in_full(self):
        events = self._run(self._querying_view(), rate=0.5, pick=0.1)
        assert events == ["request.completed", "sql.query"]


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
