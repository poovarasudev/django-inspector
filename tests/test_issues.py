"""Tests for the Issues page aggregates (DASH-04)."""

import datetime

from django.test import TestCase
from django.utils import timezone

from django_inspector.dashboard import issues
from django_inspector.dashboard.issues import endpoint_stats, exception_groups, query_shape_groups
from django_inspector.storage.columns import exception_fingerprint, normalize_sql, sql_fingerprint
from django_inspector.storage.models import Event


def _tid(n):
    return ("t%s" % n).ljust(32, "0")


def _since(hours=1):
    return timezone.now() - datetime.timedelta(hours=hours)


def exception_event(exc_type="app.errors.Boom", file="/app/views.py", function="view",
                     message="boom", trace_id=None, when=None, **extra):
    trace_id = trace_id or _tid(1)
    frames = [{"file": file, "line": 10, "function": function}]
    metadata = {"exception_type": exc_type, "exception_message": message, "frames": frames}
    metadata.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="exception.raised", metadata=metadata,
        fingerprint=exception_fingerprint(exc_type, frames),
        timestamp=when or timezone.now(),
    )


def sql_event(sql="SELECT 1", db_alias="default", duration_ms=5.0, origin_file="/app/views.py",
              origin_line=10, trace_id=None, when=None, **extra):
    trace_id = trace_id or _tid(1)
    metadata = {
        "sql": sql, "db_alias": db_alias, "duration_ms": duration_ms,
        "origin_file": origin_file, "origin_line": origin_line,
    }
    metadata.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="sql.query", metadata=metadata,
        fingerprint=sql_fingerprint(sql, db_alias), duration_ms=duration_ms,
        timestamp=when or timezone.now(),
    )


def request_event(method="GET", path="/x/", route=None, status_code=200, duration_ms=10.0,
                   trace_id=None, when=None, **extra):
    trace_id = trace_id or _tid(1)
    metadata = {
        "method": method, "path": path, "route": route, "status_code": status_code,
        "latency_ms": duration_ms,
    }
    metadata.update(extra)
    return Event.objects.create(
        trace_id=trace_id, event_type="request.completed", metadata=metadata,
        method=method, path=path, status_code=status_code, duration_ms=duration_ms,
        timestamp=when or timezone.now(),
    )


class TestExceptionGroups(TestCase):
    def test_groups_by_fingerprint_with_counts_and_seen_times(self):
        t1 = timezone.now() - datetime.timedelta(minutes=10)
        t2 = timezone.now() - datetime.timedelta(minutes=5)
        exception_event(when=t1, trace_id=_tid(1))
        example = exception_event(when=t2, trace_id=_tid(2))
        exception_event(exc_type="app.errors.Other", function="other", trace_id=_tid(3))

        groups, capped = exception_groups(_since())
        assert capped is False
        boom = next(g for g in groups if g["exception_type"] == "app.errors.Boom")
        assert boom["count"] == 2
        assert boom["affected_traces"] == 2
        assert boom["first_seen"] == t1
        assert boom["last_seen"] == t2
        assert boom["example_id"] == example.pk
        assert boom["exception_message"] == "boom"
        assert boom["frame"] == "/app/views.py:view"

    def test_orders_by_count_then_last_seen(self):
        exception_event(exc_type="app.errors.Rare", trace_id=_tid(1))
        for i in range(3):
            exception_event(exc_type="app.errors.Common", trace_id=_tid(10 + i))

        groups, _ = exception_groups(_since())
        assert [g["exception_type"] for g in groups] == ["app.errors.Common", "app.errors.Rare"]

    def test_frame_falls_back_to_innermost_when_all_frames_are_library_code(self):
        import django

        exception_event(file=django.__file__, function="get_response")
        groups, _ = exception_groups(_since())
        assert groups[0]["frame"] == "%s:get_response" % django.__file__

    def test_window_filter_excludes_older_events(self):
        old = timezone.now() - datetime.timedelta(hours=2)
        exception_event(when=old)
        groups, _ = exception_groups(_since())
        assert groups == []

    def test_null_fingerprint_is_skipped(self):
        Event.objects.create(
            trace_id=_tid(1), event_type="exception.raised", metadata={"exception_type": "X"}
        )
        groups, _ = exception_groups(_since())
        assert groups == []

    def test_only_exception_raised_events_are_counted(self):
        sql_event()
        groups, _ = exception_groups(_since())
        assert groups == []

    def test_scan_cap_bounds_rows_and_sets_the_flag(self):
        for i in range(5):
            exception_event(trace_id=_tid(i), when=timezone.now() - datetime.timedelta(seconds=i))
        original = issues.ISSUES_SCAN_LIMIT
        issues.ISSUES_SCAN_LIMIT = 3
        try:
            groups, capped = exception_groups(_since())
        finally:
            issues.ISSUES_SCAN_LIMIT = original
        assert capped is True
        assert sum(g["count"] for g in groups) == 3


