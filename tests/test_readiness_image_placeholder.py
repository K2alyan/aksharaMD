"""Regression tests for image-placeholder-only output detection in readiness scoring.

Confirmed false-safes from ParseBench calibration (2026-07-12):
  text_simple__letter3     - scanned Home Office letter, rs=87, human=FAIL
  text_simple__myctophidae - scanned species reference page, rs=87, human=FAIL
  text_dense__japanese     - vertical Japanese text, rs=85, human=FAIL

All three produced only image placeholders because OCR was unavailable.
However, full-page raster bytes ARE captured in doc.assets (via ocr_pixmap).
compile_to_multimodal() delivers those bytes to vision-capable models.

W_IMAGE_ONLY_REQUIRES_VISION (bytes present):
  - Score is NOT capped at 55 — text-only is poor but multimodal is usable.
  - A note is added flagging that vision is required for text content.

W_IMAGE_ONLY_NO_USABLE_FALLBACK (bytes absent — rasterization failed):
  - Score IS capped at 55 (RISKY) — neither text nor image is usable.
"""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.asset import Asset
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.models.manifest import _quality_band
from aksharamd.scoring.readiness import compute_confidence

_OCR_UNAVAILABLE_MSG = (
    "[Image not extracted — OCR unavailable. "
    "Install pytesseract and Tesseract to extract text from images: "
    "pip install aksharamd[ocr]"
)

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal non-empty PNG header


def _make_ctx_placeholder_only(pages: int = 1, include_image_bytes: bool = False) -> CompilationContext:
    """Context whose entire output is OCR-unavailable placeholders + IMAGE blocks.

    include_image_bytes=True simulates the real pipeline where ocr_pixmap is
    captured in doc.assets — triggering W_IMAGE_ONLY_REQUIRES_VISION.
    include_image_bytes=False simulates rasterization failure — triggering
    W_IMAGE_ONLY_NO_USABLE_FALLBACK.
    """
    blocks = []
    assets = []
    for p in range(1, pages + 1):
        asset_id = f"aabbccdd{p:04d}"
        blocks.append(Block(
            type=BlockType.PARAGRAPH,
            content=_OCR_UNAVAILABLE_MSG,
            page=p, index=0,
            confidence=ExtractionConfidence.AMBIGUOUS,
        ))
        blocks.append(Block(
            type=BlockType.IMAGE,
            content=f"![Image on page {p}](asset://{asset_id})",
            page=p, index=1,
            metadata={"asset_id": asset_id},
        ))
        if include_image_bytes:
            assets.append(Asset(
                id=asset_id,
                type="image",
                page=p,
                image_bytes=_FAKE_PNG,
            ))

    doc = Document(
        source="test_scanned.pdf",
        file_type="pdf",
        pages=pages,
        blocks=blocks,
        assets=assets,
        metadata={
            "pdf_classification": "native_text",  # misclassified — the bug we're testing
            "pdf_stats": {"image_pages": pages, "table_pages": 0},
            "pdf_ocr_available": False,
        },
    )
    ctx = CompilationContext(source="test_scanned.pdf")
    ctx.document = doc
    ctx.original_tokens = 50 * pages
    return ctx


