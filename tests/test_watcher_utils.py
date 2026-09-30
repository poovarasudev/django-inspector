"""Tests for helpers shared by watchers (origin extraction)."""

import os
from unittest import mock

import django

import django_inspector
from django_inspector.watchers import utils
from django_inspector.watchers.utils import extract_origin


def _call_from_app_code():
    return extract_origin()


class TestExtractOrigin:
    def test_returns_the_calling_app_frame(self):
        origin = _call_from_app_code()
        assert origin["file"].endswith("test_watcher_utils.py")
        assert origin["function"] == "_call_from_app_code"

    def test_does_not_format_the_whole_stack(self):
        # traceback.extract_stack() reads source lines for every frame: too slow per query.
        with mock.patch("traceback.extract_stack", side_effect=AssertionError("slow path")):
            assert _call_from_app_code()["function"] == "_call_from_app_code"

    def test_library_files_are_skipped(self):
        assert utils._is_library_file(os.path.join(os.path.dirname(django.__file__), "db", "x.py"))
        assert utils._is_library_file(os.path.join(os.path.dirname(django_inspector.__file__), "m.py"))
        assert utils._is_library_file("<frozen importlib._bootstrap>")
        assert utils._is_library_file("/venv/lib/python3.12/site-packages/rest_framework/views.py")

    def test_windows_style_library_paths_are_skipped(self):
        assert utils._is_library_file(r"C:\venv\Lib\site-packages\rest_framework\views.py")

    def test_app_files_are_not_skipped(self):
        assert not utils._is_library_file(__file__)
