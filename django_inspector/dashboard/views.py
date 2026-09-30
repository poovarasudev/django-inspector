import datetime
import os
from functools import lru_cache

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.functional import cached_property

from django_inspector.conf import inspector_settings
from django_inspector.storage.models import Event
from django_inspector.watchers.cache import CACHE_EVENT_TYPES

CACHE_OPERATIONS = [event_type.split(".", 1)[1] for event_type in CACHE_EVENT_TYPES]
LOG_LEVELS = [(10, "DEBUG"), (20, "INFO"), (30, "WARNING"), (40, "ERROR"), (50, "CRITICAL")]
PAGE_SIZE = 25
MAX_DETAIL_EVENTS = 2000  # events shown on one request's page

_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_ASSET_TYPES = {
    "htmx.min.js": "application/javascript",
    "inspector.js": "application/javascript",
    "inspector.css": "text/css",
}


class CappedPaginator(Paginator):
    """
    A paginator whose total is counted only up to ``max_count``: a COUNT(*)
    over a large events table is slow, and nobody pages past 10,000 rows.
    """

    def __init__(self, object_list, per_page, max_count=10000, **kwargs):
        super().__init__(object_list, per_page, **kwargs)
        self.max_count = max_count
        self.count_capped = False

    @cached_property
    def count(self):
        counted = self.object_list[: self.max_count + 1].count()
        self.count_capped = counted > self.max_count
        return min(counted, self.max_count)


def _paginate(request, qs):
    return CappedPaginator(qs, PAGE_SIZE).get_page(request.GET.get("page", 1))


def _day_start(value, days_later=0):
    """Start of the given YYYY-MM-DD day (plus ``days_later``), or None if invalid."""
    try:
        day = parse_date(value or "")
    except ValueError:  # well-formed but impossible, e.g. 2026-02-30
        return None
    if day is None:
        return None
    start = datetime.datetime.combine(day + datetime.timedelta(days=days_later), datetime.time.min)
    if settings.USE_TZ:
        start = timezone.make_aware(start)
    return start


def _filter_dates(request, qs):
    """from_date/to_date filters; the to date includes that whole day. Invalid dates are ignored."""
    start = _day_start(request.GET.get("from_date"))
    if start is not None:
        qs = qs.filter(timestamp__gte=start)
    end = _day_start(request.GET.get("to_date"), days_later=1)
    if end is not None:
        qs = qs.filter(timestamp__lt=end)
    return qs


def _normalize_trace_id(value):
    return (value or "").strip().lower().replace("-", "")


def live_feed(request):
    qs = Event.objects.filter(event_type="request.completed").order_by("-timestamp")
    page = _paginate(request, qs)

    if request.headers.get("HX-Request"):
        return render(request, "inspector/_live_feed_table.html", {"page": page})
    return render(request, "inspector/live_feed.html", {"page": page})


def requests_list(request):
    qs = Event.objects.filter(event_type="request.completed").order_by("-timestamp")

    method = request.GET.get("method", "")
    status = request.GET.get("status", "")
    path = request.GET.get("path", "")
    trace = _normalize_trace_id(request.GET.get("trace"))

    if method:
        qs = qs.filter(metadata__method=method)
    if status:
        status_map = {
            "2xx": (200, 300),
            "3xx": (300, 400),
            "4xx": (400, 500),
            "5xx": (500, 600),
        }
        if status in status_map:
            low, high = status_map[status]
            qs = qs.filter(metadata__status_code__gte=low, metadata__status_code__lt=high)
    if path:
        qs = qs.filter(metadata__path__icontains=path)
    if trace:
        qs = qs.filter(trace_id=trace)
    qs = _filter_dates(request, qs)

    page = _paginate(request, qs)
    context = {"page": page, "filters": request.GET}

    if request.headers.get("HX-Request"):
        return render(request, "inspector/requests/_table.html", context)
    return render(request, "inspector/requests/list.html", context)


