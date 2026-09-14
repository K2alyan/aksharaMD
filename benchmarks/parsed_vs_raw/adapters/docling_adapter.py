"""Docling wrapped as a ParserAdapter for parsed-vs-raw / eval_v1.

Prior to A.1, Docling ran on a legacy path that produced markdown but
did not route through ``Compiler(parser_adapter=...)``. The Compiler's
readiness_score for Docling was therefore ``None``, which made any
cross-parser comparison invalid (Authorization A finding PARSER-1).

This adapter runs Docling's ``DocumentConverter`` against the incoming
PDF bytes, exports markdown, and returns a ``ParsedArtifact`` that the
Compiler can consume — same instrument as MarkItDown and marker arms.
"""
from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aksharamd.parser_contract import ParsedArtifact, ParserInput


@dataclass
class DoclingAdapter:
    _parser_version: str = "unknown"

    def __post_init__(self) -> None:
        try:
            import importlib.metadata as _im
            self._parser_version = _im.version("docling")
        except Exception:
            self._parser_version = "unknown"
        try:
            import docling  # noqa: F401
            from docling.document_converter import DocumentConverter  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "DoclingAdapter requires the 'docling' package. "
                "Install with: pip install docling"
            ) from exc

    def parse(self, source: ParserInput) -> ParsedArtifact:
        from docling.document_converter import DocumentConverter

        # Docling's DocumentConverter accepts a path. Write bytes to a
        # temp file, convert, then clean up.
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(source.data)
            tmp_path = fh.name
        try:
            conv = DocumentConverter()
            result = conv.convert(tmp_path)
            markdown = result.document.export_to_markdown()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        content = (markdown or "").encode("utf-8")
        return ParsedArtifact(
            source_id=source.source_id,
            source_hash=source.source_hash,
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            content_mime_type="text/markdown",
            parser_name="docling",
            parser_version=self._parser_version,
            parser_configuration_id="default",
            declared_truncated=False,
        )
