"""
Template Watcher — captures each Django template render with name, source
path, loader, render time, context size and its place in the render tree.

Wraps django.template.base.Template._render, which runs for top-level renders,
{% include %}, inclusion tags and {% extends %} parents alike. Django template
language only; Jinja2 is not covered (TMPL-05).

Requirements: TMPL-01..TMPL-05
"""

import functools
import logging
import time
from contextvars import ContextVar

from django.template.base import Template
from django.template.loader_tags import ExtendsNode

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms

logger = logging.getLogger("django_inspector")

MAX_CONTEXT_KEYS = 50
_BUILTIN_CONTEXT_KEYS = frozenset({"True", "False", "None"})

# Render-tree state for the trace rendering in this context:
# {"trace_id": str, "next_id": int, "stack": [(render_id, template), ...]}.
# Replaced with a fresh state whenever the trace id changes.
_render_state = ContextVar("inspector_template_render_state", default=None)


class TemplateWatcher(BaseWatcher):
    """
    Captures template renders by wrapping Template._render.

    - TMPL-01: name, source path, render duration
    - TMPL-02: parent/child relationship (render_id, parent_id, depth, relation)
    - TMPL-03: context size (key count + key names, never values)
    - TMPL-04: trace correlation via BaseWatcher.record()
    - TMPL-05: Django template language only
    """

    watcher_name = "template"

    def __init__(self):
        super().__init__()
        self._original_render = None

    def install_hooks(self):
        current = Template._render
        if getattr(current, "_inspector_wrapped", False):
            return
        self._original_render = current
        Template._render = _make_render_wrapper(self, current)

    def remove_hooks(self):
        if self._original_render is not None:
            Template._render = self._original_render
            self._original_render = None

    def record_render(self, template, context, render_id, parent, depth, duration_ms, error):
        """Build and record one template.rendered event. Never raises unless INSPECTOR_RAISE_ERRORS."""
        try:
            parent_id, parent_template = parent if parent is not None else (None, None)
            origin = getattr(template, "origin", None)
            loader = getattr(origin, "loader", None)
            keys = _context_keys(context)
            metadata = {
                "render_id": render_id,
                "parent_id": parent_id,
                "depth": depth,
                "relation": _relation(parent_template),
                "name": template.name,
                "origin_path": getattr(origin, "name", None),
                "loader": (
                    type(loader).__module__ + "." + type(loader).__qualname__
                    if loader is not None
                    else None
                ),
                "duration_ms": duration_ms,
                "context_key_count": len(keys),
                "context_keys": keys[:MAX_CONTEXT_KEYS],
            }
            if error is not None:
                metadata["error"] = "%s: %s" % (type(error).__name__, error)
            self.record("template.rendered", metadata)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording template.rendered event", exc_info=True)


def _make_render_wrapper(watcher, original):
    @functools.wraps(original)
    def wrapper(template, context):
        if not watcher.is_capturing():
            return original(template, context)
        trace_id = get_current_trace_id()

        try:
            state = _state_for(trace_id)
            render_id = state["next_id"]
            state["next_id"] += 1
            parent = state["stack"][-1] if state["stack"] else None
            depth = len(state["stack"])
            state["stack"].append((render_id, template))
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error preparing template.rendered event", exc_info=True)
            return original(template, context)

        start = time.monotonic()
        try:
            output = original(template, context)
        except Exception as exc:
            state["stack"].pop()
            watcher.record_render(template, context, render_id, parent, depth, elapsed_ms(start), exc)
            raise
        except BaseException:
            state["stack"].pop()
            raise
        state["stack"].pop()
        watcher.record_render(template, context, render_id, parent, depth, elapsed_ms(start), None)
        return output

    wrapper._inspector_wrapped = True
    return wrapper


def _state_for(trace_id):
    state = _render_state.get()
    if state is None or state["trace_id"] != trace_id:
        state = {"trace_id": trace_id, "next_id": 1, "stack": []}
        _render_state.set(state)
    return state


def _relation(parent_template):
    """
    None at top level. "extends" when the parent starts with {% extends %}:
    an extending template renders nothing directly except its base.
    Otherwise "include" ({% include %} or an inclusion tag).
    """
    if parent_template is None:
        return None
    nodelist = getattr(parent_template, "nodelist", None) or []
    if any(isinstance(node, ExtendsNode) for node in nodelist):
        return "extends"
    return "include"


def _context_keys(context):
    """Sorted variable names visible to the template, minus the True/False/None builtins."""
    try:
        flat = context.flatten()
    except Exception:
        return []
    return sorted(str(k) for k in flat if k not in _BUILTIN_CONTEXT_KEYS)


register("template", TemplateWatcher)
