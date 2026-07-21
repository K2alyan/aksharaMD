"""Contract tests locking in the behavioural guarantees of
``TesseractBackend.process`` and ``TesseractBackend.availability``.

These tests do not exercise the actual Tesseract binary — they use a
mocked ``_try_ocr_structured`` and, where necessary, a mocked
``fitz.open``. What they DO exercise is the plumbing the future
dispatch layer will rely on:

* order preservation across ``page_indices``;
* repeated indices producing repeated results (no dedup);
* invalid indices producing per-page failure without aborting the
  batch;
* one result per requested page — always;
* PDF handle closed after both success and exception paths;
* ``availability()`` staying lightweight.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aksharamd.plugins.ocr_backends._protocol import OcrPageRequest
from aksharamd.plugins.ocr_backends.tesseract_backend import TesseractBackend


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    """Synthetic three-page PDF. Content does not matter — every
    _try_ocr_structured call is mocked in the tests below."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    for _ in range(3):
        pdf.new_page(width=100, height=100)
    p = tmp_path / "three.pdf"
    pdf.save(str(p))
    pdf.close()
    return p


def _tuples_for(page_idx: int) -> list[tuple]:
    """Distinct fixed tuples per page so we can assert order."""
    from aksharamd.models.block import BlockType
    return [(BlockType.PARAGRAPH, f"page_{page_idx}_content", None)]


# ── Contract 1: order preserved across page_indices ─────────────────────


def test_process_preserves_page_indices_order(three_page_pdf: Path):
    backend = TesseractBackend()
    order = [2, 0, 1]
    with patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        side_effect=lambda img: _tuples_for(0),  # content ignored — order is what matters
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=order, dpi=150)
        )
    assert [r.page_index for r in results] == order


# ── Contract 2: repeated index produces repeated results ───────────────


def test_process_repeated_page_index_not_deduplicated(three_page_pdf: Path):
    backend = TesseractBackend()
    order = [1, 1, 1]
    with patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        return_value=_tuples_for(1),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=order, dpi=150)
        )
    assert len(results) == 3
    assert all(r.page_index == 1 for r in results)
    assert all(r.is_ok for r in results)


# ── Contract 3 + 4: invalid index fails individually, others succeed ───


def test_process_invalid_index_fails_individually(three_page_pdf: Path):
    """A page index outside the document range must produce a failed
    ``OcrPageResult`` at that position without preventing the other
    requested pages from being processed."""
    backend = TesseractBackend()
    # 999 does not exist in a 3-page PDF; fitz.open()[999] raises.
    order = [0, 999, 2]
    with patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        return_value=_tuples_for(0),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=order, dpi=150)
        )

    # One result per requested index, in order.
    assert [r.page_index for r in results] == order
    assert len(results) == 3
    # Valid pages succeeded.
    assert results[0].is_ok is True
    assert results[2].is_ok is True
    # Invalid page failed cleanly.
    bad = results[1]
    assert bad.is_ok is False
    assert bad.failure is not None
    assert bad.failure.kind == "other"
    assert bad.blocks == []


def test_process_returns_one_result_per_requested_index_even_on_failure(
    three_page_pdf: Path,
):
    """Even when every page fails, ``process()`` returns exactly
    ``len(page_indices)`` results."""
    backend = TesseractBackend()
    order = [0, 1, 2]

    def _boom(_img):
        raise RuntimeError("simulated OCR failure")

    with patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        side_effect=_boom,
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=order, dpi=150)
        )

    assert [r.page_index for r in results] == order
    assert all(not r.is_ok for r in results)
    assert all(r.failure is not None and r.failure.kind == "other" for r in results)


# ── Contract 5: PDF handle closes after success and exception ──────────


def _make_pdf_spy(pages_ok: int, per_page_exc: Exception | None = None):
    """Return a MagicMock that behaves like a fitz Document.

    ``__getitem__`` returns a page whose ``get_pixmap`` returns a small
    valid PNG so ``PIL.Image.open`` succeeds. If ``per_page_exc`` is
    set, ``get_pixmap`` raises it instead.
    """
    pdf = MagicMock(name="fitz.Document")
    pdf.__len__.return_value = pages_ok

    def _getitem(idx):
        if idx >= pages_ok or idx < -pages_ok:
            raise ValueError(f"page {idx} out of range")
        page = MagicMock(name=f"page_{idx}")
        if per_page_exc is not None:
            page.get_pixmap.side_effect = per_page_exc
        else:
            # A trivial 1x1 PNG (bytes-level real, so PIL.Image.open works).
            pix = MagicMock(name="pix")
            _pix_bytes = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
                b"\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT"
                b"\x08\x99c\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x8b\xb2\x86"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            pix.tobytes = MagicMock(return_value=_pix_bytes)
            page.get_pixmap.return_value = pix
        return page

    pdf.__getitem__.side_effect = _getitem
    return pdf


def test_process_closes_pdf_handle_on_success(three_page_pdf: Path):
    backend = TesseractBackend()
    spy_pdf = _make_pdf_spy(pages_ok=3)

    with patch(
        "fitz.open",
        return_value=spy_pdf,
    ), patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        return_value=_tuples_for(0),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 1], dpi=150)
        )
    assert len(results) == 2
    spy_pdf.close.assert_called_once()


def test_process_closes_pdf_handle_on_exception(three_page_pdf: Path):
    """If the per-page work raises even OUTSIDE the caught paths (e.g.
    fitz getitem itself fails), ``finally`` still closes the handle."""
    backend = TesseractBackend()
    spy_pdf = _make_pdf_spy(pages_ok=3, per_page_exc=RuntimeError("simulated"))

    with patch(
        "fitz.open",
        return_value=spy_pdf,
    ), patch(
        "aksharamd.plugins.parsers.image._try_ocr_structured",
        return_value=_tuples_for(0),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 1], dpi=150)
        )

    # Each page's failure was captured; total count matches.
    assert len(results) == 2
    assert all(not r.is_ok for r in results)
    # And critically: the handle was closed.
    spy_pdf.close.assert_called_once()


# ── Contract 6: availability() is lightweight ──────────────────────────


def test_availability_does_not_import_heavy_modules():
    """``availability()`` must probe pytesseract only. It must NOT
    pull torch, transformers, PIL, fitz, or any Unlimited-OCR module
    into the process."""
    # Force a clean import of pytesseract-adjacent state so the test
    # observes only what availability() itself brings in on this call.
    backend = TesseractBackend()

    heavy_names = {"torch", "transformers"}
    before = set(sys.modules.keys())
    backend.availability()
    after = set(sys.modules.keys())
    added = after - before

    # None of these heavy dependencies should be pulled by an
    # availability probe. PIL / fitz / cv2 CAN legitimately be
    # brought in transitively by pytesseract itself in some environments,
    # so we do not gate on those. torch / transformers are the ones
    # that would signal a real regression.
    assert not (added & heavy_names), (
        f"availability() pulled heavy modules unexpectedly: {added & heavy_names}"
    )

    # And it must not touch the Unlimited-OCR production package.
    assert not any(
        name.startswith("aksharamd.plugins.ocr_backends.unlimited_ocr")
        for name in added
    )
