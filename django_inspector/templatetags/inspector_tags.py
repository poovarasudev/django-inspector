from django import template

from django_inspector import __version__

register = template.Library()


@register.simple_tag
def inspector_version():
    """The installed django-inspector version, shown in the dashboard sidebar."""
    return __version__
