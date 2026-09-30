"""Measure the inspector's per-request overhead (the "under ~2 ms p50" goal).

A representative view (5 queries, 3 cache calls, a small template tree and a
warning log) is requested many times through the full middleware chain, with
the inspector on (every watcher enabled, default sampling) and off. The p50
difference is the overhead.

    python scripts/benchmark.py                      # print the numbers
    python scripts/benchmark.py --max-overhead-ms 5  # also fail above a budget

Run from the repository root. It configures its own throwaway Django project.
"""

import argparse
import logging
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

TEMPLATES = {
    "layout.html": "<html>{% block body %}{% endblock %}</html>",
    "page.html": "{% extends 'layout.html' %}{% block body %}"
    "{% for item in items %}{% include 'item.html' %}{% endfor %}{% endblock %}",
    "item.html": "<p>{{ item }}</p>",
}


def configure():
    settings.configure(
        DEBUG=False,
        SECRET_KEY="benchmark",
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "django_inspector"],
        MIDDLEWARE=["django_inspector.middleware.InspectorMiddleware"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
        }],
        USE_TZ=True,
        LOGGING_CONFIG=None,
        SILENCED_SYSTEM_CHECKS=["django_inspector.W008"],
        DJANGO_INSPECTOR={
            "WATCHERS": {name: True for name in (
                "request", "sql", "exception", "cache", "template", "signal", "log",
            )},
        },
    )
    django.setup()


def view(request):
    from django.contrib.auth.models import User
    from django.core.cache import cache
    from django.shortcuts import render

    for i in range(5):
        User.objects.filter(pk=i).first()
    cache.set("bench", "value")
    cache.get("bench")
    cache.get("missing")
    logging.getLogger("benchmark").warning("a warning")
    return render(request, "page.html", {"items": ["a", "b", "c"]})


urlpatterns = []


def timed_requests(client, count):
    timings = []
    for _ in range(count):
        start = time.perf_counter()
        response = client.get("/bench/")
        timings.append((time.perf_counter() - start) * 1000)
        assert response.status_code == 200
    return timings


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--max-overhead-ms", type=float, default=None)
    args = parser.parse_args()

    configure()
    logging.getLogger("benchmark").propagate = True
    from django.core.management import call_command
    from django.test import Client
    from django.test.utils import override_settings, setup_test_environment
    from django.urls import path

    urlpatterns.append(path("bench/", view))
    setup_test_environment()
    call_command("migrate", verbosity=0)

    from django_inspector.storage.models import Event

    results = {}
    for label, middleware in (("off", []), ("on", settings.MIDDLEWARE)):
        with override_settings(MIDDLEWARE=middleware):
            client = Client()
            timed_requests(client, 50)  # warm up
            results[label] = timed_requests(client, args.requests)
        Event.objects.all().delete()

    p50 = {label: statistics.median(t) for label, t in results.items()}
    p95 = {label: sorted(t)[int(len(t) * 0.95)] for label, t in results.items()}
    overhead = p50["on"] - p50["off"]
    print("requests per run: %d" % args.requests)
    for label in ("off", "on"):
        print("inspector %-3s  p50 %.3f ms   p95 %.3f ms" % (label, p50[label], p95[label]))
    print("p50 overhead: %.3f ms" % overhead)
    if args.max_overhead_ms is not None and overhead > args.max_overhead_ms:
        print("FAIL: overhead above %.1f ms" % args.max_overhead_ms)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
