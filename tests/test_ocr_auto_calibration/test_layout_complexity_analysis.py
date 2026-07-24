"""Tests for the layout-complexity analysis and report writer.

Uses in-memory captures built from synthetic ``LayoutDocumentFeatures``
so nothing here requires a PDF, fitz, or network. Every property the
milestone spec asks for is pinned:

* the layout-vs-OCR table separates native-text from scanned docs;
* false-positive report flags complex-but-native-text papers;
* rejected-table predictor reports both raw pairs and a correlation
  interpretation once the corpus is large enough.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from aksharamd.plugins.parsers.layout_complexity import (
    LayoutDocumentFeatures,
    LayoutPageFeatures,
)
from benchmarks.ocr_auto_calibration.layout_complexity_analysis import (
    CORRELATION_MIN_SAMPLE_SIZE,
    FALSE_POSITIVE_MIN_TOTAL_CHARS,
    LAYOUT_COMPLEXITY_ANALYSIS_VERSION,
    analyze,
    false_positive_report,
    layout_vs_ocr_table,
    rejected_table_candidate_predictor,
)
from benchmarks.ocr_auto_calibration.layout_complexity_capture import (
    capture_from_features,
)
from benchmarks.ocr_auto_calibration.layout_complexity_report import (
    write_analysis_json,
    write_capture_json,
    write_markdown_report,
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


def _capture(document_id: str, features: LayoutDocumentFeatures):
    return capture_from_features(document_id=document_id, features=features)


def _native_arxiv_features(*, pages: int, table_count: int = 0) -> LayoutDocumentFeatures:
    """A native-text multi-column scientific paper: plenty of chars,
    two columns per page, some tables. No OCR pixmap flagged."""
    return LayoutDocumentFeatures(
        pages=tuple(
            _neutral_page(
                page_index=i,
                column_count=2,
                page_char_count=3000,
                table_count=table_count,
                figure_caption_hit_count=1,
            )
            for i in range(pages)
        )
    )


def _scan_features(*, pages: int) -> LayoutDocumentFeatures:
    return LayoutDocumentFeatures(
        pages=tuple(
            _neutral_page(
                page_index=i,
                page_char_count=0,
                span_count=0,
                mean_span_char_length=0.0,
                has_ocr_pixmap=True,
                image_count=1,
                image_area_ratio=1.0,
            )
            for i in range(pages)
        )
    )


# ── Layout-vs-OCR table ──────────────────────────────────────────────


def test_layout_vs_ocr_table_populates_row_per_capture() -> None:
    captures = [
        _capture("arxiv-attn", _native_arxiv_features(pages=15, table_count=1)),
        _capture("scan-doc", _scan_features(pages=20)),
    ]
    table = layout_vs_ocr_table(captures)
    assert len(table.rows) == 2

    arxiv_row = next(r for r in table.rows if r.document_id == "arxiv-attn")
    scan_row = next(r for r in table.rows if r.document_id == "scan-doc")

    assert arxiv_row.is_native_text_dominant is True
    assert arxiv_row.ocr_required_fraction == 0.0
    assert arxiv_row.page_char_count_total >= FALSE_POSITIVE_MIN_TOTAL_CHARS

    assert scan_row.is_native_text_dominant is False
    assert scan_row.ocr_required_fraction == 1.0
    assert scan_row.page_char_count_total == 0


def test_layout_vs_ocr_table_summary_counts_bands() -> None:
    captures = [
        _capture("native", _native_arxiv_features(pages=15, table_count=1)),
        _capture("scan", _scan_features(pages=10)),
    ]
    table = layout_vs_ocr_table(captures)
    summary = table.summary
    assert summary["documents"] == 2.0
    # Bands sum to total.
    band_total = (
        summary["band.simple_count"]
        + summary["band.moderate_count"]
        + summary["band.complex_count"]
    )
    assert band_total == 2.0


def test_layout_vs_ocr_table_handles_empty_corpus() -> None:
    table = layout_vs_ocr_table([])
    assert table.rows == ()
    assert table.summary["documents"] == 0.0


# ── False-positive report ────────────────────────────────────────────


def test_false_positive_flags_native_text_complex_doc() -> None:
    """The scientific-corpus caveat pinned by the milestone spec: a
    native-text multi-column paper with tables classifies as
    moderate/complex layout but has zero OCR-required pages.  The
    report MUST flag it as a false-positive candidate."""
    captures = [
        _capture(
            "arxiv-native",
            _native_arxiv_features(pages=15, table_count=2),
        )
    ]
    report = false_positive_report(captures)
    assert report.total_documents_considered == 1
    assert report.documents_excluded_short == 0
    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.document_id == "arxiv-native"
    assert entry.layout_band in {"moderate", "complex"}
    assert "native-text-dominant" in entry.reason


def test_false_positive_does_not_flag_simple_native_text_doc() -> None:
    captures = [_capture("plain", LayoutDocumentFeatures(pages=(_neutral_page(),)))]
    report = false_positive_report(captures)
    # Simple band = no false positive by construction.
    assert report.entries == ()


def test_false_positive_does_not_flag_scanned_doc() -> None:
    """A scanned doc has few native-text chars, so it is excluded from
    the false-positive tally entirely — the report is about docs that
    have text AND are layout-complex."""
    captures = [_capture("scan", _scan_features(pages=10))]
    report = false_positive_report(captures)
    assert report.entries == ()
    assert report.documents_excluded_short == 1
    assert report.total_documents_considered == 0


# ── Rejected-table-candidate predictor ──────────────────────────────


def test_predictor_reports_insufficient_sample_when_below_threshold() -> None:
    captures = [
        _capture(f"doc-{i}", _native_arxiv_features(pages=5))
        for i in range(CORRELATION_MIN_SAMPLE_SIZE - 1)
    ]
    pred = rejected_table_candidate_predictor(captures)
    assert pred.correlation_available is False
    assert pred.pearson_r is None
    assert "insufficient sample" in pred.interpretation
    assert len(pred.pairs) == CORRELATION_MIN_SAMPLE_SIZE - 1


def test_predictor_positive_correlation_case() -> None:
    """Synthetic captures where rejected-count moves with OCR fraction."""
    captures = []
    for i in range(CORRELATION_MIN_SAMPLE_SIZE):
        n_scan_pages = i
        n_native_pages = CORRELATION_MIN_SAMPLE_SIZE - i
        pages = []
        for j in range(n_native_pages):
            pages.append(
                _neutral_page(
                    page_index=j,
                    rejected_table_candidate_count=i * 5,
                )
            )
        for j in range(n_scan_pages):
            pages.append(
                _neutral_page(
                    page_index=n_native_pages + j,
                    page_char_count=0,
                    span_count=0,
                    mean_span_char_length=0.0,
                    has_ocr_pixmap=True,
                )
            )
        captures.append(
            _capture(f"doc-{i}", LayoutDocumentFeatures(pages=tuple(pages)))
        )
    pred = rejected_table_candidate_predictor(captures)
    assert pred.correlation_available is True
    assert pred.pearson_r is not None
    # The construction produces a monotone increase in both x and y.
    assert pred.pearson_r > 0.0


def test_predictor_handles_invariant_series() -> None:
    captures = [
        _capture(f"doc-{i}", _native_arxiv_features(pages=5))
        for i in range(CORRELATION_MIN_SAMPLE_SIZE)
    ]
    pred = rejected_table_candidate_predictor(captures)
    # All docs have the same rejected count (0) and same OCR fraction (0),
    # so Pearson r is undefined.
    assert pred.correlation_available is False
    assert "undefined" in pred.interpretation.lower()


# ── Analyze convenience + full analysis payload ─────────────────────


def test_analyze_bundles_all_three_analyses() -> None:
    captures = [
        _capture("arxiv-native", _native_arxiv_features(pages=15, table_count=2)),
        _capture("scan", _scan_features(pages=10)),
    ]
    analysis = analyze(captures)
    assert analysis.analysis_version == LAYOUT_COMPLEXITY_ANALYSIS_VERSION
    assert len(analysis.layout_vs_ocr.rows) == 2
    assert len(analysis.false_positives.entries) >= 1
    assert len(analysis.rejected_table_predictor.pairs) == 2


# ── Report writers ──────────────────────────────────────────────────


def test_write_capture_json_round_trips(tmp_path: Path) -> None:
    captures = [_capture("doc", _native_arxiv_features(pages=5))]
    out = tmp_path / "capture.json"
    write_capture_json(captures=captures, out_path=out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["policy_version"]
    (row,) = payload["captures"]
    assert row["document_id"] == "doc"
    assert row["total_pages"] == 5
    assert "decision" in row
    assert row["decision"]["band"] in {"simple", "moderate", "complex"}


def test_write_analysis_json_round_trips(tmp_path: Path) -> None:
    captures = [_capture("d", _native_arxiv_features(pages=5))]
    analysis = analyze(captures)
    out = tmp_path / "analysis.json"
    write_analysis_json(analysis=analysis, out_path=out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["analysis_version"]
    assert "layout_vs_ocr" in payload
    assert "false_positives" in payload
    assert "rejected_table_predictor" in payload


def test_write_markdown_report_renders_expected_sections(tmp_path: Path) -> None:
    captures = [
        _capture("arxiv-native", _native_arxiv_features(pages=15, table_count=2)),
        _capture("scan", _scan_features(pages=10)),
    ]
    analysis = analyze(captures)
    out = tmp_path / "REPORT.md"
    write_markdown_report(
        analysis=analysis,
        captures=captures,
        corpus_name="test-corpus",
        out_path=out,
    )
    body = out.read_text(encoding="utf-8")
    assert "Layout Complexity v1 — Evidence Report (test-corpus)" in body
    assert "Layout complexity vs OCR difficulty" in body
    assert "False-positive candidates" in body
    assert "Rejected-table-candidate as a UOC-benefit predictor" in body
    assert "Caveats" in body
    assert "no production routing" in body.lower()


def test_write_markdown_report_handles_empty_corpus(tmp_path: Path) -> None:
    analysis = analyze([])
    out = tmp_path / "REPORT.md"
    write_markdown_report(
        analysis=analysis,
        captures=[],
        corpus_name="empty",
        out_path=out,
    )
    body = out.read_text(encoding="utf-8")
    assert "No documents captured" in body
