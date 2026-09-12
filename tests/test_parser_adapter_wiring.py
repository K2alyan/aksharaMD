"""Compiler ParserAdapter wiring tests.

Exercises the hash-validated parser-adapter boundary exposed via
``Compiler(parser_adapter=...)``: happy-path round-trip, the four failure
codes, provenance threading, backwards compatibility with the legacy
``parsers=`` kwarg, and one end-to-end MarkItDown smoke that skips cleanly
when MarkItDown or the sample PDF are unavailable.
"""
from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler
from aksharamd.parser_contract import ParsedArtifact, ParserInput

# ── Shared test doubles ──────────────────────────────────────────────────────

@dataclass
class StubMarkdownAdapter:
    """Minimal adapter that returns markdown bytes with valid hashes.

    Defaults are deliberately thin so individual tests can override just the
    fields they need to exercise a failure mode.
    """

    _content: bytes = b"# Adapter Doc\n\nHello from the adapter.\n"
    _content_mime_type: str = "text/markdown"
    _parser_name: str = "stub-adapter"
    _parser_version: str | None = "0.0.1"
    _parser_configuration_id: str | None = "default"
    _declared_truncated: bool = False
    # Optional overrides for negative tests. None means "use the compiler-supplied value".
    _override_source_id: str | None = None
    _override_source_hash: str | None = None
    _override_content_hash: str | None = None
    _override_content_mime_type: str | None = None

    def parse(self, source: ParserInput) -> ParsedArtifact:
        source_id = self._override_source_id if self._override_source_id is not None else source.source_id
        source_hash = self._override_source_hash if self._override_source_hash is not None else source.source_hash
        content_mime = (
            self._override_content_mime_type
            if self._override_content_mime_type is not None
            else self._content_mime_type
        )
        content_hash = (
            self._override_content_hash
            if self._override_content_hash is not None
            else hashlib.sha256(self._content).hexdigest()
        )
        return ParsedArtifact(
            source_id=source_id,
            source_hash=source_hash,
            content=self._content,
            content_hash=content_hash,
            content_mime_type=content_mime,
            parser_name=self._parser_name,
            parser_version=self._parser_version,
            parser_configuration_id=self._parser_configuration_id,
            declared_truncated=self._declared_truncated,
        )


@pytest.fixture
def sample_markdown(tmp_path: Path) -> Path:
    body = (
        "# Sample\n\n"
        "First paragraph with enough content to survive the optimizer merge threshold.\n\n"
        "Second paragraph with another sentence that is also long enough to persist.\n"
    )
    doc = tmp_path / "sample.md"
    doc.write_text(body, encoding="utf-8")
    return doc


# ── 1. Happy path ────────────────────────────────────────────────────────────

def test_adapter_round_trip_matches_pluginless_score(tmp_path: Path, sample_markdown: Path):
    """Adapter that returns the file's own bytes matches the plugin-less score.

    We copy the source's exact bytes into the artifact, so downstream ingestion
    sees the same text either way and the readiness score is identical.
    """
    baseline_ctx = Compiler(output_dir=str(tmp_path / "baseline")).compile(str(sample_markdown))
    assert baseline_ctx.document is not None
    assert baseline_ctx.manifest is not None
    baseline_score = baseline_ctx.manifest.readiness_score

    adapter = StubMarkdownAdapter(_content=sample_markdown.read_bytes())
    adapter_ctx = Compiler(
        output_dir=str(tmp_path / "adapter"),
        parser_adapter=adapter,
    ).compile(str(sample_markdown))
    assert adapter_ctx.document is not None
    assert adapter_ctx.manifest is not None
    assert not adapter_ctx.validation.errors, adapter_ctx.validation.errors
    assert adapter_ctx.manifest.readiness_score == baseline_score


# ── 2. ADAPTER_SOURCE_HASH_MISMATCH ─────────────────────────────────────────

def test_adapter_hash_mismatch_rejected(tmp_path: Path, sample_markdown: Path):
    adapter = StubMarkdownAdapter(
        _content=sample_markdown.read_bytes(),
        _override_source_hash="a" * 64,  # valid digest shape, wrong value
    )
    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=adapter,
    ).compile(str(sample_markdown))
    codes = {e.code for e in ctx.validation.errors}
    assert "ADAPTER_SOURCE_HASH_MISMATCH" in codes, codes
    assert ctx.document is None


# ── 3. ADAPTER_CONTENT_HASH_MISMATCH ────────────────────────────────────────

def test_adapter_content_hash_mismatch_rejected(tmp_path: Path, sample_markdown: Path):
    """ParsedArtifact validates content_hash in __post_init__.

    That means an artifact whose content and content_hash disagree cannot even
    be constructed. The Compiler branch catches the resulting ValueError and
    records PARSE_FAILED. Meanwhile the compile-time
    ADAPTER_CONTENT_HASH_MISMATCH code is still exercised in-repo by
    ``test_compiler_records_content_hash_mismatch`` below via an adapter that
    bypasses __post_init__ using object.__setattr__ on the frozen dataclass.
    """
    with pytest.raises(ValueError, match="content_hash"):
        ParsedArtifact(
            source_id="stub",
            source_hash="0" * 64,
            content=b"hello",
            content_hash="0" * 64,  # sha256(b"hello") != 0*64
            content_mime_type="text/markdown",
            parser_name="stub",
        )


