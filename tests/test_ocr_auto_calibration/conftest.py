"""Fixtures for the OCR Auto Policy v1 calibration harness tests.

None of these fixtures touch real subprocesses, real GPUs, or the network.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest


@pytest.fixture
def tmp_corpus(tmp_path: Path) -> list:
    """Build a tiny 1-doc corpus for fast unit tests.

    Creates a minimal 2-page PDF (one image page, one native page) under
    ``tmp_path`` and wraps it in a :class:`CorpusEntry`.
    """
    fitz = pytest.importorskip("fitz")
    from PIL import Image

    from benchmarks.ocr_auto_calibration.corpus import CorpusEntry

    pdf_path = tmp_path / "tiny.pdf"
    pdf = fitz.open()
    # Page 0: image-only (OCR-required)
    page = pdf.new_page(width=595, height=842)
    img = Image.new("RGB", (32, 32), color=(220, 220, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 50, 545, 792), stream=buf.getvalue())
    # Page 1: native text
    page = pdf.new_page(width=595, height=842)
    page.insert_text(
        (72, 100),
        "This native page carries plenty of extractable text for the test.",
        fontsize=12,
    )
    pdf.save(str(pdf_path))
    pdf.close()

    return [
        CorpusEntry(
            document_id="tiny",
            path=pdf_path,
            sha256=None,
            profile_class="mini_test_corpus",
            expected_backend_by_policy="tesseract",
            source="synthetic",
            notes="conftest tiny fixture",
        )
    ]
