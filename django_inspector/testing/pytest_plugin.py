"""
pytest11 entry point — an ``assert_queries`` fixture and an
``inspector_queries`` marker over django_inspector.testing.guard.assert_queries.

pytest loads every registered plugin (including this one) before Django
settings are configured, so nothing here imports Django or django_inspector
at module import time: both are imported lazily, inside the fixture and the
hook, at the point they're actually used.

Requirements: TEST-03
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "inspector_queries(max=None, no_n_plus_one=False, no_duplicates=False, using=None): "
        "wrap the whole test call in an assert_queries() guard.",
    )


@pytest.fixture
def assert_queries():
    """Returns the assert_queries factory, so a test can write ``with assert_queries(max=3): ...``."""
    from django_inspector.testing.guard import assert_queries as _assert_queries

    return _assert_queries


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    marker = item.get_closest_marker("inspector_queries")
    if marker is None:
        yield
        return

    from django_inspector.testing.guard import assert_queries as _assert_queries

    guard = _assert_queries(*marker.args, **marker.kwargs)
    guard.__enter__()
    outcome = yield
    if outcome.excinfo is None:
        try:
            guard.__exit__(None, None, None)
        except BaseException as exc:  # reported as the test's own failure
            outcome.force_exception(exc)
    else:
        guard.__exit__(*outcome.excinfo)
