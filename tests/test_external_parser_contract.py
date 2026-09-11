"""Opt-in contract exercise for a real, optional external parser.

The test is deliberately local and small.  It does not install packages,
download models, call a service, or invoke an LLM.  MarkItDown is an optional
comparison parser, so environments without the evaluation extra skip the
exercise while retaining the contract in source control.
"""
from __future__ import annotations

import hashlib
import importlib.util
from importlib.metadata import version
from pathlib import Path

import pytest

from aksharamd.assessment.models import CandidateArtifact, SourceArtifact
from aksharamd.assessment.service import Assessor
from aksharamd.parser_contract import ParsedArtifact, ParserAdapter, ParserInput


class MarkItDownAdapter:
    """Small bridge proving an external parser uses the shared contract."""

    def __init__(self, path: Path):
        self.path = path

    def parse(self, source: ParserInput) -> ParsedArtifact:
        from markitdown import MarkItDown

        if self.path.read_bytes() != source.data:
            raise ValueError("parser path does not match ParserInput source")
        converted = MarkItDown().convert(str(self.path))
        return ParsedArtifact(
            source_id=source.source_id,
            source_hash=source.source_hash,
            parser_name="markitdown",
            parser_version=version("markitdown"),
            parser_configuration_id="local-default",
            media_type="text/markdown",
            content=converted.markdown,
        )


@pytest.mark.slow
def test_markitdown_output_enters_canonical_artifact_and_assessment_contract(tmp_path):
    """Run MarkItDown on a local fixture and assess its exact emitted bytes."""
    if importlib.util.find_spec("markitdown") is None:
        pytest.skip("optional MarkItDown dependency is not installed")

    # Markdown keeps the source inside the current source-preservation policy
    # so the result can reach ACCEPT rather than being forced to abstain for
    # an unsupported source media type.
    source_path = tmp_path / "invoice.md"
    source_path.write_text(
        "# Invoice INV-42\n\nTotal: $18.50\n",
        encoding="utf-8",
    )
    source = SourceArtifact.from_path(source_path, logical_id="fixture/invoice.md")

    source_input = ParserInput(source.logical_id, source.content_hash, source.media_type, source.data)
    adapter: ParserAdapter = MarkItDownAdapter(source_path)
    parsed = adapter.parse(source_input)
    markdown = parsed.content
    candidate_bytes = markdown.encode("utf-8")
    candidate = CandidateArtifact(
        content_hash=hashlib.sha256(candidate_bytes).hexdigest(),
        byte_size=len(candidate_bytes),
        media_type="text/markdown",
        logical_id="fixture/invoice.md",
        storage_reference="document.md",
        data=candidate_bytes,
        parser_name=parsed.parser_name,
        parser_version=parsed.parser_version,
        parser_configuration_id=parsed.parser_configuration_id,
        declared_truncated=parsed.truncated,
        original_source_hash=parsed.source_hash,
    )

    result = Assessor().assess(candidate=candidate, source=source)

    assert "INV-42" in markdown
    assert "18.50" in markdown
    assert result.source_hash == source.content_hash
    assert result.candidate_hash == candidate.content_hash
    assert result.disposition.value == "ACCEPT"
    assert result.dimensions["conversion_fidelity"].verdict.value == "pass"
    assert result.dimensions["content_integrity"].verdict.value == "pass"


def test_external_adapter_rejects_source_path_mismatch(tmp_path):
    if importlib.util.find_spec("markitdown") is None:
        pytest.skip("optional MarkItDown dependency is not installed")
    source_path = tmp_path / "source.md"
    other_path = tmp_path / "other.md"
    source_path.write_text("source", encoding="utf-8")
    other_path.write_text("other", encoding="utf-8")
    source = SourceArtifact.from_path(source_path)
    source_input = ParserInput(source.logical_id, source.content_hash, source.media_type, source.data)
    with pytest.raises(ValueError, match="does not match"):
        MarkItDownAdapter(other_path).parse(source_input)


def test_partial_adapter_output_is_explicitly_marked_preview():
    source = ParserInput("fixture", hashlib.sha256(b"").hexdigest(), "text/plain", b"")
    parsed = ParsedArtifact(source.source_id, source.source_hash, "partial", None, None, "text/markdown", "head", truncated=True, preview=True)
    assert parsed.truncated and parsed.preview


def test_partial_bridge_is_held_even_when_preview_text_matches_source():
    text = "The complete answer."
    data = text.encode("utf-8")
    source = SourceArtifact(
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        media_type="text/markdown",
        data=data,
    )
    source_input = ParserInput("fixture/preview-source.md", source.content_hash, source.media_type, data)
    parsed = ParsedArtifact(
        source_input.source_id,
        source_input.source_hash,
        "preview-parser",
        "1",
        "preview-only",
        "text/markdown",
        text,
        truncated=True,
        preview=True,
    )
    candidate_data = parsed.content.encode("utf-8")
    candidate = CandidateArtifact(
        content_hash=hashlib.sha256(candidate_data).hexdigest(),
        byte_size=len(candidate_data),
        media_type=parsed.media_type,
        data=candidate_data,
        parser_name=parsed.parser_name,
        parser_version=parsed.parser_version,
        parser_configuration_id=parsed.parser_configuration_id,
        declared_truncated=parsed.truncated,
        original_source_hash=parsed.source_hash,
    )
    result = Assessor().assess(candidate=candidate, source=source)
    assert result.disposition.value == "HOLD"
    assert any(f.code == "CANDIDATE_DECLARED_TRUNCATED" for f in result.dimensions["conversion_fidelity"].findings)
