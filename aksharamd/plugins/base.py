from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..context import CompilationContext


class BasePlugin(ABC):
    name: ClassVar[str]
    version: ClassVar[str] = "0.1.0"
    priority: ClassVar[int] = 50
    supported_types: ClassVar[list[str]] = ["*"]

    @abstractmethod
    def execute(self, ctx: CompilationContext) -> CompilationContext:
        ...


class ParserPlugin(BasePlugin):
    """Converts raw file content into a Document with blocks."""
    pass


class CleanerPlugin(BasePlugin):
    """Removes boilerplate and normalizes content."""
    pass


class OptimizerPlugin(BasePlugin):
    """Reduces token count while preserving semantics."""
    pass


class ValidatorPlugin(BasePlugin):
    """Validates document structure and completeness."""
    pass


class ChunkerPlugin(BasePlugin):
    """Splits document into semantic chunks."""
    pass


class ExporterPlugin(BasePlugin):
    """Writes output files to disk."""
    pass
