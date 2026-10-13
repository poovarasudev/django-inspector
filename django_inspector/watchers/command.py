"""
Command Watcher — turns a chosen management command's run into a trace, with
its SQL, log and exception events attached and a command.completed anchor
(name, args, options, bounded stdout/stderr, duration, exit code).

Patches BaseCommand.execute on the class, so it covers manage.py,
django-admin and call_command alike (there is only ever one execute() to
patch, unlike run_from_argv which call_command bypasses). Opt-in: a command
runs untraced unless its name is listed in COMMAND_WATCH_LIST, and a handful
of names in NEVER_TRACED are never traced even when listed.

Requirements: CMD-01..03
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from django_inspector.tracing.api import _Run
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")

MAX_OUTPUT_CHARS = 8192      # characters of stdout/stderr captured, each stream
MAX_ARG_LENGTH = 500          # characters kept per repr-bounded arg/option value
FLUSH_CHUNK_SIZE = 500        # events flushed per chunk; a chunked trace is always kept

# Never traced even when listed in COMMAND_WATCH_LIST: the dashboard's own
# cleanup, the dev server and shell commands, and the test runner (which
# would otherwise trace the whole test suite through itself).
NEVER_TRACED = frozenset({
    "runserver", "runserver_plus", "testserver", "test", "shell", "shell_plus",
    "dbshell", "migrate", "inspector_cleanup",
})

_PATCH_MARKER = "_inspector_command_patch"
_JSON_SCALARS = (str, int, float, bool, type(None))


class CommandWatcher(BaseWatcher):
    """
    Patches BaseCommand.execute to wrap a listed command's run in a
    command.completed trace.

    - CMD-01: name, args, options, bounded stdout/stderr, duration, exit code
    - CMD-02: opt-in via COMMAND_WATCH_LIST; NEVER_TRACED names are skipped
    - CMD-03: chunked flush and bounded query stats (storage/flush.py, watchers/sql.py)
    """

    watcher_name = "command"

    def __init__(self):
        super().__init__()
        self._original_execute = None

    def install_hooks(self):
        current = BaseCommand.__dict__.get("execute")
        if getattr(current, _PATCH_MARKER, False):
            return  # another instance already patched it; never double-patch

        original = BaseCommand.execute

        def _traced_execute(cmd_self, *args, **options):
            return _dispatch(cmd_self, original, args, options)

        setattr(_traced_execute, _PATCH_MARKER, True)
        BaseCommand.execute = _traced_execute
        self._original_execute = original

    def remove_hooks(self):
        if self._original_execute is None:
            return  # this instance never installed the patch
        BaseCommand.execute = self._original_execute
        self._original_execute = None


def _dispatch(cmd_self, original, args, options):
    from django_inspector.conf import inspector_settings
    from django_inspector.watchers import registry

    watcher = registry.get_instance("command")
    if watcher is None or not watcher.is_enabled or not inspector_settings.is_enabled:
        return original(cmd_self, *args, **options)

    name = _command_name(cmd_self)
    watch_list = inspector_settings.COMMAND_WATCH_LIST or []
    if name in NEVER_TRACED or name not in watch_list:
        return original(cmd_self, *args, **options)

    return _traced_call(cmd_self, original, args, options, name)


def _traced_call(cmd_self, original, args, options, name):
    from django_inspector.conf import inspector_settings

    try:
        options = dict(options)
        real_stdout = options.get("stdout") or getattr(cmd_self.stdout, "_out", None)
        real_stderr = options.get("stderr") or getattr(cmd_self.stderr, "_out", None)
        tee_out = _BoundedTee(real_stdout)
        tee_err = _BoundedTee(real_stderr)
        options["stdout"] = tee_out
        options["stderr"] = tee_err
        run = _Run(
            name, anchor_type="command.completed",
            describe=_make_describe(name, args, options, tee_out, tee_err),
            record_when_joined=True, chunk_size=FLUSH_CHUNK_SIZE,
        )
        run.enter()
    except Exception:
        if inspector_settings.INSPECTOR_RAISE_ERRORS:
            raise
        logger.warning("inspector: failed to start tracing command %r", name, exc_info=True)
        return original(cmd_self, *args, **options)

    try:
        result = original(cmd_self, *args, **options)
    except BaseException as exc:
        run.exit(type(exc), exc, exc.__traceback__)
        raise
    else:
        run.exit(None, None, None)
        return result


def _make_describe(name, args, options, tee_out, tee_err):
    def describe(exc_type, exc_value):
        exit_code = _exit_code(exc_type, exc_value)
        extra = {
            "command": name,
            "args": [_bounded_repr(a) for a in args],
            "options": _safe_options(options),
            "stdout": tee_out.getvalue(),
            "stdout_truncated": tee_out.truncated,
            "stderr": tee_err.getvalue(),
            "stderr_truncated": tee_err.truncated,
            "exit_code": exit_code,
        }
        return exit_code != 0, extra
    return describe


def _exit_code(exc_type, exc_value):
    if exc_type is None:
        return 0
    if issubclass(exc_type, CommandError):
        return getattr(exc_value, "returncode", 1)
    if issubclass(exc_type, SystemExit):
        code = exc_value.code
        if code is None:
            return 0
        if isinstance(code, int) and not isinstance(code, bool):
            return code
        return 1
    if issubclass(exc_type, KeyboardInterrupt):
        return 130
    return 1


def _command_name(command) -> str:
    """The last segment of the command class's __module__ — the name manage.py uses."""
    return type(command).__module__.rsplit(".", 1)[-1]


def _bounded_repr(value) -> str:
    text = repr(value)
    if len(text) > MAX_ARG_LENGTH:
        return text[:MAX_ARG_LENGTH] + "..."
    return text


def _is_json_friendly(value) -> bool:
    if isinstance(value, _JSON_SCALARS):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_friendly(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_friendly(v) for k, v in value.items())
    return False


def _safe_options(options: dict) -> dict:
    """options masked and bounded for the anchor: stdout/stderr and any _-prefixed key are dropped."""
    result = {}
    for key, value in options.items():
        if key in ("stdout", "stderr") or key.startswith("_"):
            continue
        result[key] = value if _is_json_friendly(value) else _bounded_repr(value)
    return result


class _BoundedTee:
    """
    Forwards every write() to the real stream unchanged, so the user still
    sees the command's full output, while keeping the first MAX_OUTPUT_CHARS
    characters in memory for the command's anchor. Every other attribute
    (flush, isatty, encoding, ...) is delegated to the real stream through
    __getattr__, so OutputWrapper styling works unchanged.
    """

    def __init__(self, real):
        self._real = real
        self._chunks = []
        self._length = 0
        self.truncated = False

    def write(self, s):
        self._real.write(s)
        if self.truncated:
            return
        remaining = MAX_OUTPUT_CHARS - self._length
        if remaining <= 0:
            self.truncated = True
        elif len(s) > remaining:
            self._chunks.append(s[:remaining])
            self._length += remaining
            self.truncated = True
        else:
            self._chunks.append(s)
            self._length += len(s)

    def getvalue(self) -> str:
        return "".join(self._chunks)

    def __getattr__(self, name):
        return getattr(self._real, name)


# Auto-register on import
register("command", CommandWatcher)
