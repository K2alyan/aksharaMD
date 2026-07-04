"""Tests for aksharamd.scoring.readiness — confidence scoring."""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.models.manifest import Manifest
from aksharamd.models.validation import Severity, ValidationIssue, ValidationReport
from aksharamd.scoring.readiness import compute_confidence, compute_readiness_score

_DEFAULT_BLOCK = [Block(type=BlockType.PARAGRAPH, content="Hello world text.", index=0)]


def _ctx(file_type: str = "md", blocks: list | None = None, pages: int = 0,
         issues: list | None = None, original_tokens: int = 100,
         manifest: Manifest | None = None, no_blocks: bool = False) -> CompilationContext:
    doc = Document(
        source=f"test.{file_type}",
        file_type=file_type,
        blocks=[] if no_blocks else (blocks if blocks is not None else _DEFAULT_BLOCK),
        pages=pages,
    )
    report = ValidationReport(issues=issues or [])
    return CompilationContext(
        source=f"test.{file_type}",
        document=doc,
        validation=report,
        original_tokens=original_tokens,
        manifest=manifest,
    )


def test_no_document_returns_zero():
    ctx = CompilationContext(source="x.md")
    result = compute_confidence(ctx)
    assert result.score == 0
    assert "No content" in result.notes[0]


def test_clean_markdown_scores_high():
    result = compute_confidence(_ctx("md"))
    assert result.score >= 80


def test_rtf_format_note_included():
    result = compute_confidence(_ctx("rtf"))
    assert any("RTF" in n for n in result.notes)


def test_legacy_doc_format_note():
    result = compute_confidence(_ctx("doc"))
    assert any("LibreOffice" in n for n in result.notes)


def test_mp4_format_note():
    result = compute_confidence(_ctx("mp4"))
    assert any("Video" in n for n in result.notes)


def test_archive_format_note():
    result = compute_confidence(_ctx("zip"))
    assert any("Archive" in n for n in result.notes)


def test_image_with_no_text_deducts_score():
    result = compute_confidence(_ctx("png", no_blocks=True))
    assert result.score < 70
    assert any("No text detected" in n for n in result.notes)


def test_image_with_ocr_text_note():
    result = compute_confidence(_ctx("png"))
    assert any("OCR" in n for n in result.notes)


def test_parse_errors_deduct_score():
    issues = [
        ValidationIssue(severity=Severity.ERROR, code="PARSE_ERROR", message="fail")
    ]
    result = compute_confidence(_ctx("pdf", issues=issues))
    assert result.score < 87


def test_token_savings_note_shown():
    manifest = Manifest(source="test.md", file_type="md", optimized_tokens=400)
    result = compute_confidence(_ctx("md", original_tokens=1000, manifest=manifest))
    assert any("redundant tokens" in n for n in result.notes)


def test_compute_readiness_score_returns_int():
    score = compute_readiness_score(_ctx("md"))
    assert isinstance(score, int)
    assert 0 <= score <= 100


def test_image_blocks_add_note():
    blocks = [
        Block(type=BlockType.PARAGRAPH, content="Some text here", index=0),
        Block(type=BlockType.IMAGE, content="diagram.png", index=1),
    ]
    result = compute_confidence(_ctx("pdf", blocks=blocks))
    assert any("image" in n.lower() for n in result.notes)


def test_no_headings_multipage_deducts():
    result = compute_confidence(_ctx("pdf", pages=5))
    assert any("heading" in n.lower() for n in result.notes)


def test_missing_pages_warning_deducts():
    issues = [
        ValidationIssue(severity=Severity.WARNING, code="MISSING_PAGE", message="no text")
        for _ in range(6)
    ]
    result = compute_confidence(_ctx("pdf", pages=10, issues=issues))
    assert result.score < 87
    assert any("page" in n.lower() for n in result.notes)


def test_large_block_warning_deducts():
    issues = [
        ValidationIssue(severity=Severity.WARNING, code="LARGE_BLOCK", message="big block")
    ]
    result = compute_confidence(_ctx("pdf", issues=issues))
    assert result.score < 87
