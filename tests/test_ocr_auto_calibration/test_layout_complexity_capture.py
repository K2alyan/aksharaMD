"""Tests for the layout-complexity capture module.

Focus is on the pure ``capture_from_features`` boundary — it derives
OCR-required counts, char totals, and rejected-table totals from the
neutral feature model. The ``capture_pdf`` PDF-parsing path is
smoke-tested when PyMuPDF is available.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aksharamd.plugins.parsers.layout_complexity import (
    LayoutDocumentFeatures,
    LayoutPageFeatures,
)
from benchmarks.ocr_auto_calibration.layout_complexity_capture import (
    LayoutComplexityCapture,
    capture_from_features,
    per_signal_page_counts,
)


def _neutral_page(page_index: int = 0, **overrides: object) -> LayoutPageFeatures:
    base = LayoutPageFeatures(
        page_index=page_index,
        page_width=612.0,
        page_height=792.0,
        page_char_count=2000,
        span_count=50,
        mean_span_char_length=40.0,
        has_ocr_pixmap=False,
        image_count=0,
        image_area_ratio=0.0,
        table_count=0,
        rejected_table_candidate_count=0,
        column_count=1,
        math_bbox_count=0,
        figure_caption_hit_count=0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# ── Pure capture path ────────────────────────────────────────────────


def test_capture_from_features_native_text_document() -> None:
    features = LayoutDocumentFeatures(
        pages=tuple(_neutral_page(page_index=i) for i in range(10))
    )
    capture = capture_from_features(
        document_id="native-text-doc", features=features, parse_runtime_ms=42.0
    )
    assert isinstance(capture, LayoutComplexityCapture)
    assert capture.document_id == "native-text-doc"
    assert capture.total_pages == 10
    assert capture.ocr_required_page_count == 0
    assert capture.ocr_required_fraction == 0.0
    assert capture.page_char_count_total == 20_000
    assert capture.rejected_table_candidate_total == 0
    assert capture.parse_runtime_ms == 42.0
    assert capture.evaluate_runtime_ms >= 0.0
    assert capture.decision.band == "simple"


def test_capture_from_features_image_only_scan_document() -> None:
    scan_page_kwargs = dict(
        page_char_count=0,
        span_count=0,
        mean_span_char_length=0.0,
        has_ocr_pixmap=True,
        image_count=1,
        image_area_ratio=1.0,
    )
    features = LayoutDocumentFeatures(
        pages=tuple(
            _neutral_page(page_index=i, **scan_page_kwargs)  # type: ignore[arg-type]
            for i in range(5)
        )
    )
    capture = capture_from_features(document_id="scan-doc", features=features)
    assert capture.total_pages == 5
    assert capture.ocr_required_page_count == 5
    assert capture.ocr_required_fraction == 1.0
    assert capture.page_char_count_total == 0
    assert capture.decision.band == "simple"


def test_capture_from_features_zero_pages_yields_zero_fraction() -> None:
    features = LayoutDocumentFeatures(pages=())
    capture = capture_from_features(document_id="empty", features=features)
    assert capture.total_pages == 0
    assert capture.ocr_required_page_count == 0
    assert capture.ocr_required_fraction == 0.0
    assert capture.decision.band == "simple"


def test_capture_from_features_records_rejected_table_total_uncapped() -> None:
    """The capture surfaces the raw pre-cap total so the analysis step
    can decide how to use it. The evaluator's cap still applies to the
    decision's score, but the raw signal remains visible."""
    features = LayoutDocumentFeatures(
        pages=(
            _neutral_page(page_index=0, rejected_table_candidate_count=1000),
            _neutral_page(page_index=1, rejected_table_candidate_count=2000),
        )
    )
    capture = capture_from_features(document_id="rej-heavy", features=features)
    assert capture.rejected_table_candidate_total == 3000
    # Decision's score is bounded by the cap even though the raw total isn't.
    assert capture.decision.score <= 100.0


# ── per_signal_page_counts convenience ───────────────────────────────


def test_per_signal_page_counts_exposes_int_counts() -> None:
    features = LayoutDocumentFeatures(
        pages=(
            _neutral_page(page_index=0, column_count=2, table_count=1),
            _neutral_page(page_index=1, column_count=2),
        )
    )
    capture = capture_from_features(document_id="doc", features=features)
    counts = per_signal_page_counts(capture)
    assert counts["multi_column"] == 2
    assert counts["table"] == 1
    assert counts["figure_caption"] == 0


# ── PyMuPDF smoke test ───────────────────────────────────────────────


def test_capture_pdf_smoke(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Round-trip: write a tiny PDF with fitz, parse it, evaluate.

    Only asserts high-level invariants — the exact score depends on
    fitz's tokenizer output for the built-in font, which varies
    across fitz versions. The point is that the capture path can
    parse a real PDF and produce a bounded decision.
    """
    pytest.importorskip("fitz")
    import fitz  # type: ignore[import-untyped]

    from benchmarks.ocr_auto_calibration.layout_complexity_capture import capture_pdf

    pdf_path = tmp_path / "smoke.pdf"
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page()
        page.insert_text(
            (72, 100), "The quick brown fox jumps over the lazy dog."
        )
    doc.save(pdf_path)
    doc.close()

    capture = capture_pdf(document_id="smoke", pdf_path=pdf_path)
    assert capture.document_id == "smoke"
    assert capture.total_pages == 3
    assert 0.0 <= capture.decision.score <= 100.0
    assert capture.decision.band in {"simple", "moderate", "complex"}
    assert capture.parse_runtime_ms >= 0.0
