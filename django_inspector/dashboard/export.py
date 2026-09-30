"""
CSV export of dashboard lists (DASH-03).

Each list view checks ``?format=csv`` after applying its filters and before
paginating, and returns ``csv_response(qs, COLUMNS, filename)`` instead of
rendering the page. The data is already masked, since it's read straight
from stored masked metadata — CSV export does no masking of its own.
"""

import csv

from django.http import StreamingHttpResponse

CSV_MAX_ROWS = 10000

# A cell starting with one of these is prefixed with ' to stop a spreadsheet
# from treating it as a formula (or, for tab/CR, splitting/merging cells).
_UNSAFE_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class _Echo:
    """A file-like object whose write() returns its argument, so csv.writer can stream."""

    def write(self, value):
        return value


def _meta(key):
    return lambda event: event.metadata.get(key)


def _safe_cell(value):
    text = "" if value is None else str(value)
    if text.startswith(_UNSAFE_PREFIXES):
        return "'" + text
    return text


COMMON_COLUMNS = [
    ("timestamp", lambda event: event.timestamp.isoformat()),
    ("trace_id", lambda event: event.trace_id),
    ("event_type", lambda event: event.event_type),
    ("duration_ms", lambda event: event.duration_ms),
]

REQUEST_COLUMNS = COMMON_COLUMNS + [
    ("method", _meta("method")),
    ("path", _meta("path")),
    ("status_code", _meta("status_code")),
    ("user", _meta("user")),
]

QUERY_COLUMNS = COMMON_COLUMNS + [
    ("sql", _meta("sql")),
    ("db_alias", _meta("db_alias")),
    ("is_slow", _meta("is_slow")),
    ("n_plus_one", _meta("n_plus_one")),
    ("is_duplicate", _meta("is_duplicate")),
    ("fingerprint", lambda event: event.fingerprint),
]

EXCEPTION_COLUMNS = COMMON_COLUMNS + [
    ("exception_type", _meta("exception_type")),
    ("exception_message", _meta("exception_message")),
    ("path", _meta("request_path")),
    ("fingerprint", lambda event: event.fingerprint),
]

CACHE_COLUMNS = COMMON_COLUMNS + [
    ("operation", _meta("operation")),
    ("key", _meta("key")),
    ("hit", _meta("hit")),
    ("backend", _meta("backend")),
]

TEMPLATE_COLUMNS = COMMON_COLUMNS + [
    ("name", _meta("name")),
    ("path", _meta("origin_path")),
]

LOG_COLUMNS = COMMON_COLUMNS + [
    ("level", _meta("level_name")),
    ("logger", _meta("logger")),
    ("message", _meta("message")),
]

SIGNAL_COLUMNS = COMMON_COLUMNS + [
    ("signal", _meta("signal")),
    ("sender", _meta("sender")),
    ("receiver_count", _meta("receiver_count")),
]

# event_type already appears in COMMON_COLUMNS; not repeated here.
TRACE_COLUMNS = COMMON_COLUMNS + [
    ("name", _meta("name")),
    ("status", _meta("status")),
]


def _rows(qs, columns):
    yield [header for header, _ in columns]
    for event in qs:
        yield [_safe_cell(getter(event)) for _, getter in columns]


def csv_response(qs, columns, filename):
    """
    Stream ``qs`` (at most ``CSV_MAX_ROWS`` rows) as a ``text/csv`` download.
    ``columns`` is a list of ``(header, getter)`` pairs, ``getter`` a callable
    taking an ``Event`` and returning that column's value.
    """
    writer = csv.writer(_Echo())
    response = StreamingHttpResponse(
        (writer.writerow(row) for row in _rows(qs[:CSV_MAX_ROWS], columns)),
        content_type="text/csv",
    )
    response["Content-Disposition"] = 'attachment; filename="%s"' % filename
    return response
