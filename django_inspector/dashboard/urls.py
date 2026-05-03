from django.urls import path

from django_inspector.dashboard import views

app_name = "inspector"

urlpatterns = [
    path("", views.live_feed, name="live-feed"),
    path("requests/", views.requests_list, name="requests-list"),
    path("requests/<int:pk>/", views.request_detail, name="request-detail"),
    path("queries/", views.queries_list, name="queries-list"),
    path("queries/<int:pk>/", views.query_detail, name="query-detail"),
    path("exceptions/", views.exceptions_list, name="exceptions-list"),
    path("exceptions/<int:pk>/", views.exception_detail, name="exception-detail"),
]
