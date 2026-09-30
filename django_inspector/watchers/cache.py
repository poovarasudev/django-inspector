"""
Cache Watcher — captures Django cache operations with key, alias, backend,
TTL, hit/miss status and timing.

Wraps methods on every backend class configured in settings.CACHES at the
class level, so per-thread backend instances and the async API (which calls
the sync methods through sync_to_async) are all covered.

Requirements: CACHE-01..CACHE-07
"""

import functools
import logging
import time
from contextvars import ContextVar

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.utils.module_loading import import_string

from django_inspector.conf import inspector_settings
from django_inspector.tracing.context import get_current_trace_id
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms, extract_origin

logger = logging.getLogger("django_inspector")

# Positional parameter names of each wrapped method, per Django's BaseCache.
# Used to read arguments however the caller passed them; extra backend-specific
# arguments are passed through untouched.
_PARAMS = {
    "get": ("key", "default", "version"),
    "set": ("key", "value", "timeout", "version"),
    "add": ("key", "value", "timeout", "version"),
    "delete": ("key", "version"),
    "clear": (),
    "get_many": ("keys", "version"),
    "set_many": ("data", "timeout", "version"),
    "delete_many": ("keys", "version"),
}

WRAPPED_METHODS = tuple(_PARAMS)
CACHE_EVENT_TYPES = tuple("cache." + name for name in WRAPPED_METHODS)
MAX_RECORDED_KEYS = 100
_MULTI_KEY_OPERATIONS = ("get_many", "set_many", "delete_many")

# True while a wrapped cache call runs in this context. Nested calls
# (e.g. BaseCache.get_many -> get) pass straight through, so only the
# outermost operation is recorded.
_in_cache_op = ContextVar("inspector_in_cache_op", default=False)

# Passed to get() as `default` so a miss can't be confused with a stored None.
_MISS = object()
_UNRESOLVED = object()


class CacheWatcher(BaseWatcher):
    """
    Captures cache operations by wrapping backend classes.

    - CACHE-01: get — key, alias, hit/miss, duration
    - CACHE-02: set/add — key, alias, TTL, value size (never the value)
    - CACHE-03: delete/clear — key ("*" for clear), alias
    - CACHE-04: get_many/set_many/delete_many — one event with keys and key_count
    - CACHE-05: trace correlation via BaseWatcher.record()
    - CACHE-06: installs and removes cleanly; idempotent
    - CACHE-07: off by default (conf.DEFAULTS["WATCHERS"]["cache"])
    """

    watcher_name = "cache"

    def __init__(self):
        super().__init__()
        # (class, method name, original function or None if it was inherited)
        self._patches = []

    def install_hooks(self):
        for cls in _configured_backend_classes():
            for name in WRAPPED_METHODS:
                current = getattr(cls, name, None)
                if current is None or getattr(current, "_inspector_wrapped", False):
                    continue
                own = name in cls.__dict__
                setattr(cls, name, _make_wrapper(self, name, current))
                self._patches.append((cls, name, current if own else None))

    def remove_hooks(self):
        for cls, name, original in reversed(self._patches):
            if original is None:
                try:
                    delattr(cls, name)
                except AttributeError:
                    pass
            else:
                setattr(cls, name, original)
        self._patches = []

    def record_operation(self, cache, operation, bound, result, duration_ms, error):
        """Build and record one cache.* event. Never raises unless INSPECTOR_RAISE_ERRORS."""
        try:
            origin = extract_origin()
            metadata = {
                "operation": operation,
                "alias": _resolve_alias(cache),
                "backend": type(cache).__module__ + "." + type(cache).__qualname__,
                "duration_ms": duration_ms,
                "origin_file": origin["file"],
                "origin_line": origin["line"],
                "origin_function": origin["function"],
            }
            metadata.update(_operation_fields(cache, operation, bound, result, error is None))
            if error is not None:
                metadata["error"] = "%s: %s" % (type(error).__name__, error)
            self.record("cache." + operation, metadata)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording cache.%s event", operation, exc_info=True)


