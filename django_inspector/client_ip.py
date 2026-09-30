"""
Client IP resolution shared by the request watcher and the dashboard IP
allowlist.

X-Forwarded-For is client-controlled: anyone can send it. It is only trusted
for the TRUSTED_PROXY_COUNT hops added by the host's own reverse proxies, so a
spoofed header can't pass the dashboard allowlist.
"""

from django_inspector.conf import inspector_settings


def get_client_ip(request) -> str:
    """
    The client's IP address.

    With TRUSTED_PROXY_COUNT = 0 (the default) this is REMOTE_ADDR. With N
    trusted proxies, each proxy appends the address it saw to X-Forwarded-For,
    so the client is the Nth entry from the right.
    """
    remote_addr = request.META.get("REMOTE_ADDR") or ""
    proxies = inspector_settings.TRUSTED_PROXY_COUNT or 0
    if proxies <= 0:
        return remote_addr
    header = request.META.get("HTTP_X_FORWARDED_FOR") or ""
    hops = [hop.strip() for hop in header.split(",") if hop.strip()]
    if not hops:
        return remote_addr
    # Fewer hops than proxies: every entry was added by a trusted proxy.
    return hops[-proxies] if len(hops) >= proxies else hops[0]
