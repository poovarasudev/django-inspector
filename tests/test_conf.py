"""Tests for the settings accessor: merged once, refreshed when settings change."""

from unittest import mock

from django.test import override_settings

from django_inspector import conf
from django_inspector.conf import inspector_settings


class TestSettingsCache:
    def test_merged_settings_are_built_once(self):
        conf._clear_cache()
        with mock.patch.object(conf, "_build_settings", wraps=conf._build_settings) as build:
            for _ in range(5):
                inspector_settings.SAMPLING_RATE
                inspector_settings.is_enabled
                inspector_settings.watcher_enabled("sql")
        assert build.call_count == 1

    def test_override_settings_refreshes_the_cache(self):
        assert inspector_settings.SAMPLING_RATE == 1.0
        with override_settings(DJANGO_INSPECTOR={"SAMPLING_RATE": 0.25}):
            assert inspector_settings.SAMPLING_RATE == 0.25
        assert inspector_settings.SAMPLING_RATE == 1.0

    def test_other_settings_do_not_clear_the_cache(self):
        inspector_settings.SAMPLING_RATE
        with mock.patch.object(conf, "_build_settings", wraps=conf._build_settings) as build:
            with override_settings(TIME_ZONE="Asia/Kolkata"):
                inspector_settings.SAMPLING_RATE
        assert build.call_count == 0

    def test_ignore_path_patterns_follow_setting_changes(self):
        from django_inspector.ignores import should_ignore_path

        with override_settings(DJANGO_INSPECTOR={"IGNORE_PATHS": [r"^/healthz$"]}):
            assert should_ignore_path("/healthz")
        assert not should_ignore_path("/healthz")
