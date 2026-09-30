"""
Tests for dashboard access control.

Requirements: AUTH-01..05
"""

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from django_inspector.dashboard.auth import can_access_dashboard, inspector_required


def _make_request(path="/inspector/", user=None, remote_addr="127.0.0.1"):
    factory = RequestFactory(REMOTE_ADDR=remote_addr)
    request = factory.get(path)
    request.user = user or AnonymousUser()
    return request


def _dummy_view(request):
    return HttpResponse("ok", status=200)


@pytest.mark.django_db
class TestInspectorRequired(TestCase):
    def test_anonymous_user_gets_403(self):
        request = _make_request(user=AnonymousUser())
        view = inspector_required(_dummy_view)
        response = view(request)
        assert response.status_code == 403

    def test_non_staff_user_gets_403(self):
        user = User.objects.create_user(username="regular", password="x", is_staff=False)
        request = _make_request(user=user)
        view = inspector_required(_dummy_view)
        response = view(request)
        assert response.status_code == 403

    def test_staff_user_gets_200(self):
        user = User.objects.create_user(username="staff", password="x", is_staff=True)
        request = _make_request(user=user)
        view = inspector_required(_dummy_view)
        response = view(request)
        assert response.status_code == 200

    def test_403_body_contains_no_event_data(self):
        request = _make_request(user=AnonymousUser())
        view = inspector_required(_dummy_view)
        response = view(request)
        body = response.content.decode()
        assert "trace" not in body.lower()
        assert "event" not in body.lower()
        assert "inspector_trace_id" not in body


@pytest.mark.django_db
class TestCustomPermissionCallable(TestCase):
    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_PERMISSION": "tests.test_dashboard_auth._allow_all"})
    def test_custom_callable_returning_true_grants_access(self):
        request = _make_request(user=AnonymousUser())
        view = inspector_required(_dummy_view)
        response = view(request)
        assert response.status_code == 200

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_PERMISSION": "tests.test_dashboard_auth._deny_all"})
    def test_custom_callable_returning_false_denies_access(self):
        user = User.objects.create_user(username="staff2", password="x", is_staff=True)
        request = _make_request(user=user)
        view = inspector_required(_dummy_view)
        response = view(request)
        assert response.status_code == 403


class TestIPAllowlist(TestCase):
    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["192.168.1.0/24"]})
    def test_ip_in_allowlist_passes(self):
        user_anon = AnonymousUser()
        request = _make_request(user=user_anon, remote_addr="192.168.1.50")
        assert not can_access_dashboard(request)

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["192.168.1.0/24"]})
    def test_ip_outside_allowlist_blocked(self):
        user_anon = AnonymousUser()
        request = _make_request(user=user_anon, remote_addr="10.0.0.1")
        assert not can_access_dashboard(request)

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["10.0.0.0/8"]})
    def test_spoofed_x_forwarded_for_does_not_pass_the_allowlist(self):
        from django_inspector.dashboard.auth import _ip_allowed
        request = RequestFactory(
            REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_FOR="10.0.0.1"
        ).get("/inspector/")
        assert _ip_allowed(request) is False

    @override_settings(DJANGO_INSPECTOR={
        "INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["10.0.0.0/8"],
        "TRUSTED_PROXY_COUNT": 1,
    })
    def test_x_forwarded_for_is_used_behind_a_trusted_proxy(self):
        from django_inspector.dashboard.auth import _ip_allowed
        request = RequestFactory(
            REMOTE_ADDR="172.16.0.2", HTTP_X_FORWARDED_FOR="10.0.0.1"
        ).get("/inspector/")
        assert _ip_allowed(request) is True

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_IP_ALLOWLIST": []})
    def test_empty_allowlist_does_not_block_by_ip(self):
        from django_inspector.dashboard.auth import _ip_allowed
        request = _make_request(user=AnonymousUser(), remote_addr="1.2.3.4")
        assert _ip_allowed(request) is True


def _allow_all(request):
    return True


def _deny_all(request):
    return False
