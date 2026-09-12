"""marker-pdf wrapped as a ParserAdapter for the parsed-vs-raw eval.

Feeding marker-pdf output through ``Compiler(parser_adapter=...)`` means
the readiness score for the marker arm is computed by the exact same
code path as the aksharamd and markitdown arms, so cross-arm correlation
compares apples to apples.

Marker's model dict is cached at module scope so a multi-doc pilot only
pays the ~15-30s load cost once.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass

from aksharamd.parser_contract import ParsedArtifact, ParserInput

_MODELS = None


def _get_models() -> dict:
    global _MODELS
    if _MODELS is None:
        from marker.models import create_model_dict  # heavy; models download on first call
        _MODELS = create_model_dict()
    return _MODELS


@dataclass
class MarkerAdapter:
    """Thin ParserAdapter wrapper around marker-pdf's PDF->Markdown conversion."""

    _parser_version: str = "unknown"

    def __post_init__(self) -> None:
        try:
            import importlib.metadata as m

            import marker  # noqa: F401
            try:
                self._parser_version = m.version("marker-pdf")
            except Exception:
                pass
        except ImportError as exc:
            raise RuntimeError(
                "MarkerAdapter requires the 'marker-pdf' package. "
                "Install with: pip install 'aksharamd[vision]'"
            ) from exc

    def parse(self, source: ParserInput) -> ParsedArtifact:
        from marker.converters.pdf import PdfConverter

        # marker's converter takes a path, not bytes. Write to a temp file.
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="marker_arm_")
        try:
            os.close(fd)
            with open(tmp_path, "wb") as fh:
                fh.write(source.data)
            converter = PdfConverter(artifact_dict=_get_models())
            rendered = converter(tmp_path)
            markdown_text = getattr(rendered, "markdown", "") or ""
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        text = markdown_text.encode("utf-8")
        return ParsedArtifact(
            source_id=source.source_id,
            source_hash=source.source_hash,
            content=text,
            content_hash=hashlib.sha256(text).hexdigest(),
            content_mime_type="text/markdown",
            parser_name="marker-pdf",
            parser_version=self._parser_version,
            parser_configuration_id="default",
            declared_truncated=False,
        )
