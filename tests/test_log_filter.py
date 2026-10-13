"""
Tests for TraceIdFilter (TRACE-06).
"""

import logging
import logging.config

from django_inspector.tracing import TraceIdFilter
from django_inspector.tracing.context import clear_trace_id, set_trace_id


def _make_record():
    return logging.LogRecord("app", logging.WARNING, __file__, 1, "hi", None, None)


class TestTraceIdFilter:
    def test_sets_the_trace_id_inside_a_trace(self):
        token = set_trace_id("abc123")
        try:
            record = _make_record()
            assert TraceIdFilter().filter(record) is True
            assert record.inspector_trace_id == "abc123"
        finally:
            clear_trace_id(token)

    def test_sets_a_dash_outside_a_trace(self):
        record = _make_record()
        assert TraceIdFilter().filter(record) is True
        assert record.inspector_trace_id == "-"

    def test_always_returns_true(self):
        assert TraceIdFilter().filter(_make_record()) is True


class TestTraceIdFilterViaDictConfig:
    def test_works_through_logging_config_dictconfig(self):
        logger = logging.getLogger("inspector.test.trace_id_filter")
        records = []

        class _CollectingHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logging.config.dictConfig({
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"trace_id": {"()": "django_inspector.tracing.TraceIdFilter"}},
            "handlers": {
                "collect": {
                    "()": lambda: _CollectingHandler(),
                    "filters": ["trace_id"],
                },
            },
            "loggers": {
                "inspector.test.trace_id_filter": {
                    "handlers": ["collect"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        })

        token = set_trace_id("via-dictconfig")
        try:
            logger.warning("hello")
        finally:
            clear_trace_id(token)

        assert len(records) == 1
        assert records[0].inspector_trace_id == "via-dictconfig"
