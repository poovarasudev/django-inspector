"""Tests for the system checks that validate DJANGO_INSPECTOR and the setup."""

from unittest import mock

import pytest
from django.test import override_settings

from django_inspector import checks


def _ids(messages):
    return [m.id for m in messages]


class TestSettingsCheck:
    def test_default_settings_pass(self):
        assert checks.check_settings() == []

    @override_settings(DJANGO_INSPECTOR=["not", "a", "dict"])
    def test_settings_must_be_a_dict(self):
        assert _ids(checks.check_settings()) == ["django_inspector.E001"]

    @override_settings(DJANGO_INSPECTOR={"SAMPLNG_RATE": 0.1})
    def test_unknown_key_warns_with_a_suggestion(self):
        [warning] = checks.check_settings()
        assert warning.id == "django_inspector.W001"
        assert "SAMPLING_RATE" in warning.hint

    @pytest.mark.parametrize("key, value", [
        ("SAMPLING_RATE", 5),
        ("SAMPLING_RATE", "0.5"),
        ("INSPECTOR_ENABLED", "yes"),
        ("MAX_BODY_SIZE", -1),
        ("MAX_EVENTS_PER_TRACE", 1.5),
        ("TRUSTED_PROXY_COUNT", True),
        ("SQL_SLOW_THRESHOLD_MS", -5),
        ("SENSITIVE_KEYS", "password"),
        ("SENSITIVE_KEYS", [None]),
        ("LOG_LEVEL_THRESHOLD", "LOUD"),
        ("RETENTION_HOURS", 0),
        ("DASHBOARD_URL_PREFIX", None),
        ("WATCHERS", ["sql"]),
    ])
    def test_invalid_values_are_errors(self, key, value):
        with override_settings(DJANGO_INSPECTOR={key: value}):
            assert "django_inspector.E002" in _ids(checks.check_settings())

    @pytest.mark.parametrize("key, value", [
        ("SAMPLING_RATE", 0),
        ("SAMPLING_RATE", 0.25),
        ("LOG_LEVEL_THRESHOLD", "error"),
        ("LOG_LEVEL_THRESHOLD", 35),
        ("WATCHERS", {"cache": True}),
        ("RETENTION_HOURS", None),
        ("RETENTION_HOURS", 48),
    ])
    def test_valid_values_pass(self, key, value):
        with override_settings(DJANGO_INSPECTOR={key: value}):
            assert checks.check_settings() == []

    @override_settings(DJANGO_INSPECTOR={"WATCHERS": {"cahce": True}})
    def test_unknown_watcher_warns(self):
        assert _ids(checks.check_settings()) == ["django_inspector.W002"]

    @override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": ["(unclosed"]})
    def test_invalid_regex_is_an_error(self):
        assert _ids(checks.check_settings()) == ["django_inspector.E003"]

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["10.0.0.0/33"]})
    def test_invalid_allowlist_entry_is_an_error(self):
        assert _ids(checks.check_settings()) == ["django_inspector.E004"]

    @override_settings(DJANGO_INSPECTOR={"IGNORE_EXCEPTIONS": ["nope.Missing"]})
    def test_unimportable_exception_warns(self):
        assert _ids(checks.check_settings()) == ["django_inspector.W003"]

    @override_settings(DJANGO_INSPECTOR={"SIGNAL_WATCH_LIST": ["django.db.models.Model"]})
    def test_non_signal_warns(self):
        assert _ids(checks.check_settings()) == ["django_inspector.W004"]

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_PERMISSION": "nope.check"})
    def test_unimportable_permission_is_an_error(self):
        assert _ids(checks.check_settings()) == ["django_inspector.E005"]


class TestMiddlewareCheck:
    def test_first_middleware_passes(self):
        assert checks.check_middleware() == []

    @override_settings(MIDDLEWARE=[])
    def test_missing_middleware_warns(self):
        assert _ids(checks.check_middleware()) == ["django_inspector.W005"]

    @override_settings(MIDDLEWARE=["django.middleware.common.CommonMiddleware", checks.MIDDLEWARE_PATH])
    def test_middleware_not_first_warns(self):
        assert _ids(checks.check_middleware()) == ["django_inspector.W006"]

    @override_settings(MIDDLEWARE=[], DJANGO_INSPECTOR={"INSPECTOR_ENABLED": False})
    def test_disabled_inspector_skips_the_check(self):
        assert checks.check_middleware() == []


class TestDashboardPrefixCheck:
    def test_matching_prefix_passes(self):
        assert checks.check_dashboard_prefix() == []

    @override_settings(DJANGO_INSPECTOR={"DASHBOARD_URL_PREFIX": "tools/"})
    def test_mismatched_prefix_warns(self):
        [warning] = checks.check_dashboard_prefix()
        assert warning.id == "django_inspector.W007"
        assert "'inspector/'" in warning.hint

    @override_settings(ROOT_URLCONF="tests.asgi_urls")
    def test_unmounted_dashboard_is_skipped(self):
        assert checks.check_dashboard_prefix() == []


class TestProductionAccessCheck:
    @override_settings(DEBUG=False)
    def test_staff_only_in_production_warns(self):
        assert _ids(checks.check_production_access()) == ["django_inspector.W008"]

    @override_settings(DEBUG=False, DJANGO_INSPECTOR={"INSPECTOR_DASHBOARD_IP_ALLOWLIST": ["10.0.0.0/8"]})
    def test_allowlist_satisfies_the_check(self):
        assert checks.check_production_access() == []

    @override_settings(DEBUG=True)
    def test_debug_skips_the_check(self):
        assert checks.check_production_access() == []


class TestChecksAreRegistered:
    def test_run_checks_includes_inspector_checks(self):
        from django.core.checks import run_checks

        with override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 7}):
            assert "django_inspector.E002" in _ids(run_checks())


class TestWatcherImportFailures:
    def test_a_failing_watcher_module_is_logged_and_skipped(self, caplog):
        from django_inspector import apps

        real_import = apps.import_module

        def fake_import(name):
            if name.endswith(".cache"):
                raise ImportError("boom")
            return real_import(name)

        with mock.patch.object(apps, "import_module", side_effect=fake_import):
            with caplog.at_level("WARNING", logger="django_inspector"):
                apps._import_watcher_modules()
        assert "django_inspector.watchers.cache" in caplog.text

    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_RAISE_ERRORS": True})
    def test_failure_raises_with_raise_errors(self):
        from django_inspector import apps

        with mock.patch.object(apps, "import_module", side_effect=ImportError("boom")):
            with pytest.raises(ImportError):
                apps._import_watcher_modules()
