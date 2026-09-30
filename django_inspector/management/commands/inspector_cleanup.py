from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from django_inspector.conf import inspector_settings
from django_inspector.storage.models import Event
from django_inspector.storage.retention import delete_older_than


class Command(BaseCommand):
    help = "Purge django-inspector events older than the specified duration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=float,
            default=None,
            help="Delete events older than N hours (default: RETENTION_HOURS, or 24)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10000,
            help="Rows deleted per statement (default: 10000)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show count of events that would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours is None:
            hours = inspector_settings.RETENTION_HOURS or 24
        if hours <= 0:
            raise CommandError("--hours must be greater than 0.")
        if options["batch_size"] <= 0:
            raise CommandError("--batch-size must be greater than 0.")
        hours_text = "%g" % hours
        cutoff = timezone.now() - timedelta(hours=hours)

        if options["dry_run"]:
            count = Event.objects.filter(timestamp__lt=cutoff).count()
            self.stdout.write(
                f"Would delete {count} event(s) older than {hours_text} hours (cutoff: {cutoff})"
            )
            return

        deleted = delete_older_than(cutoff, batch_size=options["batch_size"])
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} event(s) older than {hours_text} hours"))
