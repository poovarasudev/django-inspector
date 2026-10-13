"""Tests for derived Event columns (SCHEMA-01, SCHEMA-02)."""

from django_inspector.storage.columns import derive_columns, exception_fingerprint, sql_fingerprint


class TestGenericDurationStep:
    def test_duration_ms_from_duration_ms(self):
        columns = derive_columns("sql.query", {"duration_ms": 12.5, "db_alias": "default", "sql": "SELECT 1"})
        assert columns["duration_ms"] == 12.5

    def test_duration_ms_from_latency_ms_when_duration_ms_absent(self):
        columns = derive_columns("request.completed", {"latency_ms": 42.0})
        assert columns["duration_ms"] == 42.0

    def test_duration_ms_prefers_duration_ms_over_latency_ms(self):
        columns = derive_columns("unknown.event", {"duration_ms": 1.0, "latency_ms": 2.0})
        assert columns["duration_ms"] == 1.0

    def test_no_duration_at_all_omits_the_column(self):
        columns = derive_columns("unknown.event", {})
        assert "duration_ms" not in columns


class TestUnknownEventTypes:
    def test_unknown_event_type_gets_only_the_generic_step(self):
        columns = derive_columns("task.completed", {"latency_ms": 5, "method": "GET"})
        assert columns == {"duration_ms": 5}


class TestRequestCompletedExtractor:
    def test_fills_method_path_status_code(self):
        columns = derive_columns(
            "request.completed",
            {"method": "POST", "path": "/orders/", "status_code": 201, "latency_ms": 8.2},
        )
        assert columns["method"] == "POST"
        assert columns["path"] == "/orders/"
        assert columns["status_code"] == 201
        assert columns["duration_ms"] == 8.2


class TestSqlQueryExtractor:
    def test_fills_fingerprint(self):
        columns = derive_columns(
            "sql.query", {"sql": "SELECT * FROM users WHERE id = 1", "db_alias": "default"}
        )
        assert len(columns["fingerprint"]) == 16

    def test_no_method_or_path_columns(self):
        columns = derive_columns(
            "sql.query", {"sql": "SELECT 1", "db_alias": "default"}
        )
        assert "method" not in columns
        assert "path" not in columns


class TestExceptionRaisedExtractor:
    def _frames(self):
        return [
            {"file": "/app/views.py", "line": 10, "function": "view"},
            {"file": "/app/services.py", "line": 20, "function": "do_thing"},
        ]

    def test_fills_fingerprint(self):
        columns = derive_columns(
            "exception.raised",
            {"exception_type": "app.errors.BadThing", "frames": self._frames()},
        )
        assert len(columns["fingerprint"]) == 16

    def test_method_and_path_from_request_fields_when_present(self):
        columns = derive_columns(
            "exception.raised",
            {
                "exception_type": "app.errors.BadThing",
                "frames": self._frames(),
                "request_method": "GET",
                "request_path": "/users/1/",
            },
        )
        assert columns["method"] == "GET"
        assert columns["path"] == "/users/1/"

    def test_no_method_or_path_when_absent(self):
        columns = derive_columns(
            "exception.raised",
            {"exception_type": "app.errors.BadThing", "frames": self._frames()},
        )
        assert "method" not in columns
        assert "path" not in columns

    def test_a_jwt_in_the_request_path_appears_masked_in_the_path_column(self):
        # derive_columns only ever sees the already-masked metadata that
        # record() buffered; it must not do any unmasking of its own.
        columns = derive_columns(
            "exception.raised",
            {
                "exception_type": "app.errors.BadThing",
                "frames": self._frames(),
                "request_method": "GET",
                "request_path": "/auth/***REDACTED***/",
            },
        )
        assert columns["path"] == "/auth/***REDACTED***/"


