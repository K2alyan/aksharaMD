"""Small, parser-independent contract for source and delivered artifacts.

The existing :class:`~aksharamd.plugins.base.ParserPlugin` API operates on a
``CompilationContext`` and remains unchanged. This module gives integrations
that provide their own parser a stable boundary: parsers receive immutable
source bytes and return the exact text delivered downstream.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_digest(value: str, field_name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _check_hash(value: str, data: bytes, field_name: str) -> None:
    _validate_digest(value, field_name)
    if _sha256(data) != value:
        raise ValueError(f"{field_name} does not match supplied bytes")


@dataclass(frozen=True, slots=True)
class ParserInput:
    """Immutable source supplied to a user-selected parser."""

    source_id: str
    source_hash: str
    media_type: str
    data: bytes

    def __post_init__(self) -> None:
        # Bytes are immutable, but callers may pass a bytearray at this
        # boundary despite the annotation. Snapshot it before validation so
        # later caller mutation cannot change parser input.
        data = bytes(self.data)
        object.__setattr__(self, "data", data)
        if not self.source_id.strip():
            raise ValueError("source_id must not be blank")
        if not self.media_type.strip() or "/" not in self.media_type:
            raise ValueError("media_type must be a MIME type")
        _check_hash(self.source_hash, self.data, "source_hash")


@dataclass(frozen=True, slots=True)
class ParsedArtifact:
    """Parser output, including provenance and delivery state."""

    source_id: str
    source_hash: str
    parser_name: str
    parser_version: str | None
    parser_configuration_id: str | None
    media_type: str
    content: str
    truncated: bool = False
    preview: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if not self.source_id.strip():
            raise ValueError("source_id must not be blank")
        _validate_digest(self.source_hash, "source_hash")
        if not self.parser_name.strip():
            raise ValueError("parser_name must not be blank")
        if not self.media_type.strip() or "/" not in self.media_type:
            raise ValueError("media_type must be a MIME type")
        if not isinstance(self.content, str):
            raise TypeError("content must be text")
        if self.preview and not self.truncated:
            raise ValueError("preview artifacts must declare truncated=True")


class ParserAdapter(Protocol):
    """Structural interface implemented by swappable parser integrations."""

    def parse(self, source: ParserInput) -> ParsedArtifact:
        """Convert source into the exact downstream-delivered content."""

