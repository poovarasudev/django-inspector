"""Event is registered in the Django admin, read-only (SCHEMA-05)."""

from django.contrib.admin.sites import AdminSite

from django_inspector.admin import EventAdmin
from django_inspector.storage.models import Event


class TestEventAdmin:
    def _admin(self):
        return EventAdmin(Event, AdminSite())

    def test_no_add_permission(self):
        assert self._admin().has_add_permission(request=None) is False

    def test_no_change_permission(self):
        assert self._admin().has_change_permission(request=None) is False

    def test_no_delete_permission(self):
        assert self._admin().has_delete_permission(request=None) is False

    def test_every_model_field_is_read_only(self):
        admin_instance = self._admin()
        field_names = {f.name for f in Event._meta.get_fields() if hasattr(f, "attname")}
        assert field_names <= set(admin_instance.readonly_fields)

    def test_list_display(self):
        admin_instance = self._admin()
        assert admin_instance.list_display == (
            "timestamp", "event_type", "trace_id", "method", "status_code",
            "path", "duration_ms", "pinned",
        )

    def test_list_filter(self):
        assert self._admin().list_filter == ("event_type", "pinned")

    def test_search_fields(self):
        assert self._admin().search_fields == ("trace_id", "fingerprint")

    def test_ordering(self):
        assert self._admin().ordering == ("-timestamp",)