def _make_ctx_with_real_text(pages: int = 1) -> CompilationContext:
    """Context with placeholder AND real text blocks (partial image pages)."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, content=_OCR_UNAVAILABLE_MSG, page=1, index=0,
              confidence=ExtractionConfidence.AMBIGUOUS),
        Block(type=BlockType.IMAGE, content="![Image on page 1](asset://aabb0001)", page=1, index=1),
        Block(type=BlockType.PARAGRAPH, content="Some real text on the second page.", page=2, index=0),
    ]
    doc = Document(
        source="test_hybrid.pdf",
        file_type="pdf",
        pages=pages,
        blocks=blocks,
        metadata={
            "pdf_classification": "hybrid",
            "pdf_stats": {"image_pages": 1, "table_pages": 0},
            "pdf_ocr_available": False,
        },
    )
    ctx = CompilationContext(source="test_hybrid.pdf")
    ctx.document = doc
    ctx.original_tokens = 30
    return ctx


# ── W_IMAGE_ONLY_NO_USABLE_FALLBACK: no image bytes → cap at 55 ──────────────

def test_placeholder_no_bytes_scores_risky_single_page():
    ctx = _make_ctx_placeholder_only(pages=1, include_image_bytes=False)
    result = compute_confidence(ctx)
    assert result.score <= 55, (
        f"All-placeholder output with no image bytes scored {result.score} — must be ≤ 55 (RISKY). "
        "W_IMAGE_ONLY_NO_USABLE_FALLBACK should cap score when rasterization fails."
    )
    assert _quality_band(result.score) in ("RISKY", "POOR"), (
        f"Expected RISKY or POOR band, got {_quality_band(result.score)} at score {result.score}"
    )


def test_placeholder_no_bytes_scores_risky_multi_page():
    ctx = _make_ctx_placeholder_only(pages=3, include_image_bytes=False)
    result = compute_confidence(ctx)
    assert result.score <= 55, (
        f"Multi-page no-bytes output scored {result.score} — must be ≤ 55."
    )


def test_placeholder_no_bytes_note_present():
    ctx = _make_ctx_placeholder_only(pages=1, include_image_bytes=False)
    result = compute_confidence(ctx)
    note_texts = " ".join(result.notes)
    assert "W_IMAGE_ONLY_NO_USABLE_FALLBACK" in note_texts, (
        "Expected W_IMAGE_ONLY_NO_USABLE_FALLBACK tag in notes."
    )


# ── W_IMAGE_ONLY_REQUIRES_VISION: image bytes present → no overall cap ────────

def test_placeholder_with_bytes_not_capped_at_55():
    ctx = _make_ctx_placeholder_only(pages=1, include_image_bytes=True)
    result = compute_confidence(ctx)
    assert result.score > 55, (
        f"Placeholder-with-bytes scored {result.score} — score should NOT be capped at 55 "
        "when valid image assets exist (W_IMAGE_ONLY_REQUIRES_VISION)."
    )


def test_placeholder_with_bytes_vision_note_present():
    ctx = _make_ctx_placeholder_only(pages=1, include_image_bytes=True)
    result = compute_confidence(ctx)
    note_texts = " ".join(result.notes)
    assert "W_IMAGE_ONLY_REQUIRES_VISION" in note_texts, (
        "Expected W_IMAGE_ONLY_REQUIRES_VISION tag in notes when image bytes are captured."
    )


def test_placeholder_with_bytes_multi_page():
    ctx = _make_ctx_placeholder_only(pages=3, include_image_bytes=True)
    result = compute_confidence(ctx)
    assert result.score > 55, (
        f"Multi-page placeholder-with-bytes scored {result.score} — must be > 55."
    )


# ── non-regression: partial image does NOT get the cap ───────────────────────

def test_partial_image_with_real_text_not_capped():
    ctx = _make_ctx_with_real_text(pages=2)
    result = compute_confidence(ctx)
    assert result.score > 30, (
        f"Partial-image document scored unexpectedly low ({result.score}). "
        "Real text blocks should prevent the all-placeholder detection."
    )


# ── non-regression: native text PDF is not penalized ─────────────────────────

def test_native_text_pdf_not_affected():
    blocks = [
        Block(type=BlockType.PARAGRAPH, content="This is normal extracted text.", page=1, index=0),
        Block(type=BlockType.HEADING, content="Introduction", page=1, index=1, level=2),
    ]
    doc = Document(
        source="test_native.pdf",
        file_type="pdf",
        pages=1,
        blocks=blocks,
        metadata={"pdf_classification": "native_text", "pdf_ocr_available": True},
    )
    ctx = CompilationContext(source="test_native.pdf")
    ctx.document = doc
    ctx.original_tokens = 200
    result = compute_confidence(ctx)
    assert result.score >= 70, (
        f"Native text PDF scored {result.score} — should be OK or better."
    )
    assert _quality_band(result.score) in ("HIGH", "OK"), (
        f"Expected HIGH or OK band for native text, got {_quality_band(result.score)}"
    )