class TestQueryShapeGroups(TestCase):
    def test_groups_by_fingerprint_with_nearest_rank_percentiles(self):
        for i, d in enumerate([10, 20, 30, 40]):
            sql_event(duration_ms=d, trace_id=_tid(i))

        groups, capped = query_shape_groups(_since())
        assert capped is False
        assert len(groups) == 1
        row = groups[0]
        assert row["count"] == 4
        assert row["affected_traces"] == 4
        assert row["p50"] == 20
        assert row["p95"] == 40

    def test_percentiles_with_a_single_value(self):
        sql_event(duration_ms=15)
        groups, _ = query_shape_groups(_since())
        assert groups[0]["p50"] == 15
        assert groups[0]["p95"] == 15

    def test_sql_is_normalised_and_truncated_to_300_chars(self):
        long_sql = "SELECT " + ", ".join("col%d" % i for i in range(100))
        sql_event(sql=long_sql)
        groups, _ = query_shape_groups(_since())
        expected = normalize_sql(long_sql)
        assert len(expected) > 300
        assert groups[0]["sql"] == expected[:300]

    def test_origin_is_the_most_common_for_the_shape(self):
        sql_event(origin_file="/app/a.py", origin_line=1, trace_id=_tid(1))
        sql_event(origin_file="/app/a.py", origin_line=1, trace_id=_tid(2))
        sql_event(origin_file="/app/b.py", origin_line=2, trace_id=_tid(3))
        groups, _ = query_shape_groups(_since())
        assert groups[0]["origin"] == "/app/a.py:1"

    def test_n_plus_one_traces_counts_distinct_traces(self):
        sql_event(n_plus_one=True, trace_id=_tid(1))
        sql_event(n_plus_one=True, trace_id=_tid(1))  # same trace: counted once
        sql_event(n_plus_one=True, trace_id=_tid(2))
        sql_event(n_plus_one=False, trace_id=_tid(3))
        groups, _ = query_shape_groups(_since())
        assert groups[0]["n_plus_one_traces"] == 2

    def test_n_plus_one_shapes_are_ordered_first_even_with_a_lower_count(self):
        for i in range(5):
            sql_event(sql="SELECT * FROM common", trace_id=_tid(i))
        sql_event(sql="SELECT * FROM rare", n_plus_one=True, trace_id=_tid(10))
        sql_event(sql="SELECT * FROM rare", n_plus_one=True, trace_id=_tid(11))
        sql_event(sql="SELECT * FROM rare", n_plus_one=True, trace_id=_tid(12))

        groups, _ = query_shape_groups(_since())
        assert groups[0]["sql"] == normalize_sql("SELECT * FROM rare")
        assert groups[0]["n_plus_one_traces"] == 3
        assert groups[1]["sql"] == normalize_sql("SELECT * FROM common")

    def test_window_filter_excludes_older_events(self):
        old = timezone.now() - datetime.timedelta(hours=2)
        sql_event(when=old)
        groups, _ = query_shape_groups(_since())
        assert groups == []

    def test_null_fingerprint_is_skipped(self):
        Event.objects.create(trace_id=_tid(1), event_type="sql.query", metadata={"sql": "SELECT 1"})
        groups, _ = query_shape_groups(_since())
        assert groups == []

    def test_scan_cap_bounds_rows_and_sets_the_flag(self):
        for i in range(5):
            sql_event(trace_id=_tid(i), when=timezone.now() - datetime.timedelta(seconds=i))
        original = issues.ISSUES_SCAN_LIMIT
        issues.ISSUES_SCAN_LIMIT = 3
        try:
            groups, capped = query_shape_groups(_since())
        finally:
            issues.ISSUES_SCAN_LIMIT = original
        assert capped is True
        assert sum(g["count"] for g in groups) == 3


