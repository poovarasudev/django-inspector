"""Tests for retention: RETENTION_HOURS pruning and the inspector_cleanup command."""

import datetime
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_inspector.storage import retention
from django_inspector.storage.models import Event

pytestmark = pytest.mark.django_db


def _event(hours_ago, trace="t" * 32):
    return Event.objects.create(
        trace_id=trace, event_type="sql.query", metadata={},
        timestamp=timezone.now() - datetime.timedelta(hours=hours_ago),
    )


@pytest.fixture(autouse=True)
def _reset_throttle():
    retention._reset_prune_clock()
    yield
    retention._reset_prune_clock()


class TestDeleteOlderThan:
    def test_deletes_only_older_events(self):
        old, new = _event(48), _event(1)
        deleted = retention.delete_older_than(timezone.now() - datetime.timedelta(hours=24))
        assert deleted == 1
        assert list(Event.objects.all()) == [new]
        assert not Event.objects.filter(pk=old.pk).exists()

    def test_deletes_in_batches(self):
        for _ in range(5):
            _event(48)
        cutoff = timezone.now() - datetime.timedelta(hours=24)
        assert retention.delete_older_than(cutoff, batch_size=2, max_batches=1) == 2
        assert retention.delete_older_than(cutoff, batch_size=2) == 3
        assert Event.objects.count() == 0


class TestMaybePrune:
    def test_no_retention_setting_keeps_everything(self):
        _event(10000)
        assert retention.maybe_prune() == 0
        assert Event.objects.count() == 1

    @override_settings(DJANGO_INSPECTOR={"RETENTION_HOURS": 24})
    def test_prunes_old_events(self):
        _event(48)
        _event(1)
        assert retention.maybe_prune() == 1
        assert Event.objects.count() == 1

    @override_settings(DJANGO_INSPECTOR={"RETENTION_HOURS": 24})
    def test_prunes_at_most_once_per_interval(self):
        _event(48)
        retention.maybe_prune()
        _event(48)
        assert retention.maybe_prune() == 0
        assert Event.objects.count() == 1

    @override_settings(DJANGO_INSPECTOR={"RETENTION_HOURS": 24})
    def test_the_middleware_prunes_after_flushing(self):
        from django.http import HttpResponse

        from django_inspector.middleware import InspectorMiddleware

        _event(48)
        InspectorMiddleware(lambda request: HttpResponse("ok"))(RequestFactory().get("/r/"))
        assert list(Event.objects.values_list("event_type", flat=True)) == ["request.completed"]


class TestCleanupCommand:
    def test_hours_option(self):
        _event(10)
        _event(1)
        out = StringIO()
        call_command("inspector_cleanup", "--hours", "5", stdout=out)
        assert Event.objects.count() == 1
        assert "Deleted 1 event(s)" in out.getvalue()

    @override_settings(DJANGO_INSPECTOR={"RETENTION_HOURS": 2})
    def test_defaults_to_retention_hours(self):
        _event(3)
        _event(1)
        call_command("inspector_cleanup", stdout=StringIO())
        assert Event.objects.count() == 1

    def test_defaults_to_24_hours_without_retention(self):
        _event(25)
        _event(23)
        call_command("inspector_cleanup", stdout=StringIO())
        assert Event.objects.count() == 1

    def test_dry_run_deletes_nothing(self):
        _event(48)
        out = StringIO()
        call_command("inspector_cleanup", "--dry-run", stdout=out)
        assert Event.objects.count() == 1
        assert "Would delete 1 event(s)" in out.getvalue()

    @pytest.mark.parametrize("hours", ["0", "-3"])
    def test_non_positive_hours_are_rejected(self, hours):
        with pytest.raises(CommandError):
            call_command("inspector_cleanup", "--hours", hours, stdout=StringIO())

    def test_batch_size_option(self):
        for _ in range(3):
            _event(48)
        call_command("inspector_cleanup", "--batch-size", "1", stdout=StringIO())
        assert Event.objects.count() == 0
