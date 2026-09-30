"""Tests for the test-time query guard (TEST-01..03)."""

import asyncio
from unittest import mock

import pytest
from django.db import connection
from django.test import TestCase, override_settings

from django_inspector.storage.models import Event
from django_inspector.testing import QueryBudgetExceeded, assert_queries


def _run_two_authors(n=3):
    with connection.cursor() as cursor:
        for i in range(n):
            cursor.execute("SELECT * FROM auth_user WHERE id = %s", [i])


class TestAssertQueriesBasic(TestCase):
    def test_passes_within_budget(self):
        with assert_queries(max=5) as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        assert len(guard.queries) == 1

    def test_fails_over_budget(self):
        with pytest.raises(QueryBudgetExceeded):
            with assert_queries(max=1):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.execute("SELECT 2")

    def test_no_middleware_and_no_trace_needed(self):
        """TEST-01: works with no active trace and no middleware in play."""
        assert self.client is not None  # sanity: still a normal TestCase
        with assert_queries(max=5) as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        assert len(guard.queries) == 1

    def test_works_with_inspector_disabled(self):
        with override_settings(DJANGO_INSPECTOR={"INSPECTOR_ENABLED": False}):
            with assert_queries(max=5) as guard:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            assert len(guard.queries) == 1

    def test_works_with_sql_watcher_disabled(self):
        with override_settings(DJANGO_INSPECTOR={"WATCHERS": {"sql": False}}):
            with assert_queries(max=5) as guard:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            assert len(guard.queries) == 1

    def test_writes_no_event_rows(self):
        before = Event.objects.count()
        with assert_queries(max=5):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        assert Event.objects.count() == before

    def test_guard_queries_contents(self):
        with assert_queries() as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM auth_user WHERE id = %s", [1])
        query = guard.queries[-1]
        assert "auth_user" in query.sql
        assert query.params == [1]
        assert query.alias == "default"
        assert query.duration_ms >= 0
        assert query.file is not None
        assert "test_query_guard" in query.file
        assert query.line is not None
        assert query.function is not None

    def test_block_exception_wins_over_budget_failure(self):
        with pytest.raises(ValueError):
            with assert_queries(max=0):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                raise ValueError("boom")

    def test_ignores_savepoints(self):
        from django.db import transaction

        with assert_queries() as guard:
            with transaction.atomic():
                with transaction.atomic():  # nested atomic uses a SAVEPOINT
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
        sqls = [q.sql for q in guard.queries]
        assert not any("SAVEPOINT" in s.upper() for s in sqls)

    def test_ignores_inspector_queries(self):
        with assert_queries() as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM django_inspector_event WHERE id = 1")
        assert guard.queries == []

    def test_max_none_never_fails_on_count(self):
        with assert_queries(max=None) as guard:
            with connection.cursor() as cursor:
                for i in range(10):
                    cursor.execute("SELECT %s", [i])
        assert len(guard.queries) == 10


class TestAssertQueriesNPlusOne(TestCase):
    def test_detects_n_plus_one(self):
        with pytest.raises(QueryBudgetExceeded) as excinfo:
            with assert_queries(no_n_plus_one=True):
                _run_two_authors(3)
        message = str(excinfo.value)
        assert "N+1" in message
        assert "auth_user" in message
        assert "3" in message  # the count
        assert "test_query_guard.py" in message  # file
        assert "_run_two_authors" in message  # function

    def test_two_similar_queries_do_not_trigger_n_plus_one(self):
        with assert_queries(no_n_plus_one=True) as guard:
            _run_two_authors(2)
        assert len(guard.queries) == 2

    def test_message_never_contains_param_values(self):
        with pytest.raises(QueryBudgetExceeded) as excinfo:
            with assert_queries(no_n_plus_one=True):
                with connection.cursor() as cursor:
                    for token in ("secret-token-aaa", "secret-token-bbb", "secret-token-ccc"):
                        cursor.execute("SELECT * FROM auth_user WHERE username = %s", [token])
        message = str(excinfo.value)
        assert "secret-token" not in message


