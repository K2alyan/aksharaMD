"""Layout-complexity feature model + PDF-parser bridge tests.

Commit 1 of the layout-complexity milestone covers only the neutral
value types and the parser bridge that turns ``RawPage`` into
:class:`LayoutPageFeatures`. There is no scoring or policy yet —
those land in later commits — so nothing here asserts a threshold
or a routing decision. The tests instead pin:

* the feature module has no import path back into the parser;
* the bridge produces the documented primitive shapes for the
  signals we already have parser support for (chars, spans, images,
  tables, rejected candidates, columns, math bboxes, figure caption
  hits, image area ratio, ocr_pixmap flag);
* extraction time stays negligible on a synthetic 100-page fixture
  (upper-bounds the "must not materially slow ordinary PDFs"
  constraint from the milestone spec).
"""
from __future__ import annotations

import time

from aksharamd.plugins.parsers.layout_complexity import (
    LAYOUT_FEATURE_EXTRACTOR_VERSION,
    LayoutDocumentFeatures,
    LayoutPageFeatures,
)
from aksharamd.plugins.parsers.pdf import (
    RawPage,
    extract_layout_document_features,
)

# ── Value-type contract ──────────────────────────────────────────────


def test_layout_page_features_carries_all_documented_fields() -> None:
    """The feature dataclass must expose exactly the fields the
    evaluator (Commit 2) and the calibration harness (Commit 3) will
    depend on. Adding a field later is backward-compatible; removing
    or renaming one is a breaking change to the neutral surface, so
    this test is a light schema pin."""
    features = LayoutPageFeatures(
        page_index=0,
        page_width=612.0,
        page_height=792.0,
        page_char_count=0,
        span_count=0,
        mean_span_char_length=0.0,
        has_ocr_pixmap=False,
        image_count=0,
        image_area_ratio=0.0,
        table_count=0,
        rejected_table_candidate_count=0,
        column_count=1,
        math_bbox_count=0,
        figure_caption_hit_count=0,
    )
    # If any field disappears the ``LayoutPageFeatures(...)`` call
    # above breaks first — no further assertions needed.
    assert features.page_index == 0


def test_layout_document_features_carries_extractor_version() -> None:
    doc = LayoutDocumentFeatures()
    assert doc.extractor_version == LAYOUT_FEATURE_EXTRACTOR_VERSION
    assert doc.total_pages == 0
    assert doc.pages == ()


# ── Import-boundary regression pin ───────────────────────────────────


def test_layout_complexity_module_does_not_import_pdf_parser() -> None:
    """The neutral feature module must NOT import
    :mod:`aksharamd.plugins.parsers.pdf` or any parser-private helper
    from it. Enforced by AST-parsing the module source rather than
    checking :data:`sys.modules` — the ``parsers`` package's
    ``__init__.py`` pre-loads every sibling parser at package-import
    time, so a runtime check would give a false positive for any
    module inside the ``parsers`` package.
    """
    import ast
    from pathlib import Path

    import aksharamd.plugins.parsers.layout_complexity as m

    source = Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith(".pdf") or module == "pdf":
                offending.append(
                    f"from {module} import ..."
                )
            elif module.startswith("aksharamd.plugins.parsers.pdf"):
                offending.append(f"from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(".pdf") or alias.name == (
                    "aksharamd.plugins.parsers.pdf"
                ):
                    offending.append(f"import {alias.name}")

    assert not offending, (
        "layout_complexity must not import parsers.pdf; "
        f"found: {offending}"
    )


# ── Bridge helpers ────────────────────────────────────────────────────


def _make_span(x: float, y: float, text: str, size: float = 11.0) -> dict:
    return {"x": x, "y": y, "text": text, "size": size}


def _make_image(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"bbox": (x0, y0, x1, y1)}


def _make_page(
    *,
    page_num: int = 1,
    width: float = 612.0,
    height: float = 792.0,
    spans: list[dict] | None = None,
    tables: list[dict] | None = None,
    images: list[dict] | None = None,
    ocr_pixmap: bytes | None = None,
    math_bboxes=None,
    rejected_candidates=None,
) -> RawPage:
    return RawPage(
        page_num=page_num,
        spans=spans or [],
        tables=tables or [],
        images=images or [],
        height=height,
        width=width,
        ocr_pixmap=ocr_pixmap,
        embedded_image_bytes=[],
        content_images=[],
        math_bboxes=math_bboxes or [],
        rejected_candidates=rejected_candidates or [],
    )


# ── Bridge behaviour ────────────────────────────────────────────────


def test_bridge_extracts_page_char_count_and_span_stats() -> None:
    spans = [
        _make_span(72, 100, "The quick brown fox"),
        _make_span(72, 116, "jumps over the lazy dog"),
    ]
    doc = extract_layout_document_features([_make_page(spans=spans)])
    (page,) = doc.pages
    assert page.span_count == 2
    assert page.page_char_count == len("The quick brown fox") + len(
        "jumps over the lazy dog"
    )
    # mean_span_char_length is the mean OF character lengths of each
    # span's ``text`` string (not the "stripped" chars used by
    # page_char_count) — the two differ only when spans have
    # surrounding whitespace. The important property is that empty
    # spans do not divide by zero.
    assert page.mean_span_char_length > 0.0


def test_bridge_reports_zero_span_stats_on_empty_page() -> None:
    doc = extract_layout_document_features([_make_page()])
    (page,) = doc.pages
    assert page.span_count == 0
    assert page.page_char_count == 0
    assert page.mean_span_char_length == 0.0
    # An empty page defaults to single-column.
    assert page.column_count == 1


