"""
Derives an Event's real columns (method, status_code, path, duration_ms,
fingerprint) from its already-masked metadata, at flush time.

Watchers don't change: they only ever call ``record()`` with metadata dicts,
which ``mask_metadata`` scrubs before it reaches the buffer. Extractors here
read that same masked metadata, so a column can never hold something the
masking removed (SCHEMA-01, SCHEMA-02).
"""

import hashlib
import math
import re
import sys
from functools import lru_cache
from urllib.parse import urlsplit

from django_inspector.watchers.utils import is_library_file

MAX_METHOD_LENGTH = 16
MAX_PATH_LENGTH = 1024
FINGERPRINT_LENGTH = 16

# hashlib's usedforsecurity kwarg only exists on Python >= 3.9.
_SHA1_SUPPORTS_USEDFORSECURITY = sys.version_info >= (3, 9)


def _sha1(data: bytes):
    if _SHA1_SUPPORTS_USEDFORSECURITY:
        return hashlib.sha1(data, usedforsecurity=False)
    return hashlib.sha1(data)


_QUOTED = re.compile(r"'[^']*'")
_NUMBER = re.compile(r"\b\d+\b")


@lru_cache(maxsize=2048)
def normalize_sql(sql: str) -> str:
    """
    Normalize SQL by replacing literal values with placeholders, so queries
    of the same shape compare equal. Used for N+1 detection and fingerprints.
    """
    normalized = _QUOTED.sub("'?'", sql)
    normalized = _NUMBER.sub("?", normalized)
    return normalized.replace("%s", "?").strip()


def sql_fingerprint(sql: str, db_alias: str) -> str:
    """First 16 hex characters of SHA-1 over "<db_alias>\\n<normalised SQL>"."""
    normalized = normalize_sql(sql)
    digest = _sha1(("%s\n%s" % (db_alias, normalized)).encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def innermost_app_frame(frames):
    """
    The innermost frame whose file isn't library code, or the innermost frame
    if every one of them is. ``frames`` is ordered outermost to innermost.

    Used both for the exception fingerprint and to show an Issues page
    exception group the same frame it was grouped by (DASH-04).
    """
    for frame in reversed(frames):
        file = frame.get("file")
        if file and not is_library_file(file):
            return frame
    return frames[-1]


_innermost_app_frame = innermost_app_frame  # old name kept as an alias


def exception_fingerprint(exception_type: str, frames: list) -> str:
    """
    First 16 hex characters of SHA-1 over
    "<exception type>\\n<file>\\n<function>" of the innermost application
    frame (no line number, so it survives unrelated edits). With no frames,
    the fingerprint covers the type alone.
    """
    file = function = ""
    if frames:
        frame = innermost_app_frame(frames)
        file = frame.get("file") or ""
        function = frame.get("function") or ""
    digest = _sha1(("%s\n%s\n%s" % (exception_type, file, function)).encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def _coerce_method(value):
    if not isinstance(value, str):
        return None
    return value[:MAX_METHOD_LENGTH]


def _coerce_path(value):
    if not isinstance(value, str):
        return None
    return value[:MAX_PATH_LENGTH]


def _coerce_status_code(value):
    # bool is an int subclass; excluded explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 100 <= value <= 599:
        return value
    return None


def _coerce_duration_ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


_COERCERS = {
    "method": _coerce_method,
    "path": _coerce_path,
    "status_code": _coerce_status_code,
    "duration_ms": _coerce_duration_ms,
}


def _request_completed_columns(metadata):
    return {
        "method": metadata.get("method"),
        "path": metadata.get("path"),
        "status_code": metadata.get("status_code"),
    }


def _sql_query_columns(metadata):
    sql = metadata.get("sql")
    db_alias = metadata.get("db_alias")
    if sql is None or db_alias is None:
        return {}
    return {"fingerprint": sql_fingerprint(sql, db_alias)}


_URL_UUID_SEGMENT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_URL_DIGIT_RUN = re.compile(r"\d+")


def _url_path_template(path: str) -> str:
    """``path`` with UUID-like segments and digit runs replaced by '?', so
    calls to one endpoint (e.g. /users/1 and /users/2) group together."""
    templated = _URL_UUID_SEGMENT.sub("?", path)
    return _URL_DIGIT_RUN.sub("?", templated)


def _http_request_columns(metadata):
    columns = {}
    method = metadata.get("method")
    if method is not None:
        columns["method"] = method
    status_code = metadata.get("status_code")
    if status_code is not None:
        columns["status_code"] = status_code
    url = metadata.get("url")
    if url is None:
        return columns
    columns["path"] = url
    try:
        template = _url_path_template(urlsplit(url).path)
    except Exception:
        template = ""
    digest = _sha1(("%s\n%s\n%s" % ((method or "").upper(), metadata.get("host") or "", template)).encode("utf-8"))
    columns["fingerprint"] = digest.hexdigest()[:FINGERPRINT_LENGTH]
    return columns


def _exception_raised_columns(metadata):
    columns = {}
    exception_type = metadata.get("exception_type")
    if exception_type is not None:
        columns["fingerprint"] = exception_fingerprint(exception_type, metadata.get("frames") or [])
    if metadata.get("request_method") is not None:
        columns["method"] = metadata.get("request_method")
    if metadata.get("request_path") is not None:
        columns["path"] = metadata.get("request_path")
    return columns


# Event types without an entry here get only the generic duration_ms step:
# future watchers get a duration column without extra work.
_EXTRACTORS = {
    "request.completed": _request_completed_columns,
    "sql.query": _sql_query_columns,
    "exception.raised": _exception_raised_columns,
    "http.request": _http_request_columns,
}


def derive_columns(event_type: str, metadata: dict) -> dict:
    """
    Derive the real ``Event`` columns for one event. Returns only the columns
    that could be filled; a value that fails coercion is left out, leaving
    that column NULL.
    """
    candidates = {}

    duration = metadata.get("duration_ms")
    if duration is None:
        duration = metadata.get("latency_ms")
    if duration is not None:
        candidates["duration_ms"] = duration

    extractor = _EXTRACTORS.get(event_type)
    if extractor is not None:
        candidates.update(extractor(metadata))

    columns = {}
    for key, value in candidates.items():
        if value is None:
            continue
        coercer = _COERCERS.get(key)
        coerced = coercer(value) if coercer else value
        if coerced is not None:
            columns[key] = coerced
    return columns
