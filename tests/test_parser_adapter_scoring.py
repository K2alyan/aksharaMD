"""Scoring regressions for the ``Compiler(parser_adapter=...)`` path.

These tests exercise the invariant added by the parser-adapter scoring fix:
when the compiler converts a non-markdown source via an external adapter
that returns markdown bytes, the resulting Document is fundamentally
markdown. PDF-geometry detectors — most visibly ``MISSING_PAGE`` and
``LOW_TEXT_DENSITY`` — must not fire against that Document.

The fix keeps ``ctx.document.file_type == "md"`` on the adapter path (only
the manifest's user-facing ``file_type`` reports the source-detected
extension). Detectors gated on ``doc.file_type == "pdf"`` skip cleanly.

Failure modes guarded here:
  * ``MISSING_PAGE`` fires when a markdown-derived Document (0 pages) is
    forced to look like a PDF.
  * Downstream ``MISSING_PAGE`` warning becomes a scoring deduction that
    caps the readiness score at 69.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler
from aksharamd.parser_contract import ParsedArtifact, ParserInput

# ── Shared adapter stub ──────────────────────────────────────────────────────

@dataclass
class StubMarkdownAdapter:
    """Adapter that returns a caller-supplied markdown body verbatim."""

    _content: bytes = b"# Adapter Doc\n\nHello from the adapter.\n"
    _parser_name: str = "stub-adapter"
    _parser_version: str | None = "0.0.1"
    _parser_configuration_id: str | None = "default"

    def parse(self, source: ParserInput) -> ParsedArtifact:
        return ParsedArtifact(
            source_id=source.source_id,
            source_hash=source.source_hash,
            content=self._content,
            content_hash=hashlib.sha256(self._content).hexdigest(),
            content_mime_type="text/markdown",
            parser_name=self._parser_name,
            parser_version=self._parser_version,
            parser_configuration_id=self._parser_configuration_id,
            declared_truncated=False,
        )


def _make_minimal_pdf(tmp_path: Path, name: str = "sample.pdf") -> Path:
    """Build a real one-page PDF the compiler can hash and route via detection.

    The adapter never actually parses the PDF (the stub returns pre-baked
    markdown regardless), but the source path must exist and be readable so
    the compiler's file-size gate and adapter input builder can proceed.
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "Original PDF text.")
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


def _codes(ctx) -> set[str]:
    return {i.code for i in ctx.validation.issues}


def _deduction_ids(ctx) -> set[str]:
    manifest = ctx.manifest
    assert manifest is not None
    return {
        d.get("rule_id")
        for d in manifest.deductions
        if not d.get("suppressed", False)
    }


# ── 1. Clean markdown via adapter must not fire MISSING_PAGE ────────────────

def test_adapter_pdf_source_clean_markdown_no_missing_page(tmp_path: Path):
    """PDF source + adapter returning clean markdown must not fire MISSING_PAGE.

    Regression coverage for the QASPER 1503.00841 case: before the fix, the
    compiler stamped ``doc.file_type = "pdf"`` on the markdown-derived
    Document (which has 0 pages), so the structure validator emitted a
    ``MISSING_PAGE`` warning that the scorer turned into a -12 deduction and
    capped the readiness score at 69.
    """
    pdf = _make_minimal_pdf(tmp_path)
    body = (
        "# QASPER Sample\n\n"
        "First paragraph with enough content to survive the optimizer merge threshold.\n\n"
        "Second paragraph with additional prose describing the study methodology.\n\n"
        "Third paragraph closing the narrative.\n"
    )
    adapter = StubMarkdownAdapter(_content=body.encode("utf-8"))

    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=adapter,
    ).compile(str(pdf))

    assert ctx.document is not None
    assert not ctx.validation.errors, ctx.validation.errors
    # The flag must be set by the adapter branch.
    assert ctx.parser_provided_via_adapter is True
    # Document file_type stays as markdown so PDF-geometry detectors skip.
    assert ctx.document.file_type == "md"
    # But the manifest still reports the source-detected type for users.
    assert ctx.manifest is not None
    assert ctx.manifest.file_type == "pdf"
    # The bug's smoking gun: MISSING_PAGE must NOT be emitted by the
    # structure validator, and it must NOT show up as a scoring deduction.
    assert "MISSING_PAGE" not in _codes(ctx)
    assert "MISSING_PAGE" not in _deduction_ids(ctx)
    # Also no LOW_TEXT_DENSITY or NEAR_EMPTY_OUTPUT — same PDF-only gates.
    assert "LOW_TEXT_DENSITY" not in _codes(ctx)


# ── 2. Markdown-appropriate detectors still fire under adapter path ─────────

