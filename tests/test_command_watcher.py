"""Tests for the Command Watcher (CMD-01..03)."""

import logging

import pytest
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.test import TestCase, override_settings

from django_inspector import trace
from django_inspector.storage.models import Event
from django_inspector.watchers import registry
from django_inspector.watchers.command import NEVER_TRACED, CommandWatcher

WATCH_PROBE = override_settings(DJANGO_INSPECTOR={"COMMAND_WATCH_LIST": ["inspector_probe"]})


@pytest.fixture(autouse=True)
def _clean_buffer():
    from django_inspector.storage.flush import clear_buffer
    clear_buffer()
    yield
    clear_buffer()


class TestLifecycle(TestCase):
    """The process-wide command watcher is already enabled (WATCHERS["command"] defaults True);
    these tests work on their own instances, keeping that one disabled meanwhile."""

    def setUp(self):
        self.global_watcher = registry.get_instance("command")
        self.global_watcher.disable()

    def tearDown(self):
        self.global_watcher.enable()

    def test_install_patches_execute_and_remove_restores_it(self):
        original = BaseCommand.execute
        watcher = CommandWatcher()
        watcher.enable()
        try:
            assert BaseCommand.execute is not original
        finally:
            watcher.disable()
        assert BaseCommand.execute is original

    def test_a_second_instance_never_double_patches(self):
        original = BaseCommand.execute
        watcher1 = CommandWatcher()
        watcher1.enable()
        try:
            patched = BaseCommand.execute
            watcher2 = CommandWatcher()
            watcher2.enable()
            try:
                assert BaseCommand.execute is patched
            finally:
                watcher2.disable()
            # watcher2 never installed anything, so it must not have restored
            # the pristine execute out from under watcher1.
            assert BaseCommand.execute is patched
        finally:
            watcher1.disable()
        assert BaseCommand.execute is original


class TestUnlistedCommand(TestCase):
    def test_records_nothing(self):
        with override_settings(DJANGO_INSPECTOR={"COMMAND_WATCH_LIST": []}):
            call_command("inspector_probe")
        assert not Event.objects.filter(event_type="command.completed").exists()


class TestNeverTraced(TestCase):
    def test_never_traced_names_record_nothing_even_when_listed(self):
        assert "migrate" in NEVER_TRACED
        with override_settings(DJANGO_INSPECTOR={"COMMAND_WATCH_LIST": ["migrate"]}):
            call_command("migrate", "django_inspector", verbosity=0, interactive=False)
        assert not Event.objects.filter(event_type="command.completed").exists()


class TestTracedCommand(TestCase):
    @WATCH_PROBE
    def test_produces_a_kept_trace_with_the_full_anchor_and_its_child_events(self):
        call_command("inspector_probe", queries=2, log=True)
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["command"] == "inspector_probe"
        assert anchor.metadata["status"] == "ok"
        assert anchor.metadata["exit_code"] == 0
        assert anchor.metadata["duration_ms"] is not None
        assert "options" in anchor.metadata
        assert "args" in anchor.metadata
        assert anchor.metadata["stdout"] == ""
        assert anchor.metadata["stdout_truncated"] is False

        trace_id = anchor.trace_id
        types = set(Event.objects.filter(trace_id=trace_id).values_list("event_type", flat=True))
        assert "sql.query" in types
        assert "log.record" in types
        assert "command.completed" in types

    @WATCH_PROBE
    def test_options_are_captured(self):
        call_command("inspector_probe", stdout_text="hi")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["options"]["stdout_text"] == "hi"
        assert "stdout" not in anchor.metadata["options"]
        assert "stderr" not in anchor.metadata["options"]

    @WATCH_PROBE
    def test_sensitive_options_are_masked(self):
        call_command("inspector_probe", password="hunter2")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["options"]["password"] == "***REDACTED***"


class TestOutputCapture(TestCase):
    @WATCH_PROBE
    def test_stdout_and_stderr_are_captured_and_the_user_still_gets_everything(self):
        import io

        out, err = io.StringIO(), io.StringIO()
        call_command("inspector_probe", stdout_text="hello out", stderr_text="hello err", stdout=out, stderr=err)

        assert "hello out" in out.getvalue()
        assert "hello err" in err.getvalue()

        anchor = Event.objects.get(event_type="command.completed")
        assert "hello out" in anchor.metadata["stdout"]
        assert "hello err" in anchor.metadata["stderr"]

    @WATCH_PROBE
    def test_output_is_truncated_past_the_cap(self):
        import io

        from django_inspector.watchers.command import MAX_OUTPUT_CHARS

        out = io.StringIO()
        text = "x" * (MAX_OUTPUT_CHARS + 500)
        call_command("inspector_probe", stdout_text=text, stdout=out)

        assert out.getvalue().count("x") == MAX_OUTPUT_CHARS + 500  # the user gets it all
        anchor = Event.objects.get(event_type="command.completed")
        assert len(anchor.metadata["stdout"]) == MAX_OUTPUT_CHARS
        assert anchor.metadata["stdout_truncated"] is True

    @WATCH_PROBE
    def test_stdout_is_masked(self):
        import io

        out = io.StringIO()
        call_command("inspector_probe", stdout_text="token=4111111111111111", stdout=out)
        anchor = Event.objects.get(event_type="command.completed")
        assert "4111111111111111" not in anchor.metadata["stdout"]
        assert "4111111111111111" in out.getvalue()  # the user still sees the real output


