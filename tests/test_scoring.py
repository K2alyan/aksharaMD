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


# ── Penalty-suppression calibration tests ──────────────────────────────────────

def test_ocr_required_suppresses_near_empty_penalty():
    """When OCR_REQUIRED fires, NEAR_EMPTY_OUTPUT penalty should be suppressed
    to avoid triple-counting the same missing-content problem."""
    all_three = [
        ValidationIssue(severity=Severity.WARNING, code="NEAR_EMPTY_OUTPUT", message="sparse"),
        ValidationIssue(severity=Severity.WARNING, code="LOW_TEXT_DENSITY", message="low"),
        ValidationIssue(severity=Severity.WARNING, code="OCR_REQUIRED", message="no ocr"),
    ]
    just_ocr = [
        ValidationIssue(severity=Severity.WARNING, code="OCR_REQUIRED", message="no ocr"),
    ]
    blocks = [Block(type=BlockType.PARAGRAPH, content="minimal text", index=0)]
    doc_meta = {
        "pdf_classification": "scanned",
        "pdf_stats": {"image_pages": 3, "page_count": 3},
    }
    from aksharamd.models.document import Document
    doc_all = Document(source="x.pdf", file_type="pdf", pages=3, blocks=blocks,
                       metadata=doc_meta)
    ctx_all = CompilationContext(source="x.pdf",
                                 document=doc_all,
                                 validation=ValidationReport(issues=all_three),
                                 original_tokens=100)
    ctx_just = CompilationContext(source="x.pdf",
                                  document=doc_all,
                                  validation=ValidationReport(issues=just_ocr),
                                  original_tokens=100)
    score_all = compute_confidence(ctx_all).score
    score_just = compute_confidence(ctx_just).score
    # With suppression: all-three should equal just-ocr (same penalty applied)
    assert score_all == score_just, (
        f"Penalty stacking: all-three scored {score_all}, just-OCR scored {score_just}; "
        "NEAR_EMPTY and LOW_TEXT should be suppressed when OCR_REQUIRED fires"
    )


def test_glyph_artifacts_penalty_drops_into_risky_band():
    """CID-garbled text is unusable by LLMs — score should drop below 70."""
    issues = [
        ValidationIssue(severity=Severity.WARNING, code="GLYPH_ARTIFACTS", message="cid")
    ]
    result = compute_confidence(_ctx("pdf", issues=issues))
    assert result.score < 70, (
        f"GLYPH_ARTIFACTS should push score below 70 (got {result.score}); "
        "CID-garbled text is not usable by LLMs"
    )


def test_near_empty_without_ocr_required_still_deducts():
    """NEAR_EMPTY_OUTPUT penalty should fire normally when OCR_REQUIRED is absent
    (e.g., encrypted or corrupt PDF, not a scanned one)."""
    issues = [
        ValidationIssue(severity=Severity.WARNING, code="NEAR_EMPTY_OUTPUT", message="sparse"),
    ]
    result = compute_confidence(_ctx("pdf", issues=issues))
    assert result.score < 87


def test_ocr_attempted_sparse_never_scores_worse_than_ocr_required():
    """When OCR ran on a scanned PDF but produced near-empty output, the score
    must be >= what OCR_REQUIRED alone would give — attempting OCR should never
    make things worse than not having it installed."""
    from aksharamd.models.document import Document

    doc_meta = {
        "pdf_classification": "scanned",
        "pdf_ocr_available": True,
        "pdf_stats": {"image_pages": 4, "page_count": 4},
    }
    blocks = [Block(type=BlockType.PARAGRAPH, content="minimal", index=0)]

    # Simulate: OCR ran but result is near-empty + low-density
    doc = Document(source="x.pdf", file_type="pdf", pages=4, blocks=blocks,
                   metadata=doc_meta)
    issues_ocr_sparse = [
        ValidationIssue(severity=Severity.WARNING, code="NEAR_EMPTY_OUTPUT", message="sparse"),
        ValidationIssue(severity=Severity.WARNING, code="LOW_TEXT_DENSITY", message="low"),
    ]
    ctx_sparse = CompilationContext(source="x.pdf", document=doc,
                                    validation=ValidationReport(issues=issues_ocr_sparse),
                                    original_tokens=100)

    # Baseline: same PDF but OCR not installed (OCR_REQUIRED)
    doc_no_ocr_meta = {**doc_meta, "pdf_ocr_available": False}
    doc_no_ocr = Document(source="x.pdf", file_type="pdf", pages=4, blocks=blocks,
                          metadata=doc_no_ocr_meta)
    issues_no_ocr = [
        ValidationIssue(severity=Severity.WARNING, code="OCR_REQUIRED", message="no ocr"),
    ]
    ctx_no_ocr = CompilationContext(source="x.pdf", document=doc_no_ocr,
                                    validation=ValidationReport(issues=issues_no_ocr),
                                    original_tokens=100)

    score_sparse = compute_confidence(ctx_sparse).score
    score_no_ocr = compute_confidence(ctx_no_ocr).score

    assert score_sparse >= score_no_ocr, (
        f"OCR-attempted-sparse ({score_sparse}) scored worse than OCR_REQUIRED ({score_no_ocr}); "
        "attempting OCR should never penalise more than skipping it"
    )
    # Both should be in the same RISKY band, not POOR
    assert score_sparse >= 40, f"OCR sparse score ({score_sparse}) is unexpectedly low"
