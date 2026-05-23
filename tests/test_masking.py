"""
Tests for django_inspector.masking — sensitive-data redaction.

Requirements: MASK-01..06
"""

import pytest
from django.test import override_settings

from django_inspector.masking import mask_metadata, _REDACTED, _REDACTED_DEEP


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
