"""Tesseract backend availability + process smoke tests.

The availability probe is mocked so the test runs regardless of
whether pytesseract / the tesseract binary are installed. The
process() smoke test operates on a small in-memory PDF and mocks the
inline OCR helper so we exercise the backend's plumbing, not
Tesseract itself.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from aksharamd.plugins.ocr_backends._protocol import (
    BackendCapabilities,
    OcrPageRequest,
)
from aksharamd.plugins.ocr_backends.tesseract_backend import TesseractBackend


def test_tesseract_capabilities():
    caps = TesseractBackend().capabilities()
    assert isinstance(caps, BackendCapabilities)
    assert caps.emits == "blocks"
    assert caps.supports_math is False


def test_tesseract_availability_when_pytesseract_missing():
    backend = TesseractBackend()
    fake_import_error = ImportError("No module named 'pytesseract'")

    with patch(
        "builtins.__import__",
        side_effect=lambda name, *a, **kw: (
            (_ for _ in ()).throw(fake_import_error)
            if name == "pytesseract"
            else __import__(name, *a, **kw)
        ),
    ):
        avail = backend.availability()
    assert avail.is_available is False
    assert "pytesseract" in avail.reason.lower()


def test_tesseract_availability_when_binary_ok():
    fake_pt = types.ModuleType("pytesseract")
    fake_pt.get_tesseract_version = lambda: "5.0.0"
    with patch.dict(sys.modules, {"pytesseract": fake_pt}):
        avail = TesseractBackend().availability()
    assert avail.is_available is True
    assert avail.reason == ""


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    """One-page synthetic PDF, no text — the OCR helper is mocked
    so what's on the page doesn't matter."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    pdf.new_page(width=100, height=100)
    p = tmp_path / "tiny.pdf"
    pdf.save(str(p))
    pdf.close()
    return p


def test_tesseract_process_returns_result_per_requested_page(tiny_pdf: Path):
    backend = TesseractBackend()
    from aksharamd.models.block import BlockType
    fake_tuples = [(BlockType.PARAGRAPH, "hello world", None)]

    with patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        return_value=fake_tuples,
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=tiny_pdf, page_indices=[0], dpi=150)
        )

    assert len(results) == 1
    r = results[0]
    assert r.page_index == 0
    assert r.is_ok is True
    assert r.blocks == fake_tuples


def test_tesseract_process_empty_page_indices(tiny_pdf: Path):
    backend = TesseractBackend()
    with patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        return_value=[],
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=tiny_pdf, page_indices=[], dpi=150)
        )
    assert results == []
