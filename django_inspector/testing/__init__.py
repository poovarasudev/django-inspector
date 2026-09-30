"""
django_inspector.testing — assert_queries() and its exceptions.

Attribute access is lazy (PEP 562): merely importing this package (which
happens whenever Python resolves django_inspector.testing.pytest_plugin, the
pytest11 entry point) must not import guard.py, since guard.py imports
Django's ORM and pytest loads plugins before settings are configured.
"""

_EXPORTS = ("assert_queries", "QueryBudgetExceeded", "NPlusOneDetected", "CapturedQuery")

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        from django_inspector.testing import guard

        return getattr(guard, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
