"""
Logging Watcher — captures Python log records emitted during a request.

Installs one handler on the root logger at LOG_LEVEL_THRESHOLD (default
WARNING). Host logger levels are never changed, so only records that reach the
root logger are seen: loggers with propagate=False, and records below a
logger's own level, are not captured.

Requirements: LOG-01..LOG-06
"""

import logging
from contextvars import ContextVar

from django_inspector.conf import inspector_settings
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register

logger = logging.getLogger("django_inspector")

MAX_MESSAGE_LENGTH = 8192
_INSPECTOR_LOGGER = "django_inspector"
_FORMATTER = logging.Formatter()

# True while this context is recording a log event, so anything logged while
# recording is never captured again.
_emitting = ContextVar("inspector_log_emitting", default=False)


class LogWatcher(BaseWatcher):
    """
    Captures log records via a root-logger handler.

    - LOG-01: handler installed at startup; captures records at the threshold or above
    - LOG-02: logger, level, message, file, line, traceback
    - LOG-03: trace correlation via BaseWatcher.record()
    - LOG-04: the django_inspector logger (and its children) is never captured
    - LOG-05: LOG_LEVEL_THRESHOLD (default WARNING)
    - LOG-06: handler removed on disable(); idempotent
    """

    watcher_name = "log"

    def __init__(self):
        super().__init__()
        self._handler = None

    def install_hooks(self):
        root = logging.getLogger()
        if any(isinstance(h, InspectorLogHandler) for h in root.handlers):
            return
        self._handler = InspectorLogHandler(self, _threshold())
        root.addHandler(self._handler)

    def remove_hooks(self):
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None


class InspectorLogHandler(logging.Handler):
    """Root-logger handler that turns log records into log.record events."""

    def __init__(self, watcher, level):
        super().__init__(level)
        self.watcher = watcher

    def emit(self, record):
        if _emitting.get() or not self.watcher.is_capturing():
            return
        if record.name == _INSPECTOR_LOGGER or record.name.startswith(_INSPECTOR_LOGGER + "."):
            return
        token = _emitting.set(True)
        try:
            self.watcher.record("log.record", _record_metadata(record))
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording log.record event", exc_info=True)
        finally:
            _emitting.reset(token)


def _record_metadata(record):
    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)
    metadata = {
        "logger": record.name,
        "level": record.levelno,
        "level_name": record.levelname,
        "message": message[:MAX_MESSAGE_LENGTH],
        "file": record.pathname,
        "line": record.lineno,
        "function": record.funcName,
    }
    if len(message) > MAX_MESSAGE_LENGTH:
        metadata["message_truncated"] = True
    if record.exc_info and record.exc_info[0] is not None:
        metadata["exception_type"] = record.exc_info[0].__name__
        metadata["traceback"] = _FORMATTER.formatException(record.exc_info)
    return metadata


def _threshold():
    """LOG_LEVEL_THRESHOLD as a level number; accepts a name or an int, falls back to WARNING."""
    value = inspector_settings.LOG_LEVEL_THRESHOLD
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    level = logging.getLevelName(str(value).upper())
    if isinstance(level, int):
        return level
    logger.warning("inspector: invalid LOG_LEVEL_THRESHOLD %r; using WARNING", value)
    return logging.WARNING


register("log", LogWatcher)
