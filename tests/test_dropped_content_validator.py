"""Tests for DroppedContentValidator — W_DROPPED_CONTENT (P3).

Verifies the crown-jewel cross-reference detector against synthesised
PDFs generated in-process via PyMuPDF. No committed PDF fixtures — the
detector's behavior is exercised end-to-end by building small PDFs on
the fly and constructing corresponding Document objects.
"""
from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.plugins.validators.dropped_content import (
    DroppedContentValidator,
    _extract_pdf_word_count,
    _markdown_word_count,
)

# ── PDF synthesis helpers ──────────────────────────────────────────────────

_DEFAULT_PAGE_WORDS = 120  # well above the 100-word scale guard


def _make_synthetic_pdf(
    pages: int,
    words_per_page: int = _DEFAULT_PAGE_WORDS,
) -> bytes:
    """Create a small in-memory PDF with `words_per_page` on each page.

    Uses insert_textbox so text wraps within the page bounds rather than
    being clipped off the right edge (insert_text does not wrap).
    """
    doc = fitz.open()
    for page_idx in range(pages):
        page = doc.new_page()
        rect = fitz.Rect(72, 72, 540, 750)  # Letter-size with 1" margins
        # Prefix each word with the page index to keep them unique across pages.
        words = [f"w{page_idx}_{n:03d}" for n in range(words_per_page)]
        page.insert_textbox(rect, " ".join(words), fontsize=9)
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