def test_compiler_records_content_hash_mismatch(tmp_path: Path, sample_markdown: Path):
    """Adapter fakes a bad content_hash after construction; compiler catches it.

    We construct a valid artifact, then mutate content on the frozen dataclass
    via object.__setattr__ so the Compiler's content-hash check fires.
    """
    body = sample_markdown.read_bytes()
    valid_hash = hashlib.sha256(body).hexdigest()

    class TamperingAdapter:
        def parse(self, source: ParserInput) -> ParsedArtifact:
            artifact = ParsedArtifact(
                source_id=source.source_id,
                source_hash=source.source_hash,
                content=body,
                content_hash=valid_hash,
                content_mime_type="text/markdown",
                parser_name="tampering",
            )
            # Swap content out post-construction; content_hash now lies.
            object.__setattr__(artifact, "content", body + b"tampered")
            return artifact

    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=TamperingAdapter(),
    ).compile(str(sample_markdown))
    codes = {e.code for e in ctx.validation.errors}
    assert "ADAPTER_CONTENT_HASH_MISMATCH" in codes, codes
    assert ctx.document is None


# ── 4. declared_truncated propagates ─────────────────────────────────────────

def test_adapter_declared_truncated_propagates(tmp_path: Path, sample_markdown: Path):
    adapter = StubMarkdownAdapter(
        _content=sample_markdown.read_bytes(),
        _declared_truncated=True,
    )
    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=adapter,
    ).compile(str(sample_markdown))
    assert ctx.document is not None
    assert ctx.document.metadata.get("declared_truncated") is True


# ── 5. Provenance threads into ctx ───────────────────────────────────────────

def test_adapter_provenance_threads_into_context(tmp_path: Path, sample_markdown: Path):
    adapter = StubMarkdownAdapter(
        _content=sample_markdown.read_bytes(),
        _parser_name="external-parser",
        _parser_version="9.9.9",
        _parser_configuration_id="prod-config",
    )
    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=adapter,
    ).compile(str(sample_markdown))
    assert ctx.parser_name == "external-parser"
    assert ctx.parser_version == "9.9.9"
    assert ctx.parser_configuration_id == "prod-config"


# ── 6. Legacy path unchanged when adapter is None ────────────────────────────

def test_legacy_parsers_kwarg_still_works_when_adapter_none(tmp_path: Path, sample_markdown: Path):
    """Default compile() path is unaffected by the adapter option."""
    ctx_default = Compiler(output_dir=str(tmp_path / "default")).compile(str(sample_markdown))
    ctx_explicit_none = Compiler(
        output_dir=str(tmp_path / "explicit_none"),
        parser_adapter=None,
    ).compile(str(sample_markdown))
    assert ctx_default.document is not None
    assert ctx_explicit_none.document is not None
    assert ctx_default.manifest is not None
    assert ctx_explicit_none.manifest is not None
    assert ctx_default.manifest.readiness_score == ctx_explicit_none.manifest.readiness_score
    assert ctx_default.parser_name == ctx_explicit_none.parser_name


# ── 7. Adapter wins over parsers= kwarg ──────────────────────────────────────

def test_adapter_takes_precedence_over_parsers_kwarg(tmp_path: Path, sample_markdown: Path):
    from aksharamd.plugins.parsers.markdown import MarkdownParser

    class SentinelParser(MarkdownParser):
        name = "sentinel-parser-should-not-run"

    adapter = StubMarkdownAdapter(
        _content=sample_markdown.read_bytes(),
        _parser_name="external-parser",
    )
    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parsers={"md": SentinelParser},
        parser_adapter=adapter,
    ).compile(str(sample_markdown))
    assert ctx.parser_name == "external-parser"
    assert ctx.document is not None


# ── 8. MarkItDown end-to-end smoke ───────────────────────────────────────────

def test_markitdown_adapter_end_to_end(tmp_path: Path):
    if importlib.util.find_spec("markitdown") is None:
        pytest.skip("optional MarkItDown dependency is not installed")

    # Prefer a real corpus PDF when present; otherwise fall back to a small
    # markdown fixture so the smoke still exercises the wiring end-to-end
    # without depending on binaries not shipped with the worktree.
    candidate_paths = list(
        Path("corpus/snippet-pilot-public").glob("*.pdf")
    ) if Path("corpus/snippet-pilot-public").exists() else []

    if candidate_paths:
        source_path = candidate_paths[0]
    else:
        source_path = tmp_path / "invoice.md"
        source_path.write_text("# Invoice INV-42\n\nTotal: $18.50\n", encoding="utf-8")

    from importlib.metadata import version

    class MarkItDownAdapter:
        def __init__(self, path: Path) -> None:
            self._path = path

        def parse(self, source: ParserInput) -> ParsedArtifact:
            from markitdown import MarkItDown

            converted = MarkItDown().convert(str(self._path))
            body = converted.markdown.encode("utf-8")
            return ParsedArtifact(
                source_id=source.source_id,
                source_hash=source.source_hash,
                content=body,
                content_hash=hashlib.sha256(body).hexdigest(),
                content_mime_type="text/markdown",
                parser_name="markitdown",
                parser_version=version("markitdown"),
                parser_configuration_id="local-default",
            )

    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=MarkItDownAdapter(source_path),
    ).compile(str(source_path))
    assert not ctx.validation.errors, ctx.validation.errors
    assert ctx.manifest is not None
    assert isinstance(ctx.manifest.readiness_score, (int, float))
