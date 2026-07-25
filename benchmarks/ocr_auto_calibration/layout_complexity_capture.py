"""Layout-complexity per-document capture (Commit 3 of the Layout
Complexity v1 milestone).

Consumes a PDF path and produces a
:class:`LayoutComplexityCapture` — a structured record of the layout
features, the evaluator's decision, and the observed page-level
OCR-required signal. Measures parse and evaluate runtime so the
downstream evidence report can compare complexity classification cost
against actual OCR cost.

This module is EVIDENCE ONLY. It does not run OCR, does not compile
the document, and does not touch the manifest. The only production
code it imports is:

* :func:`aksharamd.plugins.parsers.pdf.extract_layout_document_features`
  — the neutral bridge from Commit 1;
* :func:`aksharamd.plugins.parsers.layout_complexity_evaluator.evaluate_layout_complexity`
  — the pure evaluator from Commit 2.

The parser itself is invoked via a minimal path: parse the PDF into
:class:`aksharamd.plugins.parsers.pdf.RawPage` values using PyMuPDF
directly, then hand them to the bridge. This avoids constructing a
full :class:`~aksharamd.compiler.Compiler` for what is a metadata
inspection.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from aksharamd.plugins.parsers.layout_complexity import LayoutDocumentFeatures
from aksharamd.plugins.parsers.layout_complexity_evaluator import (
    LayoutComplexityDecision,
    evaluate_layout_complexity,
)


@dataclass(frozen=True)
class LayoutComplexityCapture:
    """One document's layout-complexity evidence record.

    Field notes:

    * ``document_id`` mirrors the calling harness identifier (arxiv id,
      ParseBench id, or a synthetic label).
    * ``total_pages`` and ``ocr_required_page_count`` are direct counts
      over :class:`LayoutDocumentFeatures.pages`. ``ocr_required_fraction``
      is ``ocr_required_page_count / total_pages`` (0.0 on empty docs).
    * ``page_char_count_total`` is the sum of native text characters —
      the analysis uses this to distinguish native-text papers from
      scans without needing to import the parser's classifier.
    * ``rejected_table_candidate_total`` is the raw pre-cap sum of
      the same signal across pages. It is a candidate predictor for
      UOC benefit; the analysis step decides how to use it.
    * ``parse_runtime_ms`` and ``evaluate_runtime_ms`` are wall-clock
      measurements from :func:`time.perf_counter`. Non-deterministic
      by nature — treat as informational, not a test property.
    * ``decision`` is the full :class:`LayoutComplexityDecision`.
    """

    document_id: str
    total_pages: int
    ocr_required_page_count: int
    ocr_required_fraction: float
    page_char_count_total: int
    rejected_table_candidate_total: int
    parse_runtime_ms: float
    evaluate_runtime_ms: float
    decision: LayoutComplexityDecision


def capture_from_features(
    *,
    document_id: str,
    features: LayoutDocumentFeatures,
    parse_runtime_ms: float = 0.0,
) -> LayoutComplexityCapture:
    """Pure evaluator wrapping. Used both by :func:`capture_pdf` and
    by unit tests that assemble ``features`` in memory rather than
    reading a real PDF."""
    total_pages = features.total_pages
    ocr_required = sum(1 for page in features.pages if page.has_ocr_pixmap)
    page_char_total = sum(page.page_char_count for page in features.pages)
    rejected_total = sum(
        page.rejected_table_candidate_count for page in features.pages
    )
    ocr_fraction = ocr_required / total_pages if total_pages else 0.0

    evaluate_start = time.perf_counter()
    decision = evaluate_layout_complexity(features)
    evaluate_runtime_ms = (time.perf_counter() - evaluate_start) * 1000.0

    return LayoutComplexityCapture(
        document_id=document_id,
        total_pages=total_pages,
        ocr_required_page_count=ocr_required,
        ocr_required_fraction=ocr_fraction,
        page_char_count_total=page_char_total,
        rejected_table_candidate_total=rejected_total,
        parse_runtime_ms=parse_runtime_ms,
        evaluate_runtime_ms=evaluate_runtime_ms,
        decision=decision,
    )


def capture_pdf(
    *,
    document_id: str,
    pdf_path: Path,
) -> LayoutComplexityCapture:
    """Parse ``pdf_path`` with the same bridge production uses, then
    evaluate layout complexity. Measures parse + evaluate runtime
    separately so the analysis step can attribute cost.

    The import of the parser is deferred to keep this module cheap to
    import in unit tests that only exercise the pure paths.
    """
    from aksharamd.plugins.parsers.pdf import extract_layout_document_features

    parse_start = time.perf_counter()
    raw_pages = _parse_pdf_to_raw_pages(pdf_path)
    features = extract_layout_document_features(raw_pages)
    parse_runtime_ms = (time.perf_counter() - parse_start) * 1000.0

    # If parsing produced no pages at all (corrupt PDF, empty file),
    # evaluate against an empty document; the decision will land in
    # the simple band with score 0. Callers can inspect
    # ``total_pages == 0`` to decide whether to surface the doc as a
    # skipped-invalid rather than a real evidence point.

    return capture_from_features(
        document_id=document_id,
        features=features,
        parse_runtime_ms=parse_runtime_ms,
    )


def _parse_pdf_to_raw_pages(pdf_path: Path) -> list:  # type: ignore[type-arg]
    """Minimal PDF -> :class:`RawPage` conversion for the capture path.

    Uses PyMuPDF the same way :func:`aksharamd.plugins.parsers.pdf.
    _process_raw_page` does, but does NOT run the whole PDFParser
    pipeline (which would build blocks, run OCR probes, and touch the
    manifest). Layout-complexity capture is a metadata inspection.

    Return type is intentionally untyped: :class:`RawPage` is imported
    lazily from ``parsers.pdf`` (which pulls in fitz + marker), and the
    return value is passed straight to the bridge — no external caller
    depends on the concrete list-item type.
    """
    import fitz  # type: ignore[import-untyped]

    from aksharamd.plugins.parsers.pdf import RawPage

    raw_pages: list = []
    doc = fitz.open(pdf_path)
    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            spans = _collect_spans(page)
            images = _collect_images(page)
            tables = _collect_tables(page)
            raw_pages.append(
                RawPage(
                    page_num=page_index + 1,
                    spans=spans,
                    tables=tables,
                    images=images,
                    height=float(page.rect.height),
                    width=float(page.rect.width),
                    ocr_pixmap=None,
                    embedded_image_bytes=[],
                    content_images=[],
                    math_bboxes=[],
                    rejected_candidates=[],
                )
            )
    finally:
        doc.close()
    return raw_pages


def _collect_spans(page: object) -> list[dict]:
    """Extract text spans in the shape ``LayoutPageFeatures`` expects.

    Reuses PyMuPDF's ``get_text("dict")`` output; each span dict is a
    ``{x, y, text, size}`` compatible with the Commit 1 bridge and
    with the parser's own span consumers.
    """
    spans: list[dict] = []
    text_dict = page.get_text("dict")  # type: ignore[attr-defined]
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                spans.append(
                    {
                        "x": float(bbox[0]),
                        "y": float(bbox[1]),
                        "text": span.get("text", ""),
                        "size": float(span.get("size", 0.0)),
                    }
                )
    return spans


def _collect_images(page: object) -> list[dict]:
    """Collect embedded raster image bboxes on the page.

    Uses :meth:`fitz.Page.get_image_info` (safe, read-only) rather than
    ``get_drawings`` — the milestone spec explicitly forbids adding a
    ``get_drawings`` call in this workstream.
    """
    infos = page.get_image_info(hashes=False)  # type: ignore[attr-defined]
    images: list[dict] = []
    for info in infos:
        bbox = info.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        images.append(
            {"bbox": (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))}
        )
    return images


def _collect_tables(page: object) -> list[dict]:
    """Enumerate structured tables via PyMuPDF's table finder.

    Returns an empty list when the fitz build in use does not ship the
    table finder (older versions). That is a documented degradation —
    the capture is still useful for the other signals (columns, images,
    figure captions).
    """
    finder = getattr(page, "find_tables", None)
    if finder is None:
        return []
    try:
        found = finder()
    except Exception:
        return []
    tables_attr = getattr(found, "tables", None) or found
    result: list[dict] = []
    for table in tables_attr or []:
        extract = getattr(table, "extract", None)
        cells = extract() if callable(extract) else []
        result.append({"cells": cells})
    return result


def per_signal_page_counts(capture: LayoutComplexityCapture) -> Mapping[str, int]:
    """Extract the per-signal page-count subset of the decision's
    measurements as an ``{signal: page_count}`` view — a convenience
    for the analysis + report modules."""
    from aksharamd.plugins.parsers.layout_complexity_evaluator import _ALL_SIGNALS

    return MappingProxyType(
        {
            signal: int(capture.decision.measurements[f"{signal}.page_count"])
            for signal in _ALL_SIGNALS
        }
    )


__all__ = [
    "LayoutComplexityCapture",
    "capture_from_features",
    "capture_pdf",
    "per_signal_page_counts",
]
