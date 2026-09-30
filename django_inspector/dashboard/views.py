from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator

from django_inspector.storage.models import Event
from django_inspector.watchers.cache import CACHE_EVENT_TYPES

CACHE_OPERATIONS = [event_type.split(".", 1)[1] for event_type in CACHE_EVENT_TYPES]


def live_feed(request):
    qs = Event.objects.filter(event_type="request.completed").order_by("-timestamp")
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))

    if request.headers.get("HX-Request"):
        return render(request, "inspector/_live_feed_table.html", {"page": page})
    return render(request, "inspector/live_feed.html", {"page": page})


def requests_list(request):
    qs = Event.objects.filter(event_type="request.completed").order_by("-timestamp")

    method = request.GET.get("method", "")
    status = request.GET.get("status", "")
    path = request.GET.get("path", "")
    from_date = request.GET.get("from_date", "")
    to_date = request.GET.get("to_date", "")

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
    if from_date:
        qs = qs.filter(timestamp__gte=from_date)
    if to_date:
        qs = qs.filter(timestamp__lte=to_date)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {"page": page, "filters": request.GET}

    if request.headers.get("HX-Request"):
        return render(request, "inspector/requests/_table.html", context)
    return render(request, "inspector/requests/list.html", context)


def request_detail(request, pk):
    event = get_object_or_404(Event, pk=pk, event_type="request.completed")
    trace_events = Event.objects.filter(trace_id=event.trace_id).order_by("timestamp")

    queries = [e for e in trace_events if e.event_type == "sql.query"]
    exceptions = [e for e in trace_events if e.event_type == "exception.raised"]

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
    }

    # Compute waterfall offsets for trace events
    waterfall_events = []
    total_ms = latency_ms or 1  # avoid zero division
    for e in trace_events:
        offset_ms = (e.timestamp - event.timestamp).total_seconds() * 1000
        duration_ms = e.metadata.get("duration_ms", 0) or e.metadata.get("latency_ms", 0) or 0
        offset_pct = max(0, min(100, (offset_ms / total_ms) * 100))
        width_pct = max(0.5, min(100 - offset_pct, (duration_ms / total_ms) * 100))
        waterfall_events.append({
            "event": e,
            "offset_ms": round(offset_ms, 2),
            "duration_ms": round(duration_ms, 2),
            "offset_pct": round(offset_pct, 2),
            "width_pct": round(width_pct, 2),
        })

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
    }
    return render(request, "inspector/requests/detail.html", context)


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

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
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
    from_date = request.GET.get("from_date", "")
    to_date = request.GET.get("to_date", "")

    if exc_type:
        qs = qs.filter(metadata__exception_type=exc_type)
    if from_date:
        qs = qs.filter(timestamp__gte=from_date)
    if to_date:
        qs = qs.filter(timestamp__lte=to_date)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))

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

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
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

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
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
