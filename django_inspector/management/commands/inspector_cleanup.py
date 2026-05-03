from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from django_inspector.storage.models import Event


class Command(BaseCommand):
    help = "Purge django-inspector events older than the specified duration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Delete events older than N hours (default: 24)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show count of events that would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(hours=hours)
        qs = Event.objects.filter(timestamp__lt=cutoff)
        count = qs.count()

        if dry_run:
            self.stdout.write(f"Would delete {count} event(s) older than {hours} hours (cutoff: {cutoff})")
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} event(s) older than {hours} hours"))
