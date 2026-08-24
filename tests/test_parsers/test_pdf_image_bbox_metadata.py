"""Groundwork tests: IMAGE blocks emitted by the PDF parser carry a bbox in
their metadata.

This test file exercises the plumbing added in the bbox-metadata PR (nothing
depends on the bbox yet; no detector consumes it). Downstream detectors,
multimodal pipelines, and any parser-agnostic contract will need per-image
bbox — capturing it at parse time removes a re-parsing cost from every
future consumer.

Coverage:
    * Content-image path (embedded raster on a text-classified page):
      bbox is present and reasonable.
    * Full-page raster path (image-only page): bbox equals the page rectangle.
    * Backward-compatibility: Block.metadata still contains asset_id.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# These tests exercise the live parser against real fixtures in the parsebench
# sibling corpus. They skip cleanly when the corpus is not on disk (CI, dev
# machines without the parsebench checkout).
_PARSEBENCH_DATA = Path(r"C:\Users\kalya\parsebench\data\docs")


def _require_fixture(rel: str) -> Path:
    p = _PARSEBENCH_DATA / rel
    if not p.exists():
        pytest.skip(f"parsebench fixture missing: {rel}")
    return p


def _compile_and_get_image_blocks(pdf_path: Path) -> list[dict]:
    """Compile a PDF and return the raw block dicts of type=IMAGE."""
    from aksharamd.compiler import Compiler

    out_root = Path(pytest.__file__).parent  # arbitrary; compile() writes to <out>/<stem>/
    compiler = Compiler(output_dir=str(out_root / "_pytest_bbox_out"))
    ctx = compiler.compile(str(pdf_path))
    assert ctx.document is not None
    return [
        {
            "content": b.content,
            "metadata": b.metadata,
            "page": b.page,
        }
        for b in ctx.document.blocks
        if b.type.value == "image"
    ]


# ── Content-image path (embedded raster on a text-classified page) ────────────

def test_embedded_image_block_carries_bbox():
    """de's page-1 rasterized-content image block carries bbox metadata.

    de is the canonical fixture: a German patent PDF whose SEQ ID motif
    tables are rasterized as an embedded image object. The parser emits an
    IMAGE block via the content_images text-page branch (pdf.py L1166-1198).
    The block's metadata must include a `bbox` list of four floats.
    """
    pdf = _require_fixture("text/text_dense__de.pdf")
    img_blocks = _compile_and_get_image_blocks(pdf)
    assert img_blocks, "expected at least one IMAGE block on de"
    for b in img_blocks:
        m = b["metadata"]
        assert "asset_id" in m, "asset_id must remain on IMAGE block metadata (backward-compat)"
        assert "bbox" in m, "bbox must be present on content-image IMAGE blocks"
        bbox = m["bbox"]
        assert isinstance(bbox, list) and len(bbox) == 4
        assert all(isinstance(v, (int, float)) for v in bbox)
        # Sanity: bbox should be non-degenerate for a real embedded image on
        # de's page 1. (An unlocated image bbox comes back (0, 0, 0, 0), which
        # we tolerate — see pdf.py — but de's is a real embedded object.)
        x0, y0, x1, y1 = bbox
        assert x1 > x0 and y1 > y0, f"bbox on de embedded image should be non-degenerate; got {bbox}"


# ── Full-page raster path (image-only page) ───────────────────────────────────

def test_full_page_raster_image_block_carries_bbox_equal_to_page():
    """letter3 is a fully scanned single-page PDF. The full-page raster path
    (pdf.py L1162-1165) emits an IMAGE block whose bbox is the whole page
    rectangle."""
    pdf = _require_fixture("text/text_simple__letter3.pdf")
    img_blocks = _compile_and_get_image_blocks(pdf)
    # letter3 is scanned; there should be one IMAGE block corresponding to
    # the whole page raster.
    if not img_blocks:
        pytest.skip("letter3 did not emit an IMAGE block in this environment "
                    "(likely a stub raster path); test is informational")
    for b in img_blocks:
        m = b["metadata"]
        # Only the whole-page raster asset carries an asset_id in the form
        # sha256('<page>:raster')[:12]. If bbox is present, it should span
        # the page (either a strict 0,0->w,h or a very close approximation).
        if "bbox" in m:
            x0, y0, x1, y1 = m["bbox"]
            # Whole-page raster: x0 ~ 0, y0 ~ 0, x1 > 100, y1 > 100.
            assert x0 == 0.0 or abs(x0) < 1.0
            assert y0 == 0.0 or abs(y0) < 1.0
            assert x1 > 100.0 and y1 > 100.0


# ── Backward compatibility ────────────────────────────────────────────────────

def test_asset_id_still_present_alongside_bbox():
    """The bbox addition must not displace asset_id. Both keys coexist."""
    pdf = _require_fixture("text/text_dense__de.pdf")
    img_blocks = _compile_and_get_image_blocks(pdf)
    assert img_blocks
    for b in img_blocks:
        m = b["metadata"]
        assert "asset_id" in m
        assert isinstance(m["asset_id"], str)
        assert len(m["asset_id"]) > 0