class TestEndpointStats(TestCase):
    def test_groups_by_method_and_route_with_percentiles(self):
        for i, d in enumerate([100, 200, 300, 400]):
            request_event(route="orders/<int:pk>/", duration_ms=d, trace_id=_tid(i))

        stats, capped = endpoint_stats(_since())
        assert capped is False
        assert len(stats) == 1
        row = stats[0]
        assert row["method"] == "GET"
        assert row["endpoint"] == "orders/<int:pk>/"
        assert row["count"] == 4
        assert row["p50"] == 200
        assert row["p95"] == 400

    def test_falls_back_to_path_when_route_is_missing(self):
        request_event(route=None, path="/legacy/42/", trace_id=_tid(1))
        stats, _ = endpoint_stats(_since())
        assert stats[0]["endpoint"] == "/legacy/42/"

    def test_error_and_client_error_rates(self):
        request_event(route="x/", status_code=200, trace_id=_tid(1))
        request_event(route="x/", status_code=404, trace_id=_tid(2))
        request_event(route="x/", status_code=500, trace_id=_tid(3))
        request_event(route="x/", status_code=500, trace_id=_tid(4))

        stats, _ = endpoint_stats(_since())
        row = stats[0]
        assert row["error_rate"] == 0.5
        assert row["client_error_rate"] == 0.25

    def test_orders_by_p95_descending(self):
        request_event(route="fast/", duration_ms=10, trace_id=_tid(1))
        request_event(route="slow/", duration_ms=1000, trace_id=_tid(2))

        stats, _ = endpoint_stats(_since())
        assert [r["endpoint"] for r in stats] == ["slow/", "fast/"]

    def test_example_trace_id_is_the_slowest_request(self):
        request_event(route="x/", duration_ms=10, trace_id=_tid(1))
        request_event(route="x/", duration_ms=999, trace_id=_tid(2))

        stats, _ = endpoint_stats(_since())
        assert stats[0]["example_trace_id"] == _tid(2)

    def test_different_methods_on_the_same_route_are_separate_rows(self):
        request_event(method="GET", route="x/", trace_id=_tid(1))
        request_event(method="POST", route="x/", trace_id=_tid(2))
        stats, _ = endpoint_stats(_since())
        assert {r["method"] for r in stats} == {"GET", "POST"}

    def test_window_filter_excludes_older_events(self):
        old = timezone.now() - datetime.timedelta(hours=2)
        request_event(when=old)
        stats, _ = endpoint_stats(_since())
        assert stats == []

    def test_only_request_completed_events_are_counted(self):
        sql_event()
        stats, _ = endpoint_stats(_since())
        assert stats == []

    def test_scan_cap_bounds_rows_and_sets_the_flag(self):
        for i in range(5):
            request_event(trace_id=_tid(i), when=timezone.now() - datetime.timedelta(seconds=i))
        original = issues.ISSUES_SCAN_LIMIT
        issues.ISSUES_SCAN_LIMIT = 3
        try:
            stats, capped = endpoint_stats(_since())
        finally:
            issues.ISSUES_SCAN_LIMIT = original
        assert capped is True
        assert sum(r["count"] for r in stats) == 3
