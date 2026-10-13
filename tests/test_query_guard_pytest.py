"""Tests for the pytest plugin (TEST-03): fixture, marker, safe import."""

import os
import subprocess
import sys

import pytest

pytest_plugins = ["pytester"]


def _pytester_ini(pytester):
    """A tiny project settings module + pytest.ini pointing pytester's run at it."""
    pytester.makepyfile(_settings="""
        SECRET_KEY = "x"
        INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "django_inspector"]
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        USE_TZ = True
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
    """)
    pytester.makeini("""
        [pytest]
        DJANGO_SETTINGS_MODULE = _settings
    """)


@pytest.fixture(autouse=True)
def _isolated_django_settings_module(monkeypatch):
    """
    Each pytester sub-run configures its own Django settings; the outer
    session's DJANGO_SETTINGS_MODULE (tests.settings) must not leak into it.
    """
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)


class TestFixture:
    def test_fixture_works(self, pytester):
        _pytester_ini(pytester)
        pytester.makepyfile("""
            import pytest
            from django.db import connection

            @pytest.mark.django_db
            def test_ok(assert_queries):
                with assert_queries(max=1) as guard:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                assert len(guard.queries) == 1
        """)
        result = pytester.runpytest_subprocess("-p", "pytest_django")
        result.assert_outcomes(passed=1)


class TestMarker:
    def test_marker_fails_over_budget(self, pytester):
        _pytester_ini(pytester)
        pytester.makepyfile("""
            import pytest
            from django.db import connection

            @pytest.mark.django_db
            @pytest.mark.inspector_queries(max=0)
            def test_too_many():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
        """)
        result = pytester.runpytest_subprocess("-p", "pytest_django")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*QueryBudgetExceeded*"])

    def test_marker_passes_within_budget(self, pytester):
        _pytester_ini(pytester)
        pytester.makepyfile("""
            import pytest
            from django.db import connection

            @pytest.mark.django_db
            @pytest.mark.inspector_queries(max=5)
            def test_within_budget():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
        """)
        result = pytester.runpytest_subprocess("-p", "pytest_django")
        result.assert_outcomes(passed=1)


class TestPluginImport:
    def test_plugin_module_imports_without_django_configured(self):
        env = dict(os.environ)
        env.pop("DJANGO_SETTINGS_MODULE", None)
        result = subprocess.run(
            [sys.executable, "-c", "import django_inspector.testing.pytest_plugin"],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr

    def test_registered_as_pytest11_entry_point(self):
        from importlib.metadata import entry_points

        eps = entry_points()
        # EntryPoints.select() is 3.10+; entry_points() returned a plain
        # {group: [EntryPoint, ...]} dict on 3.8/3.9.
        found = eps.select(group="pytest11") if hasattr(eps, "select") else eps.get("pytest11", [])
        assert "django_inspector" in {ep.name for ep in found}