def test_bridge_flags_pages_with_ocr_pixmap() -> None:
    doc = extract_layout_document_features(
        [
            _make_page(page_num=1, ocr_pixmap=None),
            _make_page(page_num=2, ocr_pixmap=b"\x89PNG..."),
        ]
    )
    assert [p.has_ocr_pixmap for p in doc.pages] == [False, True]


def test_bridge_counts_images_and_computes_bounded_area_ratio() -> None:
    page = _make_page(
        width=100.0,
        height=100.0,
        images=[
            _make_image(0, 0, 50, 50),  # 25% of page
            _make_image(50, 50, 100, 100),  # +25% of page
        ],
    )
    doc = extract_layout_document_features([page])
    (features,) = doc.pages
    assert features.image_count == 2
    # Two 50x50 images on a 100x100 page = 50% coverage.
    assert features.image_area_ratio == 0.5


def test_bridge_clamps_image_area_ratio_when_bboxes_overflow_page() -> None:
    """An image bbox that reports area beyond the page must still
    yield a ratio in [0, 1]. Real PDFs occasionally record image
    bboxes that overlap page borders slightly; the ratio must not
    exceed 1.0 or cause downstream evaluators to trip on
    out-of-range floats."""
    page = _make_page(
        width=100.0,
        height=100.0,
        images=[_make_image(0, 0, 200, 200)],  # 4x page area
    )
    doc = extract_layout_document_features([page])
    (features,) = doc.pages
    assert features.image_area_ratio == 1.0


def test_bridge_counts_tables_and_rejected_candidates() -> None:
    page = _make_page(
        tables=[{"cells": [["a", "b"], ["c", "d"]]}],
        rejected_candidates=[
            {"reason": "no_ruling_lines"},
            {"reason": "single_row"},
            {"reason": "too_sparse"},
        ],
    )
    doc = extract_layout_document_features([page])
    (features,) = doc.pages
    assert features.table_count == 1
    assert features.rejected_table_candidate_count == 3


def test_bridge_derives_column_count_from_span_geometry() -> None:
    """Two clearly-separated x clusters should yield column_count >= 2.

    The parser's boundary detector groups spans within 3pt of the same
    y into a single "line" and records the leftmost x per line — so
    left and right columns need distinct y-values, not overlapping
    rows, for both x-clusters to appear in the line-start set.
    """
    spans = []
    # Left column at y=100, 130, 160, ...  (72 pt from the left margin)
    for i in range(15):
        spans.append(_make_span(72.0, 100.0 + 30 * i, "left column text"))
    # Right column offset in y so it does not share rows with the left
    # column: y=115, 145, 175, ...  (340 pt from the left margin — well
    # past the mid-page x mark for a 612-pt-wide page)
    for i in range(15):
        spans.append(_make_span(340.0, 115.0 + 30 * i, "right column text"))
    doc = extract_layout_document_features(
        [_make_page(width=612.0, spans=spans)]
    )
    (features,) = doc.pages
    assert features.column_count >= 2


def test_bridge_defaults_column_count_to_one_when_no_boundaries_found() -> None:
    spans = [_make_span(72.0, 100.0 + 12 * i, "single column") for i in range(20)]
    doc = extract_layout_document_features(
        [_make_page(width=612.0, spans=spans)]
    )
    (features,) = doc.pages
    assert features.column_count == 1


def test_bridge_records_math_bbox_count() -> None:
    page = _make_page(math_bboxes=[(0, 0, 10, 10), (20, 20, 30, 30)])
    doc = extract_layout_document_features([page])
    (features,) = doc.pages
    assert features.math_bbox_count == 2


def test_bridge_counts_figure_caption_hits() -> None:
    spans = [
        _make_span(72, 100, "Figure 1: schematic overview"),
        _make_span(72, 400, "As shown in Fig. 3, results improve."),
        _make_span(72, 600, "FIGURE 2 — throughput vs latency"),
        _make_span(72, 700, "This paragraph discusses figure it out."),
    ]
    doc = extract_layout_document_features([_make_page(spans=spans)])
    (features,) = doc.pages
    # First three should match; last should not (no trailing digit).
    assert features.figure_caption_hit_count == 3


def test_bridge_preserves_zero_based_page_indexing() -> None:
    doc = extract_layout_document_features(
        [
            _make_page(page_num=1),
            _make_page(page_num=2),
            _make_page(page_num=7),
        ]
    )
    # page_num on RawPage is 1-based; LayoutPageFeatures uses 0-based.
    assert [p.page_index for p in doc.pages] == [0, 1, 6]


# ── Runtime guard ────────────────────────────────────────────────────


def test_extraction_runtime_under_50ms_on_100_page_synthetic() -> None:
    """The complexity detector must not materially slow ordinary PDFs.
    Extracting features from a synthetic 100-page fixture with modest
    spans, images, and table candidates must complete in under 50 ms
    on any reasonable machine — well under the tens-of-seconds
    Tesseract runtime we would otherwise incur."""
    spans = [_make_span(72, 100 + 12 * i, "sample text") for i in range(30)]
    images = [_make_image(100, 100, 200, 200)]
    pages = [
        _make_page(page_num=i + 1, spans=spans, images=images)
        for i in range(100)
    ]

    start = time.perf_counter()
    doc = extract_layout_document_features(pages)
    elapsed = time.perf_counter() - start

    assert doc.total_pages == 100
    assert elapsed < 0.05, (
        f"extraction on 100 synthetic pages took {elapsed*1000:.1f} ms; "
        "the complexity detector must not materially slow ordinary "
        "PDFs during the classification phase"
    )