def _make_empty_pdf(pages: int = 1) -> bytes:
    """Create a PDF with no text layer (empty pages)."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


def _make_ctx(
    pdf_bytes: bytes | None,
    markdown_words: list[str],
    file_type: str = "pdf",
    adapter_provided: bool = False,
) -> CompilationContext:
    """Build a CompilationContext with the given pdf bytes and one PARAGRAPH block."""
    body = " ".join(markdown_words)
    blocks = [
        Block(
            type=BlockType.PARAGRAPH,
            content=body,
            page=1,
            index=0,
            confidence=ExtractionConfidence.EXTRACTED,
        )
    ]
    doc = Document(
        source="test.pdf",
        file_type=file_type,
        pages=1,
        blocks=blocks,
        metadata={},
    )
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc
    ctx.raw_source_bytes = pdf_bytes
    ctx.parser_provided_via_adapter = adapter_provided
    return ctx


# ── Helper isolation ───────────────────────────────────────────────────────

def test_extract_pdf_word_count_on_synthetic_pdf():
    pdf = _make_synthetic_pdf(pages=2, words_per_page=100)
    total, page_count, has_text_layer = _extract_pdf_word_count(pdf)
    assert page_count == 2
    assert has_text_layer is True
    # 100 words/page x 2 pages = 200 target; allow slack for insert_textbox
    # not fitting every word if font metrics differ across PyMuPDF versions.
    assert total >= 150, f"expected >=150 words extracted, got {total}"


def test_extract_pdf_word_count_on_empty_bytes():
    total, page_count, has_text_layer = _extract_pdf_word_count(b"")
    assert total == 0
    assert page_count == 0
    assert has_text_layer is False


def test_extract_pdf_word_count_on_garbage_bytes():
    """Malformed PDF must not raise; returns (0, 0, False)."""
    total, page_count, has_text_layer = _extract_pdf_word_count(b"not a real pdf")
    assert total == 0
    assert has_text_layer is False


def test_markdown_word_count_counts_only_text_blocks():
    blocks = [
        Block(type=BlockType.PARAGRAPH, content="one two three", page=1, index=0),
        Block(type=BlockType.HEADING, content="Heading here", page=1, index=1),
        Block(type=BlockType.LIST, content="item one item two", page=1, index=2),
    ]
    assert _markdown_word_count(blocks) == 3 + 2 + 4


# ── Happy path: full-fidelity extraction should not fire ───────────────────

def test_full_fidelity_extraction_does_not_fire():
    """Markdown word count matches PDF word count — no signal."""
    pdf = _make_synthetic_pdf(pages=2, words_per_page=100)
    total, _, _ = _extract_pdf_word_count(pdf)
    # Match the extracted count exactly so ratio == 1.0.
    ctx = _make_ctx(pdf, [f"m{n}" for n in range(total)])
    DroppedContentValidator().execute(ctx)
    diag = ctx.document.metadata["dropped_content_diagnostics"]
    assert diag["warned"] is False
    assert diag["ratio"] >= 0.5


# ── Firing paths ───────────────────────────────────────────────────────────

def test_dropped_pages_fires_the_detector():
    """A PDF with 3 pages of text but only a tiny markdown fires the detector."""
    pdf = _make_synthetic_pdf(pages=3, words_per_page=100)
    total, _, _ = _extract_pdf_word_count(pdf)
    # Markdown has only 10% of the PDF's word count.
    tiny_word_count = max(1, total // 10)
    ctx = _make_ctx(pdf, [f"m{n}" for n in range(tiny_word_count)])
    DroppedContentValidator().execute(ctx)
    diag = ctx.document.metadata["dropped_content_diagnostics"]
    assert diag["warned"] is True
    assert diag["ratio"] < 0.5
    assert diag["missing_words"] > 0


def test_warning_message_carries_lower_bound_framing():
    pdf = _make_synthetic_pdf(pages=3, words_per_page=100)
    ctx = _make_ctx(pdf, [f"m{n}" for n in range(10)])
    DroppedContentValidator().execute(ctx)
    warnings = [i for i in ctx.validation.issues if i.code == "W_DROPPED_CONTENT"]
    assert len(warnings) == 1
    assert "LOWER BOUND" in warnings[0].message


# ── Skip guards ────────────────────────────────────────────────────────────

def test_non_pdf_file_type_is_skipped_silently():
    ctx = _make_ctx(pdf_bytes=b"", markdown_words=["only"], file_type="md")
    DroppedContentValidator().execute(ctx)
    assert "dropped_content_diagnostics" not in ctx.document.metadata


def test_adapter_provided_document_is_skipped():
    """Even with file_type='pdf' (defensive), adapter-provided docs skip."""
    pdf = _make_synthetic_pdf(pages=3)
    ctx = _make_ctx(pdf, [f"w{n}" for n in range(10)], adapter_provided=True)
    DroppedContentValidator().execute(ctx)
    assert "dropped_content_diagnostics" not in ctx.document.metadata


def test_missing_raw_bytes_suppresses_signal():
    """ctx.raw_bytes() returning None (URL/S3 case) suppresses the detector."""
    ctx = _make_ctx(pdf_bytes=None, markdown_words=["one", "two"])
    ctx.source = ""  # ensure lazy fallback in raw_bytes() also returns None
    DroppedContentValidator().execute(ctx)
    diag = ctx.document.metadata["dropped_content_diagnostics"]
    assert diag["warned"] is False
    assert "ctx.raw_bytes() returned None" in diag.get("suppressed_reason", "")


def test_scanned_pdf_is_skipped():
    """A PDF with no text layer (0 words extracted) is skipped."""
    pdf_bytes = _make_empty_pdf(pages=1)
    ctx = _make_ctx(pdf_bytes, [f"word{n}" for n in range(100)])
    DroppedContentValidator().execute(ctx)
    diag = ctx.document.metadata["dropped_content_diagnostics"]
    assert diag["warned"] is False
    assert "no text layer" in diag.get("suppressed_reason", "")


def test_small_pdf_is_skipped_by_scale_guard():
    """A PDF with fewer than 100 words does not run the ratio check."""
    small_pdf = _make_synthetic_pdf(pages=1, words_per_page=20)
    ctx = _make_ctx(small_pdf, [])
    DroppedContentValidator().execute(ctx)
    diag = ctx.document.metadata["dropped_content_diagnostics"]
    assert diag["warned"] is False
    assert "scale guard" in diag.get("suppressed_reason", "")


# ── Diagnostic completeness ────────────────────────────────────────────────

def test_diagnostics_always_populated_for_eligible_pdfs():
    pdf = _make_synthetic_pdf(pages=2, words_per_page=100)
    total, _, _ = _extract_pdf_word_count(pdf)
    ctx = _make_ctx(pdf, [f"m{n}" for n in range(total)])
    DroppedContentValidator().execute(ctx)
    diag = ctx.document.metadata["dropped_content_diagnostics"]
    assert "pdf_word_count" in diag
    assert "markdown_word_count" in diag
    assert "ratio" in diag
    assert "has_text_layer" in diag
    assert "pdf_page_count" in diag
    assert diag["warning_maturity"] == "experimental"
