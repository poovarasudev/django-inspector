"""
A configurable management command used to exercise CommandWatcher: it can run
SQL queries, log a record, write to stdout/stderr, and fail in the ways the
watcher must classify into an exit code.
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

logger = logging.getLogger("tests.command_watcher")


class Command(BaseCommand):
    help = "Test fixture for the command watcher."

    def add_arguments(self, parser):
        parser.add_argument("--fail", default=None)
        parser.add_argument("--queries", type=int, default=0)
        parser.add_argument("--log", action="store_true")
        parser.add_argument("--stdout-text", default=None)
        parser.add_argument("--stderr-text", default=None)
        parser.add_argument("--password", default=None)

    def handle(self, *args, **options):
        for i in range(options["queries"]):
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s", [i])

        if options["log"]:
            logger.warning("probe log")

        if options["stdout_text"]:
            self.stdout.write(options["stdout_text"])
        if options["stderr_text"]:
            self.stderr.write(options["stderr_text"])

        fail = options["fail"]
        if fail == "valueerror":
            raise ValueError("probe failure")
        if fail == "commanderror":
            raise CommandError("probe command error", returncode=3)
        if fail == "systemexit0":
            raise SystemExit(0)
        if fail == "systemexit2":
            raise SystemExit(2)
        if fail == "keyboardinterrupt":
            raise KeyboardInterrupt()
        return None