def test_adapter_pdf_source_heading_skip_still_fires(tmp_path: Path):
    """Markdown-appropriate detectors (HEADING_SKIP) still fire under adapter.

    The fix must not disable ALL scoring — only PDF-geometry detectors. A
    markdown body with a heading-level jump is exactly the kind of quality
    issue the readiness score should still catch.
    """
    pdf = _make_minimal_pdf(tmp_path, name="qasper.pdf")
    # ##### (level 5) after # (level 1) is a big skip.
    body = (
        "# Top Level\n\n"
        "Some intro prose to survive the optimizer merge threshold.\n\n"
        "##### Deeply Nested Section\n\n"
        "Body of the deeply nested section with enough content to persist.\n"
    )
    adapter = StubMarkdownAdapter(_content=body.encode("utf-8"))

    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=adapter,
    ).compile(str(pdf))

    assert ctx.document is not None
    assert not ctx.validation.errors, ctx.validation.errors
    # HEADING_SKIP is a markdown-appropriate observation and MUST still fire.
    assert "HEADING_SKIP" in _codes(ctx)


# ── 3. Native PDF path still fires MISSING_PAGE (regression guard) ──────────

def test_native_pdf_path_still_fires_missing_page(tmp_path: Path, monkeypatch):
    """Native PDF path (no adapter) must still flag missing pages.

    We fabricate a Document whose declared page count exceeds what the
    parser filled in, and confirm the structure validator emits
    MISSING_PAGE. This proves the gating checks parser_provided_via_adapter,
    not something that would silently kill MISSING_PAGE for every caller.
    """
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import Block, BlockType
    from aksharamd.models.document import Document
    from aksharamd.plugins.validators.structure import StructureValidator

    source_path = str(tmp_path / "fake.pdf")
    ctx = CompilationContext(source=source_path)
    # 3 declared pages but only page 1 has any content — pages 2 and 3
    # must be flagged as missing.
    ctx.document = Document(
        source=source_path,
        file_type="pdf",
        pages=3,
        blocks=[
            Block(
                id="b1",
                type=BlockType.PARAGRAPH,
                content="Page 1 has body text of nontrivial length to exercise the flow.",
                page=1,
            ),
        ],
    )
    # Native path does not set the adapter flag.
    assert ctx.parser_provided_via_adapter is False

    ctx = StructureValidator().execute(ctx)
    codes = {i.code for i in ctx.validation.issues}
    assert "MISSING_PAGE" in codes, codes


# ── 4. Structure validator gates PDF-only checks on the adapter flag ────────

def test_structure_validator_skips_pdf_only_on_adapter_flag(tmp_path: Path):
    """Structure validator's PDF-only checks skip when adapter flag is set.

    Even if a caller manually forces ``doc.file_type = 'pdf'`` on an
    adapter-provided Document, the ``parser_provided_via_adapter`` flag
    must belt-and-suspenders keep PDF-geometry detectors quiet.
    """
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import Block, BlockType
    from aksharamd.models.document import Document
    from aksharamd.plugins.validators.structure import StructureValidator

    source_path = str(tmp_path / "fake.pdf")
    ctx = CompilationContext(source=source_path)
    ctx.parser_provided_via_adapter = True
    # Even with file_type=="pdf" and 3 declared pages but only page 1
    # populated, MISSING_PAGE must NOT fire because the document is
    # adapter-provided markdown that happened to be labelled pdf.
    ctx.document = Document(
        source=source_path,
        file_type="pdf",
        pages=3,
        blocks=[
            Block(
                id="b1",
                type=BlockType.PARAGRAPH,
                content="Adapter body paragraph of sufficient length for the flow.",
                page=1,
            ),
        ],
    )

    ctx = StructureValidator().execute(ctx)
    codes = {i.code for i in ctx.validation.issues}
    assert "MISSING_PAGE" not in codes, codes
    assert "LOW_TEXT_DENSITY" not in codes, codes


# ── 5. Native markdown source still scores as before (identity check) ───────

def test_native_markdown_unaffected_by_flag(tmp_path: Path):
    """A native markdown compile leaves ``parser_provided_via_adapter`` False."""
    md_path = tmp_path / "sample.md"
    md_path.write_text(
        "# Sample\n\n"
        "First paragraph with enough content to survive the optimizer merge threshold.\n\n"
        "Second paragraph with another sentence that is also long enough to persist.\n",
        encoding="utf-8",
    )

    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(md_path))

    assert ctx.document is not None
    assert ctx.parser_provided_via_adapter is False
    assert ctx.document.file_type == "md"
    assert "MISSING_PAGE" not in _codes(ctx)
