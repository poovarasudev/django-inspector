from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator

from django_inspector.conf import inspector_settings
from django_inspector.storage.models import Event
from django_inspector.watchers.cache import CACHE_EVENT_TYPES

CACHE_OPERATIONS = [event_type.split(".", 1)[1] for event_type in CACHE_EVENT_TYPES]
LOG_LEVELS = [(10, "DEBUG"), (20, "INFO"), (30, "WARNING"), (40, "ERROR"), (50, "CRITICAL")]


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
    cache_events = [e for e in trace_events if e.event_type in CACHE_EVENT_TYPES]
    template_renders = sorted(
        (e for e in trace_events if e.event_type == "template.rendered"),
        key=lambda e: e.metadata.get("render_id") or 0,
    )
    cache_hits, cache_misses = _cache_hits_and_misses(cache_events)

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
        "cache_events": cache_events,
        "template_renders": template_renders,
        "cache_event_types": CACHE_EVENT_TYPES,
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

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
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

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
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
