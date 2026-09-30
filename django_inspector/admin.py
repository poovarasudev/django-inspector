"""
Read-only Django admin for Event (SCHEMA-05, concern C-13).

Only loaded when the host project has django.contrib.admin installed:
Django's own admin autodiscovery is what imports this module, so hosts
without the admin are unaffected.
"""

from django.contrib import admin

from django_inspector.storage.models import Event


class EventAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp", "event_type", "trace_id", "method", "status_code",
        "path", "duration_ms", "pinned",
    )
    list_filter = ("event_type", "pinned")
    search_fields = ("trace_id", "fingerprint")
    ordering = ("-timestamp",)
    readonly_fields = [f.name for f in Event._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting goes through retention; pinning stays the only way to
        # keep rows past RETENTION_HOURS.
        return False


admin.site.register(Event, EventAdmin)
