"""
Issues page aggregates (DASH-04): exceptions by fingerprint, SQL by query
shape, and endpoints by route, ranked by latency and error rate.

Pure functions of ``since`` — no request, no view — so they're testable
without going through a view. Each returns ``(rows, scan_cap_hit)``.

Every query here is bounded by ``ISSUES_SCAN_LIMIT``: at most that many of
the newest matching rows are scanned, whatever the table's total size.
Percentiles are computed in Python (nearest-rank) over those bounded rows,
since SQLite and MySQL have no percentile aggregate.
"""

from collections import Counter, defaultdict
from math import ceil
from typing import DefaultDict, List, Tuple

from django.db.models import Count, Max, Min, Q

from django_inspector.storage.columns import innermost_app_frame, normalize_sql
from django_inspector.storage.models import Event

ISSUES_SCAN_LIMIT = 10000
_EXAMPLE_TEXT_LIMIT = 300


def _bounded(qs):
    """
    ``qs`` filtered to at most ``ISSUES_SCAN_LIMIT`` of its newest rows, and
    whether that cut the result short. One indexed query, flat cost however
    big the table is.
    """
    # Ordered by timestamp so the (event_type, -timestamp) index serves it, and
    # "newest" means newest by capture time (ids follow flush order instead);
    # id only breaks ties so the bound stays exact.
    cutoff = list(
        qs.order_by("-timestamp", "-id").values_list("timestamp", "id")[ISSUES_SCAN_LIMIT - 1:ISSUES_SCAN_LIMIT]
    )
    if not cutoff:
        return qs, False
    timestamp, event_id = cutoff[0]
    return qs.filter(Q(timestamp__gt=timestamp) | Q(timestamp=timestamp, id__gte=event_id)), True


def _percentile(sorted_values, pct):
    """Nearest-rank percentile over an ascending, non-empty list of values."""
    if not sorted_values:
        return None
    rank = max(1, ceil(pct / 100 * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _truncate(text, limit=_EXAMPLE_TEXT_LIMIT):
    if text is None:
        return None
    return text[:limit]


def exception_groups(since, limit=50) -> Tuple[List[dict], bool]:
    """Exceptions grouped by fingerprint, ranked by count then recency."""
    qs = Event.objects.filter(
        event_type="exception.raised", timestamp__gte=since, fingerprint__isnull=False
    )
    bounded, capped = _bounded(qs)

    grouped = list(
        bounded.values("fingerprint")
        .annotate(
            count=Count("id"),
            first_seen=Min("timestamp"),
            last_seen=Max("timestamp"),
            example_id=Max("id"),
            affected_traces=Count("trace_id", distinct=True),
        )
        .order_by("-count", "-last_seen")[:limit]
    )

    examples = Event.objects.in_bulk(row["example_id"] for row in grouped)

    results = []
    for row in grouped:
        example = examples.get(row["example_id"])
        metadata = example.metadata if example else {}
        frames = metadata.get("frames") or []
        frame = innermost_app_frame(frames) if frames else None
        results.append({
            "fingerprint": row["fingerprint"],
            "count": row["count"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "affected_traces": row["affected_traces"],
            "example_id": row["example_id"],
            "exception_type": metadata.get("exception_type"),
            "exception_message": _truncate(metadata.get("exception_message")),
            "frame": "%s:%s" % (frame.get("file") or "", frame.get("function") or "") if frame else None,
            "example_trace_id": example.trace_id if example else None,
        })
    return results, capped


def query_shape_groups(since, limit=50) -> Tuple[List[dict], bool]:
    """SQL grouped by normalised query shape, N+1 shapes surfaced first."""
    qs = Event.objects.filter(
        event_type="sql.query", timestamp__gte=since, fingerprint__isnull=False
    )
    bounded, capped = _bounded(qs)

    grouped = list(
        bounded.values("fingerprint")
        .annotate(
            count=Count("id"),
            example_id=Max("id"),
            affected_traces=Count("trace_id", distinct=True),
            n_plus_one_traces=Count(
                "trace_id", filter=Q(metadata__n_plus_one=True), distinct=True
            ),
        )
        .order_by("-n_plus_one_traces", "-count")[:limit]
    )

    examples = Event.objects.in_bulk(row["example_id"] for row in grouped)

    fingerprints = [row["fingerprint"] for row in grouped]
    durations: DefaultDict[str, list] = defaultdict(list)
    origins: DefaultDict[str, Counter] = defaultdict(Counter)
    detail_rows = bounded.filter(fingerprint__in=fingerprints).values_list(
        "fingerprint", "duration_ms", "metadata__origin_file", "metadata__origin_line"
    )
    for fingerprint, duration_ms, origin_file, origin_line in detail_rows:
        if duration_ms is not None:
            durations[fingerprint].append(duration_ms)
        if origin_file:
            origins[fingerprint][(origin_file, origin_line)] += 1

    results = []
    for row in grouped:
        example = examples.get(row["example_id"])
        sql = normalize_sql(example.metadata.get("sql")) if example and example.metadata.get("sql") else None
        shape_durations = sorted(durations.get(row["fingerprint"], []))
        origin_counts = origins.get(row["fingerprint"])
        top_origin = origin_counts.most_common(1)[0][0] if origin_counts else None
        results.append({
            "fingerprint": row["fingerprint"],
            "count": row["count"],
            "affected_traces": row["affected_traces"],
            "n_plus_one_traces": row["n_plus_one_traces"],
            "example_id": row["example_id"],
            "sql": _truncate(sql),
            "origin": "%s:%s" % top_origin if top_origin else None,
            "p50": _percentile(shape_durations, 50),
            "p95": _percentile(shape_durations, 95),
            "example_trace_id": example.trace_id if example else None,
        })
    return results, capped


def endpoint_stats(since, limit=50) -> Tuple[List[dict], bool]:
    """Endpoints grouped by (method, route or path), ranked by p95 latency."""
    qs = Event.objects.filter(event_type="request.completed", timestamp__gte=since)
    bounded, capped = _bounded(qs)

    rows = bounded.values_list("method", "status_code", "duration_ms", "path", "metadata__route", "trace_id")

    groups = defaultdict(list)
    for method, status_code, duration_ms, path, route, trace_id in rows:
        groups[(method, route or path)].append((status_code, duration_ms, trace_id))

    results = []
    for (method, endpoint), entries in groups.items():
        durations = sorted(d for _, d, _ in entries if d is not None)
        count = len(entries)
        error_count = sum(1 for sc, _, _ in entries if sc is not None and 500 <= sc < 600)
        client_error_count = sum(1 for sc, _, _ in entries if sc is not None and 400 <= sc < 500)
        slowest = max(entries, key=lambda e: e[1] if e[1] is not None else -1)
        results.append({
            "method": method,
            "endpoint": endpoint,
            "count": count,
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "error_rate": error_count / count,
            "client_error_rate": client_error_count / count,
            "example_trace_id": slowest[2],
        })

    results.sort(key=lambda r: r["p95"] if r["p95"] is not None else -1, reverse=True)
    return results[:limit], capped
