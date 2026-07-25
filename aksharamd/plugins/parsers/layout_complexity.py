"""Neutral layout-complexity feature model for Auto OCR routing.

This module owns *value types only* — no parser imports, no domain
helpers, no I/O. The concrete PDF parser (``pdf.py``) is responsible
for turning its private ``RawPage`` shape into
:class:`LayoutPageFeatures` and :class:`LayoutDocumentFeatures` here,
so that the future complexity evaluator (Commit 2) and calibration
harness (Commit 3) depend only on this neutral surface and not on the
parser's internal geometry types.

Design contract
---------------

* No imports from :mod:`aksharamd.plugins.parsers.pdf` (which owns
  ``RawPage`` and private helpers like ``_detect_column_boundaries``).
  This is enforced by an import-order regression test.
* No scoring, no policy, no thresholds live here. This module is a
  *feature model*, not a decision model. The evaluator that ships in
  the next commit consumes these types and returns a
  :class:`LayoutComplexityDecision` (that class does not exist yet
  and MUST NOT be pre-declared here).
* Fields are additive-friendly. Any new field ships with a documented
  default so downstream callers (evaluator, calibration harness,
  manifest emitters) never break when a parser upgrade adds a signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Extractor version — bumped whenever the *set* of extracted features
# changes or a feature's definition changes. Ships in
# :attr:`LayoutDocumentFeatures.extractor_version` so downstream
# calibration reports can pin against a specific feature vintage.
LAYOUT_FEATURE_EXTRACTOR_VERSION = "1"


@dataclass(frozen=True)
class LayoutPageFeatures:
    """Per-page structural features consumed by the layout-complexity
    evaluator (Commit 2) and the calibration harness (Commit 3).

    Every field is a primitive or a tuple of primitives — no parser
    types, no fitz objects, no PIL images. Downstream code can carry
    this over a subprocess boundary or serialize it verbatim to a
    calibration report.

    Field semantics
    ---------------

    * ``page_index`` is the 0-based source-PDF page index (matches the
      convention of :class:`~aksharamd.plugins.ocr_backends._protocol.
      OcrPageRequest`).
    * ``page_width`` and ``page_height`` are in PDF points; page area
      is ``page_width * page_height``.
    * ``page_char_count`` counts text characters extracted from the
      native text layer (0 for image-only pages).
    * ``span_count`` is the number of parser spans on the page (a rough
      proxy for text-block density).
    * ``mean_span_char_length`` is the mean characters-per-span; 0.0
      when there are no spans. High values suggest continuous prose;
      low values suggest fragmented labels / small captions.
    * ``has_ocr_pixmap`` is True when the parser flagged the page for
      full-page rasterization because the text layer fell below the
      OCR threshold — i.e. the page is effectively image-only from
      the parser's point of view.
    * ``image_count`` is the number of embedded raster images on the
      page.
    * ``image_area_ratio`` is the sum of embedded-image bbox areas
      divided by page area, clamped to ``[0.0, 1.0]``. Resolution-
      invariant.
    * ``table_count`` is the number of *accepted* structured tables
      (post the parser's quality gate).
    * ``rejected_table_candidate_count`` is the number of table-shaped
      regions the parser's quality gate *rejected*. Under-utilized
      signal for calibration: these are regions Tesseract will lose
      entirely, so a high count on OCR-required pages is a candidate
      driver for UOC. The complexity evaluator must cap this field's
      contribution empirically; not every rejected candidate warrants
      UOC.
    * ``column_count`` is the parser's estimate of column count for the
      page (1 for single-column, 2 for two-column, etc.). The bridge
      derives this from the same column-boundary detector already used
      elsewhere in the parser.
    * ``math_bbox_count`` is the number of undecodable-font-span
      bounding boxes the parser recorded (math candidate regions).
    * ``figure_caption_hit_count`` is the number of parser spans whose
      text matches the "Figure N" / "Fig. N" / "FIG. N" caption
      pattern. Rough proxy for figure density on pages that already
      have text (a scan with no text layer contributes zero here).
    """

    page_index: int
    page_width: float
    page_height: float
    page_char_count: int
    span_count: int
    mean_span_char_length: float
    has_ocr_pixmap: bool
    image_count: int
    image_area_ratio: float
    table_count: int
    rejected_table_candidate_count: int
    column_count: int
    math_bbox_count: int
    figure_caption_hit_count: int


@dataclass(frozen=True)
class LayoutDocumentFeatures:
    """Document-level container of per-page layout features.

    Aggregate statistics deliberately live on the evaluator, not here.
    This class carries only the raw per-page features and a small
    envelope so calibration and later production code can share the
    exact same neutral surface.
    """

    pages: tuple[LayoutPageFeatures, ...] = field(default_factory=tuple)
    extractor_version: str = LAYOUT_FEATURE_EXTRACTOR_VERSION

    @property
    def total_pages(self) -> int:
        return len(self.pages)