def _make_wrapper(watcher, operation, original):
    @functools.wraps(original)
    def wrapper(cache, *args, **kwargs):
        if _in_cache_op.get() or get_current_trace_id() is None:
            return original(cache, *args, **kwargs)

        try:
            bound, call_args, call_kwargs = _prepare_call(operation, args, kwargs)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error preparing cache.%s event", operation, exc_info=True)
            return original(cache, *args, **kwargs)

        token = _in_cache_op.set(True)
        start = time.monotonic()
        try:
            result = original(cache, *call_args, **call_kwargs)
        except Exception as exc:
            _in_cache_op.reset(token)
            watcher.record_operation(cache, operation, bound, None, elapsed_ms(start), exc)
            raise
        except BaseException:
            _in_cache_op.reset(token)
            raise
        _in_cache_op.reset(token)
        watcher.record_operation(cache, operation, bound, result, elapsed_ms(start), None)

        if operation == "get" and result is _MISS:
            return bound.get("default")
        return result

    wrapper._inspector_wrapped = True
    return wrapper


def _prepare_call(operation, args, kwargs):
    """
    Return (bound, call_args, call_kwargs): the named arguments of the call and
    the arguments to pass to the original. For get(), the caller's default is
    swapped for _MISS so a miss is detectable. For get_many()/delete_many(), an
    iterator of keys is materialised once so both the backend and the event
    see every key.
    """
    bound = dict(zip(_PARAMS[operation], args))
    bound.update(kwargs)
    call_args, call_kwargs = args, kwargs
    if operation == "get":
        call_args, call_kwargs = _replace_arg(operation, call_args, call_kwargs, "default", _MISS)
    elif operation in ("get_many", "delete_many") and "keys" in bound:
        keys = list(bound["keys"])
        bound["keys"] = keys
        call_args, call_kwargs = _replace_arg(operation, call_args, call_kwargs, "keys", keys)
    return bound, call_args, call_kwargs


def _replace_arg(operation, args, kwargs, name, value):
    """Replace one named argument, whether it was passed positionally or by keyword."""
    index = _PARAMS[operation].index(name)
    if len(args) > index:
        return args[:index] + (value,) + args[index + 1:], kwargs
    return args, dict(kwargs, **{name: value})


def _operation_fields(cache, operation, bound, result, succeeded):
    """Per-operation metadata. Values are never recorded — only their type and size."""
    if operation in _MULTI_KEY_OPERATIONS:
        if operation == "set_many":
            keys = list(bound.get("data") or {})
        else:
            keys = list(bound.get("keys") or [])
        fields = {
            "keys": [str(k) for k in keys[:MAX_RECORDED_KEYS]],
            "key_count": len(keys),
        }
        if operation == "get_many" and succeeded:
            fields["hit_count"] = len(result)
            fields["miss_count"] = len(keys) - len(result)
        elif operation == "set_many":
            fields["ttl_seconds"] = _ttl_seconds(cache, bound.get("timeout", DEFAULT_TIMEOUT))
            if succeeded:
                fields["failed_keys"] = [str(k) for k in (result or [])]
        return fields
    if operation == "clear":
        return {"key": "*"}
    fields = {"key": str(bound.get("key"))}
    if operation == "get":
        if succeeded:
            fields["hit"] = result is not _MISS
    elif operation in ("set", "add"):
        value = bound.get("value")
        fields["ttl_seconds"] = _ttl_seconds(cache, bound.get("timeout", DEFAULT_TIMEOUT))
        fields["value_type"] = type(value).__name__
        fields["value_size_bytes"] = _value_size(value)
        if operation == "add" and succeeded:
            fields["stored"] = bool(result)
    elif operation == "delete" and succeeded:
        fields["deleted"] = bool(result)
    return fields


def _ttl_seconds(cache, timeout):
    """The effective TTL in seconds; None means the entry never expires."""
    if timeout is DEFAULT_TIMEOUT:
        return getattr(cache, "default_timeout", None)
    return timeout


def _value_size(value):
    """Size in bytes for str/bytes values; None for anything else (no second pickle)."""
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return None


def _resolve_alias(cache):
    """The CACHES alias this backend instance serves, memoised on the instance."""
    alias = getattr(cache, "_inspector_alias", _UNRESOLVED)
    if alias is not _UNRESOLVED:
        return alias
    alias = None
    for name in settings.CACHES:
        try:
            if caches[name] is cache:
                alias = name
                break
        except Exception:
            continue
    try:
        cache._inspector_alias = alias
    except AttributeError:
        pass
    return alias


def _configured_backend_classes():
    """Unique backend classes named in settings.CACHES; unimportable ones are skipped."""
    classes = []
    for alias, config in settings.CACHES.items():
        try:
            cls = import_string(config["BACKEND"])
        except Exception:
            logger.warning(
                "inspector: cannot import cache backend for alias %r; it will not be watched",
                alias,
                exc_info=True,
            )
            continue
        if cls not in classes:
            classes.append(cls)
    return classes


register("cache", CacheWatcher)
