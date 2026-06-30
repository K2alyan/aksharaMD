from __future__ import annotations

from .base import BasePlugin, ParserPlugin

_parsers: dict[str, type[ParserPlugin]] = {}
_plugin_classes: list[type[BasePlugin]] = []


def register_parser(ext: str, cls: type[ParserPlugin]) -> None:
    _parsers[ext.lower()] = cls


def get_parser(file_type: str) -> ParserPlugin | None:
    cls = _parsers.get(file_type.lower())
    return cls() if cls else None


def register_plugin(cls: type[BasePlugin]) -> None:
    if cls not in _plugin_classes:
        _plugin_classes.append(cls)


def get_plugins_of_type(plugin_type: type[BasePlugin]) -> list[BasePlugin]:
    instances = [
        cls() for cls in _plugin_classes
        if issubclass(cls, plugin_type) and cls is not plugin_type
    ]
    return sorted(instances, key=lambda p: p.priority)
