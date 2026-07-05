"""Tests for StructureValidator — including the new quality-signal checks."""
from __future__ import annotations

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.plugins.validators.structure import StructureValidator


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_ctx(
    blocks: list[Block],
    file_type: str = "pdf",
    pages: int = 1,
    metadata: dict | None = None,
    original_tokens: int = 0,
) -> CompilationContext:
    doc = Document(
        source="test.pdf",
        file_type=file_type,
        pages=pages,
        blocks=blocks,
        metadata=metadata or {},
    )
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf", output_dir="/tmp/out")
    ctx.document = doc
    ctx.original_tokens = original_tokens
    return ctx


def _para(content: str, page: int = 1) -> Block:
    return Block(type=BlockType.PARAGRAPH, content=content, page=page, index=0)


def _heading(content: str, level: int = 1, page: int = 1) -> Block:
    return Block(type=BlockType.HEADING, content=content, level=level, page=page, index=0)


def _warning_codes(ctx: CompilationContext) -> set[str]:
    return {i.code for i in ctx.validation.warnings}


# ── existing checks still pass ────────────────────────────────────────────────

def test_no_document_emits_error():
    ctx = CompilationContext(source="x.pdf", output_dir="/tmp")
    StructureValidator().execute(ctx)
    assert any(i.code == "NO_DOCUMENT" for i in ctx.validation.issues)


def test_empty_blocks_emits_warning():
    ctx = _make_ctx([])
    StructureValidator().execute(ctx)
    assert "EMPTY_DOCUMENT" in _warning_codes(ctx)


def test_heading_hierarchy_skip_detected():
    blocks = [_heading("Title", 1), _heading("Sub", 4)]
    ctx = _make_ctx(blocks)
    StructureValidator().execute(ctx)
    assert "HEADING_SKIP" in _warning_codes(ctx)


def test_clean_document_has_no_warnings():
    blocks = [
        _heading("Introduction", 1),
        _para("This is a normal paragraph with plenty of text to pass all thresholds."),
    ]
    ctx = _make_ctx(blocks, pages=1, metadata={
        "pdf_classification": "native_text",
        "pdf_stats": {"image_pages": 0, "table_pages": 0, "page_count": 1},
        "pdf_ocr_available": True,
    })
    StructureValidator().execute(ctx)
    codes = _warning_codes(ctx)
    assert "NEAR_EMPTY_OUTPUT" not in codes
    assert "LOW_TEXT_DENSITY" not in codes
    assert "OCR_REQUIRED" not in codes


# ── NEAR_EMPTY_OUTPUT ─────────────────────────────────────────────────────────

def test_near_empty_output_fires_on_sparse_extraction():
    # 5 pages, only 10 chars total → well below 80 chars/page threshold
    blocks = [_para("hi", page=1)]
    ctx = _make_ctx(blocks, pages=5)
    StructureValidator().execute(ctx)
    assert "NEAR_EMPTY_OUTPUT" in _warning_codes(ctx)


def test_near_empty_output_does_not_fire_on_dense_extraction():
    content = "A" * 200  # 200 chars on 1 page → well above threshold
    blocks = [_para(content, page=1)]
    ctx = _make_ctx(blocks, pages=1)
    StructureValidator().execute(ctx)
    assert "NEAR_EMPTY_OUTPUT" not in _warning_codes(ctx)


# ── LOW_TEXT_DENSITY ──────────────────────────────────────────────────────────

def test_low_text_density_fires_when_only_images():
    # Only IMAGE blocks — no paragraph/heading/table chars
    image_block = Block(type=BlockType.IMAGE, content="Image on page 1", page=1, index=0)
    ctx = _make_ctx([image_block], pages=3)
    StructureValidator().execute(ctx)
    assert "LOW_TEXT_DENSITY" in _warning_codes(ctx)


def test_low_text_density_does_not_fire_on_table_heavy_pdf():
    # TABLE blocks count toward text density — a well-extracted table-heavy PDF
    # should not be penalized for having few paragraph blocks.
    table_content = "| Col A | Col B | Col C |\n| --- | --- | --- |\n" + \
                    "| data  | data  | data  |\n" * 10  # ~300 chars of table
    table_block = Block(type=BlockType.TABLE, content=table_content, page=1, index=0)
    ctx = _make_ctx([table_block] * 3, pages=3)
    StructureValidator().execute(ctx)
    assert "LOW_TEXT_DENSITY" not in _warning_codes(ctx)


def test_low_text_density_does_not_fire_on_normal_pdf():
    blocks = [_para("A" * 300, page=i) for i in range(1, 4)]
    ctx = _make_ctx(blocks, pages=3)
    StructureValidator().execute(ctx)
    assert "LOW_TEXT_DENSITY" not in _warning_codes(ctx)


def test_low_text_density_not_checked_for_non_pdf():
    # Same sparse content but file_type is docx — check should not fire
    blocks = [_para("hi", page=1)]
    ctx = _make_ctx(blocks, file_type="docx", pages=5)
    StructureValidator().execute(ctx)
    assert "LOW_TEXT_DENSITY" not in _warning_codes(ctx)


# ── GLYPH_ARTIFACTS ───────────────────────────────────────────────────────────

def test_glyph_artifacts_fires_above_threshold():
    # 20 CID glyphs in 100-char text → ratio = 20% > 2%
    content = "(cid:1) " * 20 + "normal text to pad " * 3
    blocks = [_para(content)]
    ctx = _make_ctx(blocks, pages=1)
    StructureValidator().execute(ctx)
    assert "GLYPH_ARTIFACTS" in _warning_codes(ctx)