class TestCoercion:
    def test_bool_status_code_is_dropped(self):
        columns = derive_columns("request.completed", {"status_code": True})
        assert "status_code" not in columns

    def test_out_of_range_status_code_is_dropped(self):
        columns = derive_columns("request.completed", {"status_code": 999})
        assert "status_code" not in columns

    def test_status_code_in_range_is_kept(self):
        columns = derive_columns("request.completed", {"status_code": 100})
        assert columns["status_code"] == 100
        columns = derive_columns("request.completed", {"status_code": 599})
        assert columns["status_code"] == 599

    def test_nan_duration_is_dropped(self):
        columns = derive_columns("unknown.event", {"duration_ms": float("nan")})
        assert "duration_ms" not in columns

    def test_infinite_duration_is_dropped(self):
        columns = derive_columns("unknown.event", {"duration_ms": float("inf")})
        assert "duration_ms" not in columns

    def test_negative_duration_is_dropped(self):
        columns = derive_columns("unknown.event", {"duration_ms": -1.0})
        assert "duration_ms" not in columns

    def test_bool_duration_is_dropped(self):
        columns = derive_columns("unknown.event", {"duration_ms": True})
        assert "duration_ms" not in columns

    def test_zero_duration_is_kept(self):
        columns = derive_columns("unknown.event", {"duration_ms": 0})
        assert columns["duration_ms"] == 0

    def test_over_long_method_is_truncated(self):
        columns = derive_columns("request.completed", {"method": "X" * 40})
        assert columns["method"] == "X" * 16

    def test_over_long_path_is_truncated(self):
        columns = derive_columns("request.completed", {"path": "/x/" * 500})
        assert len(columns["path"]) == 1024

    def test_non_str_method_is_dropped(self):
        columns = derive_columns("request.completed", {"method": 123})
        assert "method" not in columns

    def test_non_str_path_is_dropped(self):
        columns = derive_columns("request.completed", {"path": ["a", "b"]})
        assert "path" not in columns


