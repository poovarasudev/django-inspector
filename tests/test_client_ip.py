"""
Tests for client IP resolution shared by the request watcher and the dashboard
IP allowlist. X-Forwarded-For is only trusted for TRUSTED_PROXY_COUNT hops.
"""

from django.test import RequestFactory, override_settings

from django_inspector.client_ip import get_client_ip


def _request(**meta):
    return RequestFactory().get("/", **meta)


class TestGetClientIp:
    def test_uses_remote_addr_by_default(self):
        request = _request(REMOTE_ADDR="203.0.113.9")
        assert get_client_ip(request) == "203.0.113.9"

    def test_ignores_x_forwarded_for_without_trusted_proxies(self):
        request = _request(REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_FOR="10.0.0.1")
        assert get_client_ip(request) == "203.0.113.9"

    @override_settings(DJANGO_INSPECTOR={"TRUSTED_PROXY_COUNT": 1})
    def test_one_trusted_proxy_takes_the_last_hop(self):
        # A client can prepend anything; the trusted proxy appends the real address.
        request = _request(REMOTE_ADDR="10.1.1.1", HTTP_X_FORWARDED_FOR="10.0.0.1, 198.51.100.7")
        assert get_client_ip(request) == "198.51.100.7"

    @override_settings(DJANGO_INSPECTOR={"TRUSTED_PROXY_COUNT": 2})
    def test_two_trusted_proxies_take_the_second_to_last_hop(self):
        request = _request(
            REMOTE_ADDR="10.1.1.1",
            HTTP_X_FORWARDED_FOR="10.0.0.1, 198.51.100.7, 10.2.2.2",
        )
        assert get_client_ip(request) == "198.51.100.7"

    @override_settings(DJANGO_INSPECTOR={"TRUSTED_PROXY_COUNT": 3})
    def test_fewer_hops_than_proxies_uses_the_first_hop(self):
        request = _request(REMOTE_ADDR="10.1.1.1", HTTP_X_FORWARDED_FOR="198.51.100.7, 10.2.2.2")
        assert get_client_ip(request) == "198.51.100.7"

    @override_settings(DJANGO_INSPECTOR={"TRUSTED_PROXY_COUNT": 1})
    def test_trusted_proxy_without_header_falls_back_to_remote_addr(self):
        request = _request(REMOTE_ADDR="10.1.1.1")
        assert get_client_ip(request) == "10.1.1.1"

    def test_missing_remote_addr_is_empty(self):
        request = _request()
        request.META.pop("REMOTE_ADDR", None)
        assert get_client_ip(request) == ""
