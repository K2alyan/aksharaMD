from __future__ import annotations

from .base import BasePlugin, ParserPlugin

_parsers: dict[str, type[ParserPlugin]] = {}
_plugin_classes: list[type[BasePlugin]] = []
_plugin_cache: dict[type, list] = {}


def register_parser(ext: str, cls: type[ParserPlugin]) -> None:
    _parsers[ext.lower()] = cls


def get_parser(file_type: str) -> ParserPlugin | None:
    cls = _parsers.get(file_type.lower())
    return cls() if cls else None


def register_plugin(cls: type[BasePlugin]) -> None:
    if cls not in _plugin_classes:
        _plugin_classes.append(cls)


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
