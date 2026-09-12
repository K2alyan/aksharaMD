"""MarkItDown wrapped as a ParserAdapter for the parsed-vs-raw eval.

Feeding MarkItDown output through ``Compiler(parser_adapter=...)`` means
the readiness score for the MarkItDown arm is computed by the exact
same code path as the AksharaMD arm, so cross-arm correlation compares
apples to apples. See ``benchmarks/parsed_vs_raw/README.md`` for the
motivation.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from aksharamd.parser_contract import ParsedArtifact, ParserInput


@dataclass
class MarkItDownAdapter:
    """Thin ParserAdapter wrapper around MarkItDown's PDF->Markdown conversion."""

    _parser_version: str = "unknown"

    def __post_init__(self) -> None:
        try:
            import markitdown  # noqa: F401
            # markitdown does not expose __version__ consistently; keep "unknown".
        except ImportError as exc:
            raise RuntimeError(
                "MarkItDownAdapter requires the 'markitdown' package. "
                "Install with: pip install markitdown"
            ) from exc

    def parse(self, source: ParserInput) -> ParsedArtifact:
        from markitdown import MarkItDown

        md = MarkItDown()
        # MarkItDown accepts a stream or a path; using BytesIO avoids
        # touching disk on the hot path.
        stream = io.BytesIO(source.data)
        result = md.convert_stream(stream)
        text = (result.text_content or "").encode("utf-8")
        return ParsedArtifact(
            source_id=source.source_id,
            source_hash=source.source_hash,
            content=text,
            content_hash=hashlib.sha256(text).hexdigest(),
            content_mime_type="text/markdown",
            parser_name="markitdown",
            parser_version=self._parser_version,
            parser_configuration_id="default",
            declared_truncated=False,
        )
