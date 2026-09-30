"""
Watcher registry — central registry for all active watchers.
Watchers register themselves here on import; AppConfig.ready() imports
this module to trigger auto-discovery.
"""

_registry: dict = {}
_instances: dict = {}


def register(name: str, watcher_class):
    """Register a watcher class under the given name."""
    _registry[name] = watcher_class


def get(name: str):
    """Return a registered watcher class by name, or None."""
    return _registry.get(name)


def all_watchers() -> dict:
    """Return a copy of all registered watchers."""
    return dict(_registry)


def set_instance(name: str, watcher) -> None:
    """Remember the app's watcher instance for ``name`` (set by AppConfig.ready)."""
    _instances[name] = watcher


def get_instance(name: str):
    """The app's watcher instance for ``name``, or None. O(1): used on hot paths."""
    return _instances.get(name)
