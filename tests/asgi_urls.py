"""URLconf for the ASGI end-to-end tests: one view that touches every v1.1 watcher."""

import asyncio
import logging

from django.core.cache import cache
from django.dispatch import Signal
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import include, path

order_checked_out = Signal()
logger = logging.getLogger("shop.checkout")


def audit(sender, order_id, **kwargs):
    """Signal receiver; its cache read proves it ran inside the right trace."""
    cache.get("audit:%d" % order_id)
    return order_id


async def checkout(request, order_id):
    await cache.aset("order:%d" % order_id, order_id)
    await cache.aget("order:%d" % order_id)
    await asyncio.sleep(0.01)  # let concurrent requests interleave
    html = render_to_string("inspector_tests/checkout.html", {"order_id": order_id})
    if hasattr(order_checked_out, "asend"):
        await order_checked_out.asend(sender=None, order_id=order_id)
    else:  # Django < 5.0
        order_checked_out.send(sender=None, order_id=order_id)
    await asyncio.sleep(0.01)
    logger.warning("order %s checked out", order_id)
    return HttpResponse(html)


def checkout_sync(request, order_id):
    cache.set("order:%d" % order_id, order_id)
    cache.get("order:%d" % order_id)
    html = render_to_string("inspector_tests/checkout.html", {"order_id": order_id})
    order_checked_out.send(sender=None, order_id=order_id)
    logger.warning("order %s checked out", order_id)
    return HttpResponse(html)


urlpatterns = [
    path("checkout/<int:order_id>/", checkout),
    path("sync/checkout/<int:order_id>/", checkout_sync),
    path("inspector/", include("django_inspector.dashboard.urls")),
]
