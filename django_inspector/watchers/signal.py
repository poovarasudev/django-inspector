"""
Signal Watcher — captures dispatches of the signals listed in SIGNAL_WATCH_LIST
with sender, receiver count and per-receiver timing in call order.

Each watched Signal instance gets instance-level wrappers for send/send_robust/
asend/asend_robust and _live_receivers. During a dispatch, _live_receivers hands
Django timed wrappers around each receiver, so Django's own dispatch code runs
unchanged; the wrappers are swapped back for the original receivers in the
returned (receiver, response) pairs.

Requirements: SIGL-01..SIGL-06
"""

import functools
import logging
import time
from contextvars import ContextVar
from typing import Any, Optional

from django.dispatch import Signal
from django.utils.module_loading import import_string

from django_inspector.conf import inspector_settings
from django_inspector.watchers.base import BaseWatcher
from django_inspector.watchers.registry import register
from django_inspector.watchers.utils import elapsed_ms

logger = logging.getLogger("django_inspector")

_DISPATCH_METHODS = ("send", "send_robust", "asend", "asend_robust")

# The dispatch in progress in this context; each send* call sets its own, so
# nested dispatches are tracked separately.
_current_dispatch: ContextVar[Optional[Any]] = ContextVar("inspector_signal_dispatch", default=None)


class SignalWatcher(BaseWatcher):
    """
    Captures signal dispatches for the signals in SIGNAL_WATCH_LIST.

    - SIGL-01: signal (dotted path), sender, receiver count
    - SIGL-02: per-receiver execution time, including failures
    - SIGL-03: receivers listed in call order
    - SIGL-04: trace correlation via BaseWatcher.record()
    - SIGL-05: django_inspector's own receivers are not timed or listed
    - SIGL-06: only signals named in SIGNAL_WATCH_LIST; off by default
    """

    watcher_name = "signal"

    def __init__(self):
        super().__init__()
        # (signal, [attribute names this watcher set on it])
        self._patched = []

    def install_hooks(self):
        for name in inspector_settings.SIGNAL_WATCH_LIST or []:
            signal = _resolve_signal(name)
            if signal is None or "_inspector_signal_name" in signal.__dict__:
                continue
            signal._inspector_signal_name = name
            attrs = ["_inspector_signal_name", "_live_receivers"]
            signal._live_receivers = _make_live_receivers(signal, signal._live_receivers)
            for method in _DISPATCH_METHODS:
                original = getattr(signal, method, None)
                if original is None:
                    continue
                setattr(signal, method, _make_dispatch(self, name, signal, method, original))
                attrs.append(method)
            self._patched.append((signal, attrs))

    def remove_hooks(self):
        for signal, attrs in reversed(self._patched):
            for attr in attrs:
                signal.__dict__.pop(attr, None)
        self._patched = []

    def record_dispatch(self, name, method, sender, dispatch, duration_ms, error):
        """Build and record one signal.dispatched event. Never raises unless INSPECTOR_RAISE_ERRORS."""
        try:
            if dispatch.receiver_count == 0:
                return
            metadata = {
                "signal": name,
                "sender": _sender_name(sender),
                "receiver_count": dispatch.receiver_count,
                "receivers": [dict(entry) for entry in dispatch.called],
                "duration_ms": duration_ms,
                "method": method,
            }
            if error is not None:
                metadata["error"] = _error_text(error)
            self.record("signal.dispatched", metadata)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error recording signal.dispatched event", exc_info=True)


class _Dispatch:
    """Receivers resolved and called during one dispatch of one signal."""

    def __init__(self, signal):
        self.signal = signal
        self.resolved = False
        self.receiver_count = 0
        self.called = []  # {"receiver", "duration_ms", "error"?} in call order
        self._originals = {}  # id(wrapper) -> original receiver
        self._wrappers = []  # keeps wrappers alive so their ids stay unique

    def wrap(self, receivers, is_async):
        wrapped = []
        for receiver in receivers:
            if _is_inspector_receiver(receiver):
                wrapped.append(receiver)
                continue
            self.receiver_count += 1
            timed = _timed_async(receiver, self) if is_async else _timed_sync(receiver, self)
            self._originals[id(timed)] = receiver
            self._wrappers.append(timed)
            wrapped.append(timed)
        return wrapped

    def start(self, receiver):
        entry = {"receiver": _callable_name(receiver), "duration_ms": None}
        self.called.append(entry)
        return entry

    def unwrap(self, responses):
        return [(self._originals.get(id(receiver), receiver), response) for receiver, response in responses]