class TestHttpRequestExtractor:
    def _metadata(self, **extra):
        data = {
            "library": "requests", "method": "GET", "url": "https://api.example.com/users/1",
            "host": "api.example.com", "status_code": 200, "duration_ms": 12.0,
        }
        data.update(extra)
        return data

    def test_fills_method_status_code_and_path(self):
        columns = derive_columns("http.request", self._metadata())
        assert columns["method"] == "GET"
        assert columns["status_code"] == 200
        assert columns["path"] == "https://api.example.com/users/1"

    def test_path_is_truncated_to_1024(self):
        long_url = "https://api.example.com/" + ("x" * 2000)
        columns = derive_columns("http.request", self._metadata(url=long_url))
        assert len(columns["path"]) == 1024

    def test_no_url_omits_path_and_fingerprint(self):
        columns = derive_columns("http.request", {"method": "GET", "status_code": 200})
        assert "path" not in columns
        assert "fingerprint" not in columns

    def test_fingerprint_is_16_hex_characters(self):
        columns = derive_columns("http.request", self._metadata())
        assert len(columns["fingerprint"]) == 16
        int(columns["fingerprint"], 16)

    def test_digit_runs_group_together(self):
        a = derive_columns("http.request", self._metadata(url="https://api.example.com/users/1"))
        b = derive_columns("http.request", self._metadata(url="https://api.example.com/users/2"))
        assert a["fingerprint"] == b["fingerprint"]

    def test_uuid_segments_group_together(self):
        a = derive_columns(
            "http.request",
            self._metadata(url="https://api.example.com/orders/550e8400-e29b-41d4-a716-446655440000"),
        )
        b = derive_columns(
            "http.request",
            self._metadata(url="https://api.example.com/orders/6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
        )
        assert a["fingerprint"] == b["fingerprint"]

    def test_different_paths_differ(self):
        a = derive_columns("http.request", self._metadata(url="https://api.example.com/users/1"))
        b = derive_columns("http.request", self._metadata(url="https://api.example.com/orders/1"))
        assert a["fingerprint"] != b["fingerprint"]

    def test_different_hosts_differ(self):
        a = derive_columns("http.request", self._metadata(url="https://a.example.com/x/1", host="a.example.com"))
        b = derive_columns("http.request", self._metadata(url="https://b.example.com/x/1", host="b.example.com"))
        assert a["fingerprint"] != b["fingerprint"]

    def test_different_methods_differ(self):
        a = derive_columns("http.request", self._metadata(method="GET"))
        b = derive_columns("http.request", self._metadata(method="POST"))
        assert a["fingerprint"] != b["fingerprint"]

    def test_duration_ms_is_filled_by_the_generic_step(self):
        columns = derive_columns("http.request", self._metadata(duration_ms=42.5))
        assert columns["duration_ms"] == 42.5


class TestSqlFingerprint:
    def test_equal_across_literal_values(self):
        a = sql_fingerprint("SELECT * FROM users WHERE id = 1", "default")
        b = sql_fingerprint("SELECT * FROM users WHERE id = 2", "default")
        assert a == b

    def test_different_across_db_alias(self):
        a = sql_fingerprint("SELECT * FROM users WHERE id = 1", "default")
        b = sql_fingerprint("SELECT * FROM users WHERE id = 1", "replica")
        assert a != b

    def test_different_across_query_shapes(self):
        a = sql_fingerprint("SELECT * FROM users WHERE id = 1", "default")
        b = sql_fingerprint("SELECT * FROM orders WHERE id = 1", "default")
        assert a != b

    def test_returns_16_hex_characters(self):
        fp = sql_fingerprint("SELECT 1", "default")
        assert len(fp) == 16
        int(fp, 16)  # raises ValueError if not hex


class TestExceptionFingerprint:
    def _frames(self, file="/app/views.py", function="view"):
        return [{"file": file, "line": 10, "function": function}]

    def test_equal_across_line_numbers(self):
        a = exception_fingerprint("app.errors.BadThing", [{"file": "/app/x.py", "line": 1, "function": "f"}])
        b = exception_fingerprint("app.errors.BadThing", [{"file": "/app/x.py", "line": 99, "function": "f"}])
        assert a == b

    def test_different_across_exception_types(self):
        frames = self._frames()
        a = exception_fingerprint("app.errors.BadThing", frames)
        b = exception_fingerprint("app.errors.OtherThing", frames)
        assert a != b

    def test_different_across_functions(self):
        a = exception_fingerprint("app.errors.BadThing", self._frames(function="view"))
        b = exception_fingerprint("app.errors.BadThing", self._frames(function="other"))
        assert a != b

    def test_picks_innermost_non_library_frame(self):
        import django

        frames = [
            {"file": "/app/views.py", "line": 1, "function": "view"},
            {"file": django.__file__, "line": 2, "function": "get_response"},
        ]
        a = exception_fingerprint("app.errors.BadThing", frames)
        b = exception_fingerprint("app.errors.BadThing", [frames[0]])
        assert a == b

    def test_all_library_frames_falls_back_to_innermost(self):
        import django

        frames = [
            {"file": django.__file__, "line": 1, "function": "outer"},
            {"file": django.__file__, "line": 2, "function": "inner"},
        ]
        fp = exception_fingerprint("app.errors.BadThing", frames)
        assert fp == exception_fingerprint("app.errors.BadThing", [frames[-1]])

    def test_no_frames_covers_the_type_alone(self):
        fp = exception_fingerprint("app.errors.BadThing", [])
        assert len(fp) == 16
        assert fp == exception_fingerprint("app.errors.BadThing", [])

    def test_no_frames_differs_from_frames_present(self):
        assert exception_fingerprint("app.errors.BadThing", []) != exception_fingerprint(
            "app.errors.BadThing", self._frames()
        )

    def test_returns_16_hex_characters(self):
        fp = exception_fingerprint("app.errors.BadThing", self._frames())
        assert len(fp) == 16
        int(fp, 16)