class TestAssertQueriesDuplicates(TestCase):
    def test_detects_exact_duplicates(self):
        with pytest.raises(QueryBudgetExceeded) as excinfo:
            with assert_queries(no_duplicates=True):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM auth_user WHERE id = %s", [1])
                    cursor.execute("SELECT * FROM auth_user WHERE id = %s", [1])
        message = str(excinfo.value)
        assert "duplicate" in message.lower()

    def test_different_params_are_not_duplicates(self):
        with assert_queries(no_duplicates=True) as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM auth_user WHERE id = %s", [1])
                cursor.execute("SELECT * FROM auth_user WHERE id = %s", [2])
        assert len(guard.queries) == 2


class TestAssertQueriesUsing(TestCase):
    databases = {"default", "secondary"}

    def test_using_single_alias_string(self):
        with assert_queries(using="default") as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            with connections_secondary_cursor() as cursor:
                cursor.execute("SELECT 1")
        # Only the default-alias query was captured.
        assert len(guard.queries) == 1
        assert guard.queries[0].alias == "default"

    def test_using_list_of_aliases(self):
        with assert_queries(using=["default", "secondary"]) as guard:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            with connections_secondary_cursor() as cursor:
                cursor.execute("SELECT 1")
        aliases = {q.alias for q in guard.queries}
        assert aliases == {"default", "secondary"}

    def test_using_unknown_alias_raises_key_error(self):
        with pytest.raises(KeyError):
            with assert_queries(using="bogus"):
                pass


def connections_secondary_cursor():
    from django.db import connections

    return connections["secondary"].cursor()


class TestAssertQueriesDecorators(TestCase):
    def test_function_decorator(self):
        @assert_queries(max=1)
        def do_one_query():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")

        do_one_query()  # must not raise
        do_one_query()  # a second call gets a fresh guard, still passes

    def test_function_decorator_fails_over_budget(self):
        @assert_queries(max=0)
        def do_one_query():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")

        with pytest.raises(QueryBudgetExceeded):
            do_one_query()

    def test_async_function_decorator(self):
        from asgiref.sync import sync_to_async

        def _run_query():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")

        @assert_queries(max=1)
        async def do_one_query_async():
            await sync_to_async(_run_query, thread_sensitive=True)()

        asyncio.run(do_one_query_async())

    def test_testcase_method_decorator(self):
        calls = []

        class Inner(TestCase):
            @assert_queries(max=1)
            def test_it(self):
                calls.append(1)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")

        result = Inner("test_it").run()
        assert result.wasSuccessful()
        assert calls == [1]

    def test_class_decorator_wraps_test_methods(self):
        @assert_queries(max=0)
        class Inner(TestCase):
            def test_it(self):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")

            def not_a_test(self):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")

        result = Inner("test_it").run()
        assert not result.wasSuccessful()  # max=0 budget is exceeded

    def test_class_decorator_rejects_non_testcase(self):
        with pytest.raises(TypeError):
            @assert_queries(max=1)
            class NotATestCase:
                def test_it(self):
                    pass


class TestAssertQueriesArgumentValidation(TestCase):
    def test_negative_max_rejected(self):
        with pytest.raises(ValueError):
            assert_queries(max=-1)

    def test_non_int_max_rejected(self):
        with pytest.raises(TypeError):
            assert_queries(max="five")

    def test_bool_max_rejected(self):
        with pytest.raises(TypeError):
            assert_queries(max=True)


class TestAssertQueriesBookkeepingFailure(TestCase):
    def test_bookkeeping_failure_does_not_break_the_query(self):
        import django_inspector.testing.guard as guard_module

        with assert_queries() as guard:
            with mock.patch.object(guard, "_record", side_effect=RuntimeError("boom")):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")  # must not raise despite the bookkeeping failure
        # The query ran; nothing was recorded for it because bookkeeping failed.
        assert guard.queries == []
        del guard_module  # imported only to prove the module path exists


class TestNPlusOneDetectedIsExported(TestCase):
    def test_importable_from_testing_package(self):
        from django_inspector.testing import NPlusOneDetected

        assert issubclass(NPlusOneDetected, Exception)