def request_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="request.completed")
    trace_events = list(
        Event.objects.filter(trace_id=event.trace_id).order_by("timestamp", "id")[: MAX_DETAIL_EVENTS + 1]
    )
    trace_truncated = len(trace_events) > MAX_DETAIL_EVENTS
    trace_events = trace_events[:MAX_DETAIL_EVENTS]

    queries = [e for e in trace_events if e.event_type == "sql.query"]
    exceptions = [e for e in trace_events if e.event_type == "exception.raised"]
    cache_events = [e for e in trace_events if e.event_type in CACHE_EVENT_TYPES]
    template_renders = sorted(
        (e for e in trace_events if e.event_type == "template.rendered"),
        key=lambda e: e.metadata.get("render_id") or 0,
    )
    cache_hits, cache_misses = _cache_hits_and_misses(cache_events)
    log_records = [e for e in trace_events if e.event_type == "log.record"]
    signal_dispatches = [e for e in trace_events if e.event_type == "signal.dispatched"]

    total_query_time = sum(e.metadata.get("duration_ms", 0) for e in queries)
    slow_queries = sum(1 for e in queries if e.metadata.get("is_slow"))
    n_plus_one = sum(1 for e in queries if e.metadata.get("n_plus_one"))
    duplicate = sum(1 for e in queries if e.metadata.get("is_duplicate"))
    latency_ms = event.metadata.get("latency_ms", 0)

    summary = {
        "total_queries": len(queries),
        "total_query_time": round(total_query_time, 2),
        "exception_count": len(exceptions),
        "latency_ms": latency_ms,
        "slow_queries": slow_queries,
        "n_plus_one": n_plus_one,
        "duplicate": duplicate,
        "cache_ops": len(cache_events),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "template_renders": len(template_renders),
        "log_records": len(log_records),
        "signal_dispatches": len(signal_dispatches),
    }

    waterfall_events = _waterfall(trace_events, event)

    view = request.GET.get("view", "timeline")
    if view not in ("timeline", "tabs", "waterfall"):
        view = "timeline"

    context = {
        "event": event,
        "trace_events": trace_events,
        "queries": queries,
        "exceptions": exceptions,
        "summary": summary,
        "view": view,
        "waterfall_events": waterfall_events,
        "cache_events": cache_events,
        "template_renders": template_renders,
        "cache_event_types": CACHE_EVENT_TYPES,
        "log_records": log_records,
        "signal_dispatches": signal_dispatches,
        "trace_truncated": trace_truncated,
        "max_detail_events": MAX_DETAIL_EVENTS,
    }
    return render(request, "inspector/requests/detail.html", context)


def trace_detail(request, trace_id):
    """/trace/<id>/: jump to the request page of a trace id (e.g. one found in your logs)."""
    event = (
        Event.objects.filter(trace_id=_normalize_trace_id(trace_id), event_type="request.completed")
        .order_by("timestamp")
        .first()
    )
    if event is None:
        raise Http404("No request with that trace id.")
    return redirect("inspector:request-detail", pk=event.pk)


def request_export(request, pk):
    """The request's whole trace as a JSON download."""
    event = get_object_or_404(Event, pk=pk, event_type="request.completed")
    events = Event.objects.filter(trace_id=event.trace_id).order_by("timestamp", "id")
    data = {
        "trace_id": event.trace_id,
        "request_event_id": event.pk,
        "events": [
            {
                "id": e.pk,
                "event_type": e.event_type,
                "timestamp": e.timestamp.isoformat(),
                "metadata": e.metadata,
            }
            for e in events
        ],
    }
    response = JsonResponse(data, json_dumps_params={"indent": 2})
    response["Content-Disposition"] = 'attachment; filename="trace-%s.json"' % event.trace_id
    return response


@lru_cache(maxsize=None)
def _asset_bytes(name):
    with open(os.path.join(_ASSET_DIR, name), "rb") as f:
        return f.read()