class TestExitCodes(TestCase):
    @WATCH_PROBE
    def test_success_is_exit_code_zero(self):
        call_command("inspector_probe")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["exit_code"] == 0
        assert anchor.metadata["status"] == "ok"

    @WATCH_PROBE
    def test_command_error_uses_its_returncode(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="probe command error"):
            call_command("inspector_probe", fail="commanderror")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["exit_code"] == 3
        assert anchor.metadata["status"] == "error"

    @WATCH_PROBE
    def test_system_exit_zero_is_treated_as_success(self):
        with pytest.raises(SystemExit) as exc_info:
            call_command("inspector_probe", fail="systemexit0")
        assert exc_info.value.code == 0
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["exit_code"] == 0
        assert anchor.metadata["status"] == "ok"

    @WATCH_PROBE
    def test_system_exit_nonzero_is_a_failure(self):
        with pytest.raises(SystemExit) as exc_info:
            call_command("inspector_probe", fail="systemexit2")
        assert exc_info.value.code == 2
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["exit_code"] == 2
        assert anchor.metadata["status"] == "error"

    @WATCH_PROBE
    def test_keyboard_interrupt_is_130(self):
        with pytest.raises(KeyboardInterrupt):
            call_command("inspector_probe", fail="keyboardinterrupt")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["exit_code"] == 130
        assert anchor.metadata["status"] == "error"

    @WATCH_PROBE
    def test_value_error_is_exit_code_one_and_reraised_unchanged(self):
        with pytest.raises(ValueError, match="probe failure"):
            call_command("inspector_probe", fail="valueerror")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.metadata["exit_code"] == 1
        assert anchor.metadata["status"] == "error"

    @WATCH_PROBE
    def test_value_error_produces_an_exception_event(self):
        with pytest.raises(ValueError):
            call_command("inspector_probe", fail="valueerror")
        trace_id = Event.objects.get(event_type="command.completed").trace_id
        exc_event = Event.objects.get(trace_id=trace_id, event_type="exception.raised")
        assert exc_event.metadata["exception_message"] == "probe failure"


class TestJoined(TestCase):
    @WATCH_PROBE
    def test_inside_trace_joins_it_without_an_early_flush(self):
        with trace("outer-job") as outer_id:
            call_command("inspector_probe")
            # Still inside the block: nothing has been flushed to the DB yet.
            assert not Event.objects.filter(trace_id=outer_id).exists()
        events = Event.objects.filter(trace_id=outer_id)
        types = set(events.values_list("event_type", flat=True))
        assert "trace.completed" in types
        assert "command.completed" in types
        assert events.filter(event_type="command.completed").count() == 1

    @WATCH_PROBE
    def test_inside_a_request_joins_it(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from django_inspector.middleware import InspectorMiddleware

        captured = {}

        def view(request):
            captured["trace_id"] = request.headers.get("X-Inspector-Trace")
            call_command("inspector_probe")
            return HttpResponse("ok")

        InspectorMiddleware(view)(RequestFactory().get("/x/"))
        request_event = Event.objects.get(event_type="request.completed")
        anchor = Event.objects.get(event_type="command.completed")
        assert anchor.trace_id == request_event.trace_id


class TestDisabledInspector(TestCase):
    @override_settings(DJANGO_INSPECTOR={"INSPECTOR_ENABLED": False, "COMMAND_WATCH_LIST": ["inspector_probe"]})
    def test_commands_run_untouched(self):
        import io

        out = io.StringIO()
        call_command("inspector_probe", stdout_text="still works", stdout=out)
        assert "still works" in out.getvalue()
        assert not Event.objects.exists()


class TestMetadataBuildFailure(TestCase):
    @WATCH_PROBE
    def test_a_failing_metadata_build_does_not_break_the_command(self):
        import io
        from unittest import mock

        import django_inspector.watchers.command as command_module

        out = io.StringIO()
        with mock.patch.object(command_module, "_safe_options", side_effect=RuntimeError("boom")):
            with self.assertLogs("django_inspector", level=logging.WARNING):
                call_command("inspector_probe", stdout_text="still runs", stdout=out)
        assert "still runs" in out.getvalue()
