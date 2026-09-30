"""Tests for the Template Watcher (TMPL-01..TMPL-05)."""

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.template import Context, Engine
from django.template.base import Template
from django.template.loader import render_to_string
from django.test import TestCase

from django_inspector.conf import inspector_settings
from django_inspector.storage.flush import _get_buffer, clear_buffer
from django_inspector.tracing.context import clear_trace_id, generate_trace_id, set_trace_id
from django_inspector.watchers.template import TemplateWatcher

SOURCES = {
    "base.html": "<main>{% block content %}{% endblock %}</main>",
    "child.html": '{% extends "base.html" %}{% block content %}{% include "part.html" %}{% endblock %}',
    "part.html": "<p>{{ title }}</p>",
    "page.html": '<h1>{{ title }}</h1>{% include "part.html" %}',
    "boom.html": "{{ obj.explode }}",
    "outer_boom.html": '{% include "boom.html" %}',
}


class Exploding:
    @property
    def explode(self):
        raise ValueError("boom")


def renders():
    events = [e for e in _get_buffer() if e["event_type"] == "template.rendered"]
    return sorted(events, key=lambda e: e["metadata"]["render_id"])


def tree():
    return [
        (m["name"], m["render_id"], m["parent_id"], m["depth"], m["relation"])
        for m in (e["metadata"] for e in renders())
    ]


class TestTemplateWatcherLifecycle(TestCase):
    def test_watcher_name(self):
        assert TemplateWatcher.watcher_name == "template"

    def test_off_by_default(self):
        assert inspector_settings.watcher_enabled("template") is False

    def test_enable_wraps_and_disable_restores_render(self):
        original = Template._render
        watcher = TemplateWatcher()
        watcher.enable()
        watcher.enable()
        try:
            assert Template._render is not original
            assert Template._render.__wrapped__ is original
        finally:
            watcher.disable()
        watcher.disable()
        assert Template._render is original


class TemplateWatcherTestCase(TestCase):
    def setUp(self):
        self.engine = Engine(loaders=[("django.template.loaders.locmem.Loader", SOURCES)])
        self.watcher = TemplateWatcher()
        self.watcher.enable()
        self.trace_id = generate_trace_id()
        self.token = set_trace_id(self.trace_id)
        clear_buffer()

    def tearDown(self):
        self.watcher.disable()
        if self.token is not None:
            clear_trace_id(self.token)
        clear_buffer()


class TestTemplateCapture(TemplateWatcherTestCase):
    def test_records_name_source_duration_and_context_keys(self):
        self.engine.get_template("part.html").render(Context({"title": "Hi", "user": "ann"}))
        [event] = renders()
        meta = event["metadata"]
        assert meta["name"] == "part.html"
        assert meta["origin_path"] == "part.html"
        assert meta["loader"] == "django.template.loaders.locmem.Loader"
        assert meta["duration_ms"] >= 0
        assert meta["context_keys"] == ["title", "user"]
        assert meta["context_key_count"] == 2
        assert "ann" not in str(meta)

    def test_context_keys_are_capped(self):
        context = {"k%03d" % i: i for i in range(80)}
        self.engine.from_string("x").render(Context(context))
        meta = renders()[0]["metadata"]
        assert meta["context_key_count"] == 80
        assert len(meta["context_keys"]) == 50

    def test_from_string_template_has_no_name(self):
        self.engine.from_string("hello").render(Context({}))
        meta = renders()[0]["metadata"]
        assert meta["name"] is None
        assert meta["origin_path"] == "<unknown source>"
        assert meta["loader"] is None
        assert (meta["depth"], meta["parent_id"], meta["relation"]) == (0, None, None)

    def test_filesystem_template_records_source_path(self):
        render_to_string("inspector_tests/hello.html", {"name": "Ann"})
        meta = renders()[0]["metadata"]
        assert meta["name"] == "inspector_tests/hello.html"
        assert meta["origin_path"].endswith("tests/templates/inspector_tests/hello.html")
        assert meta["loader"] == "django.template.loaders.filesystem.Loader"


class TestRenderTree(TemplateWatcherTestCase):
    def test_extends_and_include_tree(self):
        self.engine.get_template("child.html").render(Context({"title": "Hi"}))
        # part.html is written inside child's block, but it renders while base.html
        # is rendering, so its runtime parent is base.html.
        assert tree() == [
            ("child.html", 1, None, 0, None),
            ("base.html", 2, 1, 1, "extends"),
            ("part.html", 3, 2, 2, "include"),
        ]

    def test_include_is_nested_under_includer(self):
        self.engine.get_template("page.html").render(Context({"title": "Hi"}))
        assert tree() == [
            ("page.html", 1, None, 0, None),
            ("part.html", 2, 1, 1, "include"),
        ]

    def test_render_ids_restart_for_a_new_trace(self):
        self.engine.get_template("part.html").render(Context({}))
        self.engine.get_template("part.html").render(Context({}))
        assert [e["metadata"]["render_id"] for e in renders()] == [1, 2]
        assert [e["metadata"]["depth"] for e in renders()] == [0, 0]

        clear_trace_id(self.token)
        self.token = set_trace_id(generate_trace_id())
        clear_buffer()
        self.engine.get_template("part.html").render(Context({}))
        assert [e["metadata"]["render_id"] for e in renders()] == [1]


class TestTemplateErrorsAndContext(TemplateWatcherTestCase):
    def test_render_error_is_recorded_and_reraised(self):
        with pytest.raises(ValueError, match="boom"):
            self.engine.get_template("outer_boom.html").render(Context({"obj": Exploding()}))
        events = renders()
        assert [e["metadata"]["name"] for e in events] == ["outer_boom.html", "boom.html"]
        assert all(e["metadata"]["error"] == "ValueError: boom" for e in events)

    def test_render_after_an_error_is_top_level(self):
        with pytest.raises(ValueError):
            self.engine.get_template("outer_boom.html").render(Context({"obj": Exploding()}))
        clear_buffer()
        self.engine.get_template("part.html").render(Context({}))
        meta = renders()[0]["metadata"]
        assert (meta["depth"], meta["parent_id"]) == (0, None)

    def test_events_carry_trace_id(self):
        self.engine.get_template("part.html").render(Context({}))
        assert renders()[0]["trace_id"] == self.trace_id

    def test_no_events_without_active_trace(self):
        clear_trace_id(self.token)
        self.token = None
        self.engine.get_template("page.html").render(Context({}))
        assert renders() == []

    def test_render_through_sync_to_async_is_recorded(self):
        template = self.engine.get_template("part.html")

        async def run():
            return await sync_to_async(template.render)(Context({"title": "Hi"}))

        assert async_to_sync(run)() == "<p>Hi</p>"
        [event] = renders()
        assert event["trace_id"] == self.trace_id