def asset(request, name):
    """
    The dashboard's own CSS and JavaScript (including a bundled htmx), served
    from the package so the dashboard needs no CDN and no collectstatic.
    """
    content_type = _ASSET_TYPES.get(name)
    if content_type is None:
        raise Http404("Unknown asset.")
    response = HttpResponse(_asset_bytes(name), content_type=content_type + "; charset=utf-8")
    response["Cache-Control"] = "private, max-age=86400"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def queries_list(request):
    qs = Event.objects.filter(event_type="sql.query").order_by("-timestamp")

    if request.GET.get("slow") == "on":
        qs = qs.filter(metadata__is_slow=True)
    if request.GET.get("n_plus_one") == "on":
        qs = qs.filter(metadata__n_plus_one=True)
    if request.GET.get("duplicate") == "on":
        qs = qs.filter(metadata__is_duplicate=True)
    db_alias = request.GET.get("db_alias", "")
    if db_alias:
        qs = qs.filter(metadata__db_alias=db_alias)

    page = _paginate(request, qs)
    context = {"page": page, "filters": request.GET}

    if request.headers.get("HX-Request"):
        return render(request, "inspector/queries/_table.html", context)
    return render(request, "inspector/queries/list.html", context)


def query_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="sql.query")
    parent_request = Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()
    context = {"event": event, "parent_request": parent_request}
    return render(request, "inspector/queries/detail.html", context)


def exceptions_list(request):
    qs = Event.objects.filter(event_type="exception.raised").order_by("-timestamp")

    exc_type = request.GET.get("exc_type", "")

    if exc_type:
        qs = qs.filter(metadata__exception_type=exc_type)
    qs = _filter_dates(request, qs)

    page = _paginate(request, qs)

    # Build exception type choices from recent events
    exc_types = list(
        Event.objects.filter(event_type="exception.raised")
        .values_list("metadata__exception_type", flat=True)
        .distinct()[:50]
    )

    context = {
        "page": page,
        "filters": request.GET,
        "exception_types": exc_types,
    }

    if request.headers.get("HX-Request"):
        return render(request, "inspector/exceptions/_table.html", context)
    return render(request, "inspector/exceptions/list.html", context)


def exception_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="exception.raised")
    parent_request = Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()
    context = {"event": event, "parent_request": parent_request}
    return render(request, "inspector/exceptions/detail.html", context)


def cache_list(request):
    qs = Event.objects.filter(event_type__in=CACHE_EVENT_TYPES).order_by("-timestamp")

    operation = request.GET.get("operation", "")
    alias = request.GET.get("alias", "")
    result = request.GET.get("result", "")
    key = request.GET.get("key", "")

    if operation:
        qs = qs.filter(metadata__operation=operation)
    if alias:
        qs = qs.filter(metadata__alias=alias)
    if result == "hit":
        qs = qs.filter(metadata__hit=True)
    elif result == "miss":
        qs = qs.filter(metadata__hit=False)
    if key:
        qs = qs.filter(metadata__key__icontains=key)

    page = _paginate(request, qs)
    context = {
        "page": page,
        "filters": request.GET,
        "operations": CACHE_OPERATIONS,
        "aliases": list(settings.CACHES),
    }

    if request.headers.get("HX-Request"):
        return render(request, "inspector/cache/_table.html", context)
    return render(request, "inspector/cache/list.html", context)


def cache_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type__in=CACHE_EVENT_TYPES)
    parent_request = Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()
    context = {"event": event, "parent_request": parent_request}
    return render(request, "inspector/cache/detail.html", context)


def templates_list(request):
    qs = Event.objects.filter(event_type="template.rendered").order_by("-timestamp")

    name = request.GET.get("name", "")
    if name:
        qs = qs.filter(metadata__name__icontains=name)
    if request.GET.get("top_level") == "on":
        qs = qs.filter(metadata__depth=0)

    page = _paginate(request, qs)
    context = {"page": page, "filters": request.GET}

    if request.headers.get("HX-Request"):
        return render(request, "inspector/template_renders/_table.html", context)
    return render(request, "inspector/template_renders/list.html", context)


def template_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="template.rendered")
    same_trace = Event.objects.filter(trace_id=event.trace_id, event_type="template.rendered")
    render_id = event.metadata.get("render_id")
    parent_id = event.metadata.get("parent_id")

    parent_render = None
    if parent_id is not None:
        parent_render = same_trace.filter(metadata__render_id=parent_id).first()
    child_renders = []
    if render_id is not None:
        child_renders = sorted(
            same_trace.filter(metadata__parent_id=render_id),
            key=lambda e: e.metadata.get("render_id") or 0,
        )
    parent_request = Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()

    context = {
        "event": event,
        "parent_render": parent_render,
        "child_renders": child_renders,
        "parent_request": parent_request,
    }
    return render(request, "inspector/template_renders/detail.html", context)


