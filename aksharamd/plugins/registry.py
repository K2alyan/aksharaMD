from __future__ import annotations

from .base import BasePlugin, ParserPlugin

_parsers: dict[str, type[ParserPlugin]] = {}
_plugin_classes: list[type[BasePlugin]] = []
_plugin_cache: dict[type, list] = {}


def snapshot() -> tuple[dict[str, type[ParserPlugin]], list[type[BasePlugin]]]:
    """Capture the registered plugin definitions for an isolated compiler.

    Registration is process-wide for discovery and backwards compatibility,
    but a compiler must not change behaviour when another caller registers a
    plugin later in the process.
    """
    return dict(_parsers), list(_plugin_classes)


def register_parser(ext: str, cls: type[ParserPlugin]) -> None:
    _parsers[ext.lower()] = cls


def get_parser(file_type: str) -> ParserPlugin | None:
    cls = _parsers.get(file_type.lower())
    return cls() if cls else None


def register_plugin(cls: type[BasePlugin]) -> None:
    if cls not in _plugin_classes:
        _plugin_classes.append(cls)
        # A stage may already have been used before an application loads its
        # extension. Rebuild affected stages on their next lookup while keeping
        # unrelated cached plugin instances intact.
        for plugin_type in list(_plugin_cache):
            if issubclass(cls, plugin_type):
                del _plugin_cache[plugin_type]


def get_registered_extensions() -> list[str]:
    """Return all file extensions with a registered parser (without leading dot)."""
    return list(_parsers.keys())


def get_plugins_of_type(plugin_type: type[BasePlugin]) -> list[BasePlugin]:
    if plugin_type not in _plugin_cache:
        instances = [
            cls() for cls in _plugin_classes
            if issubclass(cls, plugin_type) and cls is not plugin_type
        ]
        _plugin_cache[plugin_type] = sorted(instances, key=lambda p: p.priority)
    return _plugin_cache[plugin_type]


def _clear_plugin_cache() -> None:
    _plugin_cache.clear()