def _make_live_receivers(signal, original):
    def _live_receivers(sender):
        result = original(sender)
        dispatch = _current_dispatch.get()
        if dispatch is None or dispatch.signal is not signal or dispatch.resolved:
            return result
        dispatch.resolved = True
        try:
            if isinstance(result, tuple) and len(result) == 2:
                # Django >= 5.0: (sync_receivers, async_receivers)
                sync_receivers, async_receivers = result
                return dispatch.wrap(sync_receivers, False), dispatch.wrap(async_receivers, True)
            return dispatch.wrap(result, False)
        except Exception:
            if inspector_settings.INSPECTOR_RAISE_ERRORS:
                raise
            logger.warning("inspector: error wrapping signal receivers", exc_info=True)
            return result

    return _live_receivers


def _make_dispatch(watcher, name, signal, method, original):
    if method.startswith("a"):

        async def dispatch_async(sender, **named):
            if not watcher.is_capturing():
                return await original(sender, **named)
            dispatch = _Dispatch(signal)
            token = _current_dispatch.set(dispatch)
            start = time.monotonic()
            try:
                responses = await original(sender, **named)
            except Exception as exc:
                _current_dispatch.reset(token)
                watcher.record_dispatch(name, method, sender, dispatch, elapsed_ms(start), exc)
                raise
            except BaseException:
                _current_dispatch.reset(token)
                raise
            _current_dispatch.reset(token)
            watcher.record_dispatch(name, method, sender, dispatch, elapsed_ms(start), None)
            return dispatch.unwrap(responses)

        return dispatch_async

    def dispatch_sync(sender, **named):
        if not watcher.is_capturing():
            return original(sender, **named)
        dispatch = _Dispatch(signal)
        token = _current_dispatch.set(dispatch)
        start = time.monotonic()
        try:
            responses = original(sender, **named)
        except Exception as exc:
            _current_dispatch.reset(token)
            watcher.record_dispatch(name, method, sender, dispatch, elapsed_ms(start), exc)
            raise
        except BaseException:
            _current_dispatch.reset(token)
            raise
        _current_dispatch.reset(token)
        watcher.record_dispatch(name, method, sender, dispatch, elapsed_ms(start), None)
        return dispatch.unwrap(responses)

    return dispatch_sync


def _timed_sync(receiver, dispatch):
    @functools.wraps(receiver)
    def timed(*args, **kwargs):
        entry = dispatch.start(receiver)
        start = time.monotonic()
        try:
            return receiver(*args, **kwargs)
        except Exception as exc:
            entry["error"] = _error_text(exc)
            raise
        finally:
            entry["duration_ms"] = elapsed_ms(start)

    return timed


def _timed_async(receiver, dispatch):
    @functools.wraps(receiver)
    async def timed(*args, **kwargs):
        entry = dispatch.start(receiver)
        start = time.monotonic()
        try:
            return await receiver(*args, **kwargs)
        except Exception as exc:
            entry["error"] = _error_text(exc)
            raise
        finally:
            entry["duration_ms"] = elapsed_ms(start)

    return timed


def _resolve_signal(name):
    try:
        signal = import_string(name)
    except ImportError:
        logger.warning("inspector: SIGNAL_WATCH_LIST entry %r could not be imported", name)
        return None
    if not isinstance(signal, Signal):
        logger.warning("inspector: SIGNAL_WATCH_LIST entry %r is not a django.dispatch.Signal", name)
        return None
    return signal


def _is_inspector_receiver(receiver):
    return (getattr(receiver, "__module__", None) or "").startswith("django_inspector")


def _callable_name(obj):
    module = getattr(obj, "__module__", None) or type(obj).__module__
    qualname = getattr(obj, "__qualname__", None) or type(obj).__qualname__
    return module + "." + qualname


def _sender_name(sender):
    if sender is None or isinstance(sender, str):
        return sender
    cls = sender if isinstance(sender, type) else type(sender)
    return cls.__module__ + "." + cls.__qualname__


def _error_text(exc):
    return "%s: %s" % (type(exc).__name__, exc)


register("signal", SignalWatcher)
