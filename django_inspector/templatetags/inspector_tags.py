from django import template
from django.http import QueryDict

from django_inspector import __version__

register = template.Library()


@register.simple_tag
def inspector_version():
    """The installed django-inspector version, shown in the dashboard sidebar."""
    return __version__


@register.simple_tag(takes_context=True)
def page_query(context, number):
    """The current query string with ``page`` set to ``number``, URL-encoded."""
    request = getattr(context, "request", None)
    params = request.GET.copy() if request is not None else None
    if params is None:
        return "page=%s" % number
    params["page"] = number
    return params.urlencode()


@register.simple_tag(takes_context=True)
def csv_query(context):
    """The current filters as a query string with ``format=csv`` (DASH-03), no ``page``."""
    request = getattr(context, "request", None)
    params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params.pop("page", None)
    params["format"] = "csv"
    return params.urlencode()
