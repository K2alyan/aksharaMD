"""Deterministic examples for the swappable parser adapter boundary."""

import hashlib

import pytest

from aksharamd.parser_contract import ParsedArtifact, ParserInput


def _source(text: str) -> ParserInput:
    data = text.encode("utf-8")
    return ParserInput("invoice-1", hashlib.sha256(data).hexdigest(), "text/plain", data)


def _artifact(source: ParserInput, content: str, *, parser_name: str = "uppercase",
              declared_truncated: bool = False) -> ParsedArtifact:
    body = content.encode("utf-8")
    return ParsedArtifact(
        source_id=source.source_id,
        source_hash=source.source_hash,
        content=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        content_mime_type="text/markdown",
        parser_name=parser_name,
        parser_version="1",
        parser_configuration_id="default",
        declared_truncated=declared_truncated,
    )


class UppercaseParser:
    def parse(self, source: ParserInput) -> ParsedArtifact:
        return _artifact(source, source.data.decode().upper())


class PreviewParser:
    def parse(self, source: ParserInput) -> ParsedArtifact:
        return _artifact(
            source,
            source.data.decode().upper()[:5],
            parser_name="preview",
            declared_truncated=True,
        )


@pytest.mark.parametrize("parser,expected,declared_truncated", [
    (UppercaseParser(), "HELLO WORLD", False),
    (PreviewParser(), "HELLO", True),
])
def test_in_memory_parsers_return_complete_provenance(parser, expected, declared_truncated):
    result = parser.parse(_source("hello world"))
    assert result.content == expected.encode("utf-8")
    assert result.source_id == "invoice-1"
    assert result.source_hash == _source("hello world").source_hash
    assert result.declared_truncated is declared_truncated
    assert result.content_mime_type == "text/markdown"
    assert result.content_hash == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_input_rejects_tampered_source_hash():
    with pytest.raises(ValueError, match="source_hash"):
        ParserInput("invoice-1", "0" * 64, "text/plain", b"hello")


def test_input_snapshots_mutable_bytearray():
    data = bytearray(b"hello")
    source = ParserInput("invoice-1", hashlib.sha256(data).hexdigest(), "text/plain", data)
    data[:] = b"world"
    assert source.data == b"hello"


def test_artifact_metadata_is_an_immutable_snapshot():
    content = b"hello"
    content_hash = hashlib.sha256(content).hexdigest()
    metadata = {"engine": "local"}
    artifact = ParsedArtifact(
        source_id="invoice-1",
        source_hash="0" * 64,
        content=content,
        content_hash=content_hash,
        content_mime_type="text/plain",
        parser_name="parser",
        parser_version="1",
        parser_configuration_id=None,
        metadata=metadata,
    )
    metadata["engine"] = "changed"
    assert artifact.metadata["engine"] == "local"
    with pytest.raises(TypeError):
        artifact.metadata["new"] = "value"


def test_artifact_rejects_wrong_content_hash():
    body = b"hello"
    with pytest.raises(ValueError, match="content_hash"):
        ParsedArtifact(
            source_id="invoice-1",
            source_hash="0" * 64,
            content=body,
            content_hash="0" * 64,  # does not match sha256(body)
            content_mime_type="text/markdown",
            parser_name="parser",
        )


def test_artifact_snapshots_mutable_content_bytearray():
    body = bytearray(b"hello")
    content_hash = hashlib.sha256(body).hexdigest()
    artifact = ParsedArtifact(
        source_id="invoice-1",
        source_hash="0" * 64,
        content=body,
        content_hash=content_hash,
        content_mime_type="text/markdown",
        parser_name="parser",
    )
    body[:] = b"world"
    assert artifact.content == b"hello"