def _waterfall(trace_events, request_event):
    """
    Bar positions for the waterfall view. The request event is stamped with
    the request's start; other events are stamped when they finished, so a
    bar starts at (timestamp - duration).
    """
    start = request_event.timestamp
    total_ms = request_event.metadata.get("latency_ms") or 1  # avoid zero division
    rows = []
    for e in trace_events:
        if e.pk == request_event.pk:
            offset_ms, duration_ms = 0.0, request_event.metadata.get("latency_ms") or 0
        else:
            duration_ms = e.metadata.get("duration_ms") or 0
            end_ms = (e.timestamp - start).total_seconds() * 1000
            offset_ms = max(0.0, end_ms - duration_ms)
        offset_pct = max(0, min(100, (offset_ms / total_ms) * 100))
        width_pct = max(0.5, min(100 - offset_pct, (duration_ms / total_ms) * 100))
        rows.append({
            "event": e,
            "offset_ms": round(offset_ms, 2),
            "duration_ms": round(duration_ms, 2),
            "offset_pct": round(offset_pct, 2),
            "width_pct": round(width_pct, 2),
        })
    return rows


def _cache_hits_and_misses(cache_events):
    """Total hits and misses across get (hit flag) and get_many (hit/miss counts)."""
    hits = misses = 0
    for e in cache_events:
        meta = e.metadata
        if meta.get("operation") == "get":
            if meta.get("hit") is True:
                hits += 1
            elif meta.get("hit") is False:
                misses += 1
        elif meta.get("operation") == "get_many":
            hits += meta.get("hit_count") or 0
            misses += meta.get("miss_count") or 0
    return hits, misses


def logs_list(request):
    qs = Event.objects.filter(event_type="log.record").order_by("-timestamp")

    level = request.GET.get("level", "")
    logger_name = request.GET.get("logger", "")
    message = request.GET.get("message", "")

    if level.isdigit():
        qs = qs.filter(metadata__level__gte=int(level))
    if logger_name:
        qs = qs.filter(metadata__logger__icontains=logger_name)
    if message:
        qs = qs.filter(metadata__message__icontains=message)

    page = _paginate(request, qs)
    context = {"page": page, "filters": request.GET, "levels": LOG_LEVELS}

    if request.headers.get("HX-Request"):
        return render(request, "inspector/logs/_table.html", context)
    return render(request, "inspector/logs/list.html", context)


def log_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="log.record")
    context = {"event": event, "parent_request": _parent_request(event)}
    return render(request, "inspector/logs/detail.html", context)


def signals_list(request):
    qs = Event.objects.filter(event_type="signal.dispatched").order_by("-timestamp")

    signal = request.GET.get("signal", "")
    sender = request.GET.get("sender", "")

    if signal:
        qs = qs.filter(metadata__signal=signal)
    if sender:
        qs = qs.filter(metadata__sender__icontains=sender)

    page = _paginate(request, qs)
    context = {
        "page": page,
        "filters": request.GET,
        "signals": list(inspector_settings.SIGNAL_WATCH_LIST or []),
    }

    if request.headers.get("HX-Request"):
        return render(request, "inspector/signals/_table.html", context)
    return render(request, "inspector/signals/list.html", context)


def signal_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="signal.dispatched")
    receivers = event.metadata.get("receivers") or []
    not_run = max(0, (event.metadata.get("receiver_count") or 0) - len(receivers))
    context = {
        "event": event,
        "receivers": receivers,
        "not_run": not_run,
        "parent_request": _parent_request(event),
    }
    return render(request, "inspector/signals/detail.html", context)


def _parent_request(event):
    """The request.completed event of the trace this event belongs to, if any."""
    return Event.objects.filter(
        trace_id=event.trace_id, event_type="request.completed"
    ).first()
