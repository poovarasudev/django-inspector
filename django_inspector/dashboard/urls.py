from django.urls import path

from django_inspector.dashboard import views
from django_inspector.dashboard.auth import inspector_required

app_name = "inspector"

urlpatterns = [
    path("", inspector_required(views.live_feed), name="live-feed"),
    path("requests/", inspector_required(views.requests_list), name="requests-list"),
    path("requests/<int:pk>/", inspector_required(views.request_detail), name="request-detail"),
    path("queries/", inspector_required(views.queries_list), name="queries-list"),
    path("queries/<int:pk>/", inspector_required(views.query_detail), name="query-detail"),
    path("exceptions/", inspector_required(views.exceptions_list), name="exceptions-list"),
    path("exceptions/<int:pk>/", inspector_required(views.exception_detail), name="exception-detail"),
    path("cache/", inspector_required(views.cache_list), name="cache-list"),
    path("cache/<int:pk>/", inspector_required(views.cache_detail), name="cache-detail"),
]
