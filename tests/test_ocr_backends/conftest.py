"""Shared fixtures for PR 94c OCR-backend dispatch tests.

Generates small controlled PDFs via PyMuPDF:

* ``digital_only.pdf`` — pages with extractable text; classifier marks
  0 pages as OCR-required.
* ``scanned_only.pdf`` — pages that hold only a rasterized image; the
  classifier marks every page as OCR-required.
* ``mixed.pdf`` — page 0 digital text, page 1 scanned image, page 2
  digital text; classifier marks only page 1.

Names are kept short intentionally: Windows MAX_PATH is brittle and
this project has already been bitten by long tmp path names.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _make_digital_page(pdf, text: str) -> None:
    """Add a page with real text content — extractable by pymupdf."""
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), text, fontsize=12)


def _make_scanned_page(pdf) -> None:
    """Add a page with only a rasterized image and NO text so the
    OCR classifier (``chars < _OCR_TEXT_THRESHOLD``) marks it as
    OCR-required.

    We build the PNG through Pillow to sidestep hand-crafted-hex-byte
    fragility with the PyMuPDF PNG decoder.
    """
    import io

    import fitz  # type: ignore
    from PIL import Image

    img = Image.new("RGB", (16, 16), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    page = pdf.new_page(width=595, height=842)
    rect = fitz.Rect(50, 50, 550, 800)
    page.insert_image(rect, stream=buf.getvalue())


@pytest.fixture
def digital_only_pdf(tmp_path: Path) -> Path:
    """Two-page PDF, both pages carry extractable text."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    _make_digital_page(
        pdf,
        "This is a fully digital PDF page with more than fifty extractable "
        "characters so the OCR classifier will skip this page entirely.",
    )
    _make_digital_page(
        pdf,
        "Second page of the digital-only test document. Sufficient text "
        "here to also pass the OCR threshold and avoid rasterization.",
    )
    p = tmp_path / "dig.pdf"
    pdf.save(str(p))
    pdf.close()
    return p


@pytest.fixture
def scanned_only_pdf(tmp_path: Path) -> Path:
    """Two-page PDF, both pages hold only images (no text layer)."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    _make_scanned_page(pdf)
    _make_scanned_page(pdf)
    p = tmp_path / "scn.pdf"
    pdf.save(str(p))
    pdf.close()
    return p


@pytest.fixture
def mixed_pdf(tmp_path: Path) -> Path:
    """Three-page PDF: page 0 digital, page 1 scanned, page 2 digital."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    _make_digital_page(
        pdf,
        "First page carries extractable text well above the classifier "
        "threshold so no OCR is scheduled for this page.",
    )
    _make_scanned_page(pdf)
    _make_digital_page(
        pdf,
        "Third page again carries extractable text so only the middle page "
        "should be routed to the OCR backend.",
    )
    p = tmp_path / "mix.pdf"
    pdf.save(str(p))
    pdf.close()
    return p