def test_glyph_artifacts_does_not_fire_on_clean_text():
    blocks = [_para("Completely normal text with no encoding issues." * 10)]
    ctx = _make_ctx(blocks)
    StructureValidator().execute(ctx)
    assert "GLYPH_ARTIFACTS" not in _warning_codes(ctx)


def test_glyph_artifacts_does_not_fire_on_few_isolated_glyphs():
    # 5 CID glyphs — below _MIN_CID_COUNT threshold of 10
    content = "(cid:1) " * 5 + "normal text " * 50
    blocks = [_para(content)]
    ctx = _make_ctx(blocks)
    StructureValidator().execute(ctx)
    assert "GLYPH_ARTIFACTS" not in _warning_codes(ctx)


# ── REPEATED_CONTENT ──────────────────────────────────────────────────────────

def test_repeated_content_fires_on_boilerplate_flood():
    repeated_line = "CONFIDENTIAL — DO NOT DISTRIBUTE — INTERNAL USE ONLY"
    # 3 distinct lines each repeated 5 times → should fire
    lines_a = "\n".join([repeated_line] * 5)
    lines_b = "\n".join(["FOOTER: Page N of M — All Rights Reserved Corp"] * 5)
    lines_c = "\n".join(["Printed: Monday, January 1, 2024 by System Administrator"] * 5)
    blocks = [_para(lines_a), _para(lines_b), _para(lines_c)]
    ctx = _make_ctx(blocks, pages=3)
    StructureValidator().execute(ctx)
    assert "REPEATED_CONTENT" in _warning_codes(ctx)


def test_repeated_content_does_not_fire_on_unique_content():
    blocks = [_para(f"Paragraph {i} has unique content about topic {i}." * 3) for i in range(5)]
    ctx = _make_ctx(blocks, pages=5)
    StructureValidator().execute(ctx)
    assert "REPEATED_CONTENT" not in _warning_codes(ctx)


# ── TOKEN_BLOAT ───────────────────────────────────────────────────────────────

def test_token_bloat_fires_on_high_token_density():
    blocks = [_para("word " * 100, page=i) for i in range(1, 5)]
    # Simulate 9000 original tokens on 4 pages → 2250/page > 1500 threshold
    ctx = _make_ctx(blocks, pages=4, original_tokens=9_000)
    StructureValidator().execute(ctx)
    assert "TOKEN_BLOAT" in _warning_codes(ctx)


def test_token_bloat_does_not_fire_on_reasonable_density():
    blocks = [_para("word " * 100, page=i) for i in range(1, 5)]
    ctx = _make_ctx(blocks, pages=4, original_tokens=4_000)  # 1000/page — fine
    StructureValidator().execute(ctx)
    assert "TOKEN_BLOAT" not in _warning_codes(ctx)


def test_token_bloat_skipped_for_short_documents():
    # 2-page doc with high tokens/page — below _TOKEN_BLOAT_MIN_PAGES threshold
    blocks = [_para("word " * 100, page=i) for i in range(1, 3)]
    ctx = _make_ctx(blocks, pages=2, original_tokens=10_000)
    StructureValidator().execute(ctx)
    assert "TOKEN_BLOAT" not in _warning_codes(ctx)


def test_token_bloat_not_checked_for_non_pdf():
    blocks = [_para("word " * 100, page=i) for i in range(1, 5)]
    ctx = _make_ctx(blocks, file_type="docx", pages=4, original_tokens=15_000)
    StructureValidator().execute(ctx)
    assert "TOKEN_BLOAT" not in _warning_codes(ctx)


# ── OCR_REQUIRED ─────────────────────────────────────────────────────────────

def test_ocr_required_fires_for_scanned_without_ocr():
    blocks = [_para("[Image not extracted — OCR unavailable.", page=1)]
    ctx = _make_ctx(blocks, pages=3, metadata={
        "pdf_classification": "scanned",
        "pdf_stats": {"image_pages": 3, "text_pages": 0, "page_count": 3},
        "pdf_ocr_available": False,
    })
    StructureValidator().execute(ctx)
    assert "OCR_REQUIRED" in _warning_codes(ctx)


def test_ocr_required_fires_for_hybrid_without_ocr():
    blocks = [_para("Some native text on page 1.", page=1)]
    ctx = _make_ctx(blocks, pages=4, metadata={
        "pdf_classification": "hybrid",
        "pdf_stats": {"image_pages": 2, "text_pages": 2, "page_count": 4},
        "pdf_ocr_available": False,
    })
    StructureValidator().execute(ctx)
    assert "OCR_REQUIRED" in _warning_codes(ctx)


def test_ocr_required_does_not_fire_when_ocr_available():
    blocks = [_para("Text extracted via OCR.", page=1)]
    ctx = _make_ctx(blocks, pages=2, metadata={
        "pdf_classification": "scanned",
        "pdf_stats": {"image_pages": 2, "text_pages": 0, "page_count": 2},
        "pdf_ocr_available": True,
    })
    StructureValidator().execute(ctx)
    assert "OCR_REQUIRED" not in _warning_codes(ctx)


def test_ocr_required_does_not_fire_for_native_text():
    blocks = [_para("Native PDF text is fine.", page=1)]
    ctx = _make_ctx(blocks, pages=1, metadata={
        "pdf_classification": "native_text",
        "pdf_stats": {"image_pages": 0, "text_pages": 1, "page_count": 1},
        "pdf_ocr_available": False,  # OCR absent, but not needed
    })
    StructureValidator().execute(ctx)
    assert "OCR_REQUIRED" not in _warning_codes(ctx)
