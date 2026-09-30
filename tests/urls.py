from django.urls import include, path

urlpatterns = [
    path("inspector/", include("django_inspector.dashboard.urls")),
]
