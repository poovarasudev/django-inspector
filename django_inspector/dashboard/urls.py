from django.urls import path

from django_inspector.dashboard import views
from django_inspector.dashboard.auth import inspector_required

app_name = "inspector"

urlpatterns = [
    path("issues/", inspector_required(views.issues), name="issues"),
    path("", inspector_required(views.live_feed), name="live-feed"),
    path("requests/", inspector_required(views.requests_list), name="requests-list"),
    path("requests/<int:pk>/", inspector_required(views.request_detail), name="request-detail"),
    path("requests/<int:pk>/export.json", inspector_required(views.request_export), name="request-export"),
    path("traces/", inspector_required(views.traces_list), name="traces-list"),
    path("trace/<str:trace_id>/", inspector_required(views.trace_detail), name="trace-detail"),
    path("trace/<str:trace_id>/pin/", inspector_required(views.trace_pin), name="trace-pin"),
    path("trace/<str:trace_id>/export.json", inspector_required(views.trace_export), name="trace-export"),
    path("assets/<str:name>", inspector_required(views.asset), name="asset"),
    path("queries/", inspector_required(views.queries_list), name="queries-list"),
    path("queries/<int:pk>/", inspector_required(views.query_detail), name="query-detail"),
    path("exceptions/", inspector_required(views.exceptions_list), name="exceptions-list"),
    path("exceptions/<int:pk>/", inspector_required(views.exception_detail), name="exception-detail"),
    path("cache/", inspector_required(views.cache_list), name="cache-list"),
    path("cache/<int:pk>/", inspector_required(views.cache_detail), name="cache-detail"),
    path("templates/", inspector_required(views.templates_list), name="templates-list"),
    path("templates/<int:pk>/", inspector_required(views.template_detail), name="template-detail"),
    path("logs/", inspector_required(views.logs_list), name="logs-list"),
    path("logs/<int:pk>/", inspector_required(views.log_detail), name="log-detail"),
    path("signals/", inspector_required(views.signals_list), name="signals-list"),
    path("signals/<int:pk>/", inspector_required(views.signal_detail), name="signal-detail"),
    path("mail/", inspector_required(views.mail_list), name="mail-list"),
    path("mail/<int:pk>/", inspector_required(views.mail_detail), name="mail-detail"),
    path("mail/<int:pk>/preview/", inspector_required(views.mail_preview), name="mail-preview"),
    path("mail/<int:pk>/message.eml", inspector_required(views.mail_eml), name="mail-eml"),
    path("http/", inspector_required(views.http_list), name="http-list"),
    path("http/<int:pk>/", inspector_required(views.http_detail), name="http-detail"),
]
