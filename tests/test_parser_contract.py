"""Deterministic examples for the swappable parser adapter boundary."""

import hashlib

import pytest

from aksharamd.parser_contract import ParsedArtifact, ParserInput


def _source(text: str) -> ParserInput:
    data = text.encode("utf-8")
    return ParserInput("invoice-1", hashlib.sha256(data).hexdigest(), "text/plain", data)


class UppercaseParser:
    def parse(self, source: ParserInput) -> ParsedArtifact:
        return ParsedArtifact(
            source.source_id, source.source_hash, "uppercase", "1", "default", "text/markdown",
            source.data.decode().upper(),
        )


class PreviewParser:
    def parse(self, source: ParserInput) -> ParsedArtifact:
        return ParsedArtifact(
            source.source_id, source.source_hash, "preview", "2", "short", "text/markdown",
            source.data.decode().upper()[:5], truncated=True, preview=True,
        )


@pytest.mark.parametrize("parser,expected,truncated", [
    (UppercaseParser(), "HELLO WORLD", False),
    (PreviewParser(), "HELLO", True),
])
def test_in_memory_parsers_return_complete_provenance(parser, expected, truncated):
    result = parser.parse(_source("hello world"))
    assert result.content == expected
    assert result.source_id == "invoice-1"
    assert result.source_hash == _source("hello world").source_hash
    assert result.truncated is truncated
    assert result.media_type == "text/markdown"


def test_input_rejects_tampered_source_hash():
    with pytest.raises(ValueError, match="source_hash"):
        ParserInput("invoice-1", "0" * 64, "text/plain", b"hello")


def test_input_snapshots_mutable_bytearray():
    data = bytearray(b"hello")
    source = ParserInput("invoice-1", hashlib.sha256(data).hexdigest(), "text/plain", data)
    data[:] = b"world"
    assert source.data == b"hello"


def test_artifact_metadata_is_an_immutable_snapshot():
    metadata = {"engine": "local"}
    artifact = ParsedArtifact("invoice-1", "0" * 64, "parser", "1", None, "text/plain", "hello", metadata=metadata)
    metadata["engine"] = "changed"
    assert artifact.metadata["engine"] == "local"
    with pytest.raises(TypeError):
        artifact.metadata["new"] = "value"


def test_preview_requires_explicit_truncation():
    with pytest.raises(ValueError, match="truncated"):
        ParsedArtifact("invoice-1", "0" * 64, "parser", "1", None, "text/plain", "hello", preview=True)
