"""
Dashboard access control for django-inspector.

Provides the `inspector_required` decorator applied to all dashboard views.
Default: requires request.user.is_staff.
Configurable via INSPECTOR_DASHBOARD_PERMISSION (dotted callable) and
INSPECTOR_DASHBOARD_IP_ALLOWLIST (list of IP/CIDR strings).

Requirements: AUTH-01..05
"""

import ipaddress
import logging
from functools import wraps

from django.http import HttpResponseForbidden
from django.utils.module_loading import import_string

from django_inspector.client_ip import get_client_ip
from django_inspector.conf import inspector_settings

logger = logging.getLogger("django_inspector")

_FORBIDDEN_BODY = "Inspector: access denied"


def _ip_allowed(request) -> bool:
    """Return True if client IP is in the allowlist, or if no allowlist is configured."""
    allowlist = inspector_settings.INSPECTOR_DASHBOARD_IP_ALLOWLIST
    if not allowlist:
        return True
    try:
        client_ip = ipaddress.ip_address(get_client_ip(request))
        for entry in allowlist:
            try:
                if client_ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                logger.warning(
                    "inspector: invalid IP/CIDR in INSPECTOR_DASHBOARD_IP_ALLOWLIST: %r", entry
                )
    except ValueError:
        pass
    return False


def _permission_check(request) -> bool:
    """Return True if the request passes the configured permission check."""
    perm_path = inspector_settings.INSPECTOR_DASHBOARD_PERMISSION
    if perm_path:
        try:
            checker = import_string(perm_path)
            return bool(checker(request))
        except Exception:
            logger.warning(
                "inspector: INSPECTOR_DASHBOARD_PERMISSION callable failed", exc_info=True
            )
            return False
    user = getattr(request, "user", None)
    if user is None:
        return False
    try:
        return bool(getattr(user, "is_staff", False))
    except Exception:
        return False


def can_access_dashboard(request) -> bool:
    """Return True if the request should be allowed to access the inspector dashboard."""
    return _ip_allowed(request) and _permission_check(request)


def inspector_required(view_func):
    """
    View decorator: require staff access (or custom permission) for the inspector dashboard.

    Returns HTTP 403 with a plain body that contains no event data or trace IDs (AUTH-05).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not can_access_dashboard(request):
            return HttpResponseForbidden(_FORBIDDEN_BODY)
        return view_func(request, *args, **kwargs)
    return _wrapped
