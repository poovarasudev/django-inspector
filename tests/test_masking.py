"""
Tests for django_inspector.masking — sensitive-data redaction.

Requirements: MASK-01..06
"""

import pytest
from django.test import override_settings

from django_inspector.masking import mask_metadata, _REDACTED, _REDACTED_DEEP
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.base import BaseWatcher


class TestKeyBasedRedaction:
    def test_password_key_redacted(self):
        result = mask_metadata({"password": "s3cr3t"})
        assert result["password"] == _REDACTED

    def test_token_key_redacted(self):
        result = mask_metadata({"token": "abc123"})
        assert result["token"] == _REDACTED

    def test_authorization_key_redacted(self):
        result = mask_metadata({"authorization": "Bearer xyz"})
        assert result["authorization"] == _REDACTED

    def test_case_insensitive_matching(self):
        result = mask_metadata({"Authorization": "Bearer tok"})
        assert result["Authorization"] == _REDACTED

    def test_cookie_key_redacted(self):
        result = mask_metadata({"Cookie": "sessionid=abc"})
        assert result["Cookie"] == _REDACTED

    @pytest.mark.parametrize("key", ["X-Csrftoken", "Proxy-Authorization", "X-Auth-Token"])
    def test_credential_headers_redacted(self, key):
        assert mask_metadata({key: "abc"})[key] == "***REDACTED***"

    def test_non_sensitive_key_passes_through(self):
        result = mask_metadata({"username": "alice", "role": "admin"})
        assert result["username"] == "alice"
        assert result["role"] == "admin"


class TestNestedRedaction:
    def test_nested_dict(self):
        result = mask_metadata({"user": {"token": "abc123", "name": "alice"}})
        assert result["user"]["token"] == _REDACTED
        assert result["user"]["name"] == "alice"

    def test_list_containing_dicts(self):
        result = mask_metadata({"items": [{"password": "x"}, {"name": "y"}]})
        assert result["items"][0]["password"] == _REDACTED
        assert result["items"][1]["name"] == "y"

    def test_tuple_containing_dicts(self):
        result = mask_metadata({"pair": ({"secret": "s"}, "safe")})
        assert result["pair"][0]["secret"] == _REDACTED
        assert result["pair"][1] == "safe"

    def test_depth_limit_truncates(self):
        deep = {"k": {}}
        node = deep
        for _ in range(12):
            node["k"] = {"k": {}}
            node = node["k"]
        result = mask_metadata(deep)
        depth = 0
        r = result
        while isinstance(r, dict) and "k" in r:
            r = r["k"]
            depth += 1
        assert r == _REDACTED_DEEP


class TestValuePatternScanning:
    JWT = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )

    def test_jwt_shaped_value_redacted(self):
        result = mask_metadata({"header": self.JWT})
        assert result["header"] == _REDACTED

    def test_credit_card_shaped_value_redacted(self):
        result = mask_metadata({"body": "card=4111111111111111&other=x"})
        assert result["body"] == _REDACTED

    def test_plain_string_passes_through(self):
        result = mask_metadata({"message": "hello world"})
        assert result["message"] == "hello world"


class TestExtraKeysFromSettings:
    @override_settings(DJANGO_INSPECTOR={"SENSITIVE_KEYS": ["my_secret_field"]})
    def test_extra_key_from_settings_redacted(self):
        result = mask_metadata({"my_secret_field": "private"})
        assert result["my_secret_field"] == _REDACTED

    @override_settings(DJANGO_INSPECTOR={"SENSITIVE_KEYS": ["custom_key"]})
    def test_builtin_keys_still_redacted_with_extra(self):
        result = mask_metadata({"password": "x", "custom_key": "y", "name": "z"})
        assert result["password"] == _REDACTED
        assert result["custom_key"] == _REDACTED
        assert result["name"] == "z"


class TestNonDictTypes:
    def test_integer_passes_through(self):
        result = mask_metadata({"count": 42})
        assert result["count"] == 42

    def test_none_passes_through(self):
        result = mask_metadata({"value": None})
        assert result["value"] is None

    def test_bool_passes_through(self):
        result = mask_metadata({"flag": True})
        assert result["flag"] is True


class TestCardNumbersNeedLuhn:
    """MASK-04: only Luhn-valid 13–19 digit numbers are treated as card numbers."""

    @pytest.mark.parametrize("value", [
        "4111111111111111",                    # Visa test number
        "card 4111 1111 1111 1111 declined",   # grouped with spaces
        "5555-5555-5555-4444",                 # grouped with dashes
        "378282246310005",                     # Amex, 15 digits
        "4111111111111111 12/26",              # card followed by an expiry date
        "ref:6011111111111117",                # glued to a prefix
    ])
    def test_luhn_valid_card_numbers_are_redacted(self, value):
        assert mask_metadata({"v": value})["v"] == _REDACTED

    @pytest.mark.parametrize("value", [
        "4111111111111112",                    # right shape, fails Luhn
        "order 1234567890123 shipped",         # 13-digit id, fails Luhn
        "at 20260930104422",                   # timestamp, fails Luhn
        "id 1234567890123456789012345",        # 25 digits: too long for a card
        "call +1 555-123-4567",                # phone number: too few digits
    ])
    def test_other_digit_runs_pass_through(self, value):
        assert mask_metadata({"v": value})["v"] == value


class TestMaskingFailsClosed:
    """MASK-05: if masking raises, the original metadata never reaches the buffer."""

    class DemoWatcher(BaseWatcher):
        watcher_name = "demo"

        def install_hooks(self):
            pass

        def remove_hooks(self):
            pass

    @pytest.fixture(autouse=True)
    def _watcher_and_trace(self, monkeypatch):
        def broken_masking(metadata):
            raise ValueError("masking blew up on hunter2")

        monkeypatch.setattr("django_inspector.masking.mask_metadata", broken_masking)
        clear_buffer()
        token = set_trace_id(generate_trace_id())
        self.watcher = self.DemoWatcher()
        self.watcher.enable()
        yield
        self.watcher.disable()
        clear_trace_id(token)
        clear_buffer()

    def test_a_placeholder_is_recorded_instead_of_the_data(self):
        self.watcher.record("demo.event", {"password": "hunter2", "note": "hello"})
        [event] = _get_buffer()
        assert event["event_type"] == "demo.event"
        assert event["metadata"] == {"masking_failed": True, "error_type": "ValueError"}
        assert "hunter2" not in str(event)

    def test_the_failure_is_logged(self, caplog):
        with caplog.at_level("WARNING", logger="django_inspector"):
            self.watcher.record("demo.event", {"note": "hello"})
        assert any("masking failed" in r.getMessage() for r in caplog.records)

    def test_the_error_is_raised_when_raise_errors_is_on(self):
        with override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": True}):
            with pytest.raises(ValueError):
                self.watcher.record("demo.event", {"password": "hunter2"})
        assert _get_buffer() == []
