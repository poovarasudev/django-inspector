"""
TraceIdFilter — a logging.Filter that stamps records with the current trace
id, so a log line can be matched to the trace it happened in.

Requirements: TRACE-06
"""

import logging

from django_inspector.tracing.context import get_current_trace_id


class TraceIdFilter(logging.Filter):
    """
    Sets ``record.inspector_trace_id`` to the current trace id, or ``"-"``
    outside a trace. Always returns True: a filter that drops records would
    silently break the host's other logging.
    """

    def filter(self, record):
        record.inspector_trace_id = get_current_trace_id() or "-"
        return True
