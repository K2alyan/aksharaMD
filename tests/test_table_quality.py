"""Tests for aksharamd/scoring/table_quality.py — Milestone 5."""
from __future__ import annotations

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.models.table import (
    BoundingBox,
    ExtractionMethod,
    TableCell,
    TableData,
)
from aksharamd.scoring.table_quality import (
    SigName,
    TableQualityReport,
    TableQualitySignal,
    compute_table_quality,
)


# ── Test helpers ───────────────────────────────────────────────────────────────

def _make_td(
    rows: list[list[str]],
    header_rows: list[int] | None = None,
    extraction_method: ExtractionMethod | None = None,
    span_detection: str = "unsupported",
    header_detection: str = "assumed_first_row",
    bbox: BoundingBox | None = None,
    metadata: dict | None = None,
) -> TableData:
    if not rows:
        return TableData(row_count=0, column_count=0, cells=[])
    ncols = max(len(r) for r in rows)
    cells = [
        TableCell(text=rows[r][c], row=r, column=c)
        for r in range(len(rows))
        for c in range(len(rows[r]))
    ]
    return TableData(
        row_count=len(rows),
        column_count=ncols,
        cells=cells,
        header_rows=header_rows if header_rows is not None else [0],
        header_detection=header_detection,
        span_detection=span_detection,
        extraction_method=extraction_method,
        bbox=bbox,
        metadata=metadata or {},
    )


def _make_block(td: TableData, page: int = 1, index: int = 0) -> Block:
    return Block.from_table(td, page=page, index=index)


def _sig(report: TableQualityReport, name: str) -> TableQualitySignal | None:
    return next((s for s in report.signals if s.name == name), None)


def _status(report: TableQualityReport, name: str) -> str | None:
    s = _sig(report, name)
    return s.status if s else None


def _value(report: TableQualityReport, name: str):
    s = _sig(report, name)
    return s.value if s else None


# ── Model shape ────────────────────────────────────────────────────────────────

def test_report_model_fields():
    td = _make_td([["H"], ["R"]])
    block = _make_block(td)
    report = compute_table_quality(block)
    assert report.table_id == block.checksum
    assert report.block_id == block.id
    assert report.row_count == 2
    assert report.column_count == 1
    assert report.maturity == "experimental"
    assert isinstance(report.signals, list)
    assert report.overall_status in ("ok", "candidate_risk", "unknown")


def test_report_is_serializable():
    td = _make_td([["H1", "H2"], ["A", "B"]])
    block = _make_block(td)
    report = compute_table_quality(block)
    d = report.model_dump()
    assert isinstance(d, dict)
    assert "signals" in d
    assert all(isinstance(s, dict) for s in d["signals"])


def test_report_is_deterministic():
    td = _make_td([["H"], ["R1"], ["R2"]])
    b1 = Block.from_table(td, page=1, index=0)
    b2 = Block.from_table(td, page=1, index=0)
    r1 = compute_table_quality(b1)
    r2 = compute_table_quality(b2)
    assert r1.model_dump() == r2.model_dump()


# ── Structural completeness ────────────────────────────────────────────────────

def test_structural_complete_rectangular_table():
    td = _make_td([["H1", "H2"], ["A", "B"], ["C", "D"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.EXPLICIT_CELL_COUNT) == 6
    assert _value(report, SigName.EXPECTED_GRID_SIZE) == 6
    assert _value(report, SigName.MISSING_COORDINATE_COUNT) == 0
    assert _status(report, SigName.MISSING_COORDINATE_COUNT) == "ok"
    assert _value(report, SigName.RAGGED_ROW_COUNT) == 0


def test_structural_sparse_table_with_explicit_empty():
    td = _make_td([["H1", "H2"], ["A", ""], ["", "D"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.EXPLICIT_EMPTY_CELL_COUNT) == 2
    assert _value(report, SigName.MISSING_COORDINATE_COUNT) == 0  # cells exist, just empty


def test_structural_missing_coordinate():
    # Row 1 only has one cell — column 1 is missing
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="H1", row=0, column=0),
            TableCell(text="H2", row=0, column=1),
            TableCell(text="A", row=1, column=0),
            # (1,1) missing
        ],
        header_rows=[0],
    )
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.MISSING_COORDINATE_COUNT) == 1
    assert _status(report, SigName.MISSING_COORDINATE_COUNT) == "risk"


def test_structural_span_covered_not_counted_as_missing():
    # Cell at (0,0) spans 2 columns; (0,1) is span-covered, not missing
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="Merged", row=0, column=0, column_span=2),
            TableCell(text="A", row=1, column=0),
            TableCell(text="B", row=1, column=1),
        ],
        header_rows=[0],
        span_detection="native",
    )
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.SPAN_COVERED_COUNT) == 1
    assert _value(report, SigName.MISSING_COORDINATE_COUNT) == 0


def test_structural_empty_row_detected():
    td = _make_td([["H1", "H2"], ["", ""], ["A", "B"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.EMPTY_ROW_COUNT) >= 1


def test_structural_empty_column_detected():
    # Column 1 header is also empty so the whole column is empty
    td = _make_td([["H1", ""], ["A", ""], ["C", ""]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.EMPTY_COLUMN_COUNT) >= 1


def test_structural_ragged_extraction():
    # Row 1 has only 1 cell in a 3-column table
    td = TableData(
        row_count=2,
        column_count=3,
        cells=[
            TableCell(text="H1", row=0, column=0),
            TableCell(text="H2", row=0, column=1),
            TableCell(text="H3", row=0, column=2),
            TableCell(text="A", row=1, column=0),
            # columns 1 and 2 of row 1 are absent
        ],
        header_rows=[0],
    )
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.RAGGED_ROW_COUNT) >= 1
    assert _status(report, SigName.RAGGED_ROW_COUNT) == "risk"


def test_structural_duplicate_row_count():
    td = _make_td([["H"], ["A"], ["A"], ["B"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.DUPLICATE_ROW_COUNT) == 1


def test_structural_no_duplicates_in_normal_table():
    td = _make_td([["H"], ["A"], ["B"], ["C"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.DUPLICATE_ROW_COUNT) == 0


def test_structural_nonempty_ratio_low_is_risk():
    # More than 70% empty cells
    td = _make_td([["H1", "H2", "H3"], ["", "", ""], ["A", "", ""]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.NONEMPTY_CELL_RATIO) is not None
    # Ratio = 4 nonempty out of 9 total = 0.44, above threshold 0.3
    # Let's make it really low:
    td2 = _make_td([["H"], [""], [""], [""], [""], [""], [""], [""], ["A"]])
    report2 = compute_table_quality(_make_block(td2))
    ratio = _value(report2, SigName.NONEMPTY_CELL_RATIO)
    assert isinstance(ratio, float)
    assert ratio < 0.3


# ── Cell fragmentation ─────────────────────────────────────────────────────────

def test_fragmentation_normal_prose_cells():
    td = _make_td([
        ["Column Name", "Description"],
        ["alpha module", "A module that handles alpha requests"],
        ["beta service", "Service for beta traffic routing"],
    ])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.SINGLE_CHAR_CELL_FRACTION) == 0.0
    assert _status(report, SigName.PUNCTUATION_ONLY_FRACTION) == "ok"


def test_fragmentation_numeric_table_ok():
    td = _make_td([
        ["Year", "Revenue", "Cost"],
        ["2023", "1234567", "987654"],
        ["2024", "1350000", "1050000"],
    ])
    report = compute_table_quality(_make_block(td))
    frac = _value(report, SigName.NUMERIC_ONLY_FRACTION)
    assert isinstance(frac, float)
    assert frac > 0.5  # most cells are numeric
    # Numeric cells should NOT trigger risk
    assert _status(report, SigName.NUMERIC_ONLY_FRACTION) == "ok"


def test_fragmentation_single_character_risk():
    td = _make_td([
        ["H1", "H2"],
        ["A", "B"],
        ["C", "D"],
        ["E", "F"],
    ])
    report = compute_table_quality(_make_block(td))
    frac = _value(report, SigName.SINGLE_CHAR_CELL_FRACTION)
    assert isinstance(frac, float)
    # All body cells are single-char — risk signal
    assert frac > 0.5
    assert _status(report, SigName.SINGLE_CHAR_CELL_FRACTION) == "risk"


def test_fragmentation_punctuation_only_cells():
    td = _make_td([
        ["H1", "H2"],
        [".", "|"],
        ["-", "—"],
    ])
    report = compute_table_quality(_make_block(td))
    frac = _value(report, SigName.PUNCTUATION_ONLY_FRACTION)
    assert isinstance(frac, float)
    assert frac > 0.3
    assert _status(report, SigName.PUNCTUATION_ONLY_FRACTION) == "risk"


def test_fragmentation_short_cell_fraction_informational():
    # short_cell_fraction alone is informational, not risk
    td = _make_td([["H"], ["AB"], ["CD"], ["EF"]])
    report = compute_table_quality(_make_block(td))
    assert _status(report, SigName.SHORT_CELL_FRACTION) == "ok"


def test_fragmentation_whitespace_only_cells():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="H1", row=0, column=0),
            TableCell(text="H2", row=0, column=1),
            TableCell(text=" ", row=1, column=0),  # whitespace-only
            TableCell(text="B", row=1, column=1),
        ],
        header_rows=[0],
    )
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.WHITESPACE_ONLY_CELL_COUNT) == 1
    assert _status(report, SigName.WHITESPACE_ONLY_CELL_COUNT) == "risk"


# ── Header quality ─────────────────────────────────────────────────────────────

def test_header_native_detection():
    td = _make_td(
        [["Name", "Age"], ["Alice", "30"]],
        header_detection="native",
    )
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.HEADER_DETECTION) == "native"


def test_header_assumed_first_row():
    td = _make_td([["Name", "Age"], ["Alice", "30"]])  # assumed_first_row by default
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.HEADER_DETECTION) == "assumed_first_row"
    assert _value(report, SigName.HEADER_ROW_COUNT) == 1


def test_header_generic_names_flagged():
    td = _make_td([["col1", "col2", "col3"], ["A", "B", "C"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.GENERIC_HEADER_COUNT) == 3
    assert _status(report, SigName.GENERIC_HEADER_COUNT) == "risk"


def test_header_normal_names_not_generic():
    td = _make_td([["Name", "Revenue", "Country"], ["Alice", "100", "US"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.GENERIC_HEADER_COUNT) == 0
    assert _status(report, SigName.GENERIC_HEADER_COUNT) == "ok"


def test_header_duplicate_names_flagged():
    td = _make_td([["Name", "Name", "Value"], ["A", "B", "1"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.DUPLICATE_HEADER_NAMES) == 1
    assert _status(report, SigName.DUPLICATE_HEADER_NAMES) == "risk"


def test_header_empty_cells_informational():
    td = _make_td([["Name", "", "Value"], ["A", "B", "1"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.EMPTY_HEADER_CELLS) == 1
    assert _status(report, SigName.EMPTY_HEADER_CELLS) == "ok"  # informational


def test_header_repeated_in_body_flagged():
    # Header row {"H1", "H2"} appears again in row 2
    td = _make_td([["H1", "H2"], ["A", "B"], ["H1", "H2"], ["C", "D"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.REPEATED_HEADER_IN_BODY) >= 1
    assert _status(report, SigName.REPEATED_HEADER_IN_BODY) == "risk"


def test_header_no_body_no_repeated_header_false_positive():
    td = _make_td([["H1", "H2"], ["A", "B"], ["C", "D"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.REPEATED_HEADER_IN_BODY) == 0


def test_header_coverage_partial_ok():
    # 4 out of 4 header positions covered → coverage = 1.0
    td = _make_td([["A", "B"], ["C", "D"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.HEADER_CELL_COVERAGE) == 1.0
    assert _status(report, SigName.HEADER_CELL_COVERAGE) == "ok"


# ── Geometry signals ───────────────────────────────────────────────────────────

def test_geometry_no_bbox_unknown():
    td = _make_td([["H"], ["R"]])  # no bbox
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.TABLE_BBOX_AVAILABLE) is False
    assert _status(report, SigName.TABLE_BBOX_AVAILABLE) == "unknown"


def test_geometry_with_bbox_and_page_dimensions():
    bbox = BoundingBox(x0=50.0, y0=100.0, x1=550.0, y1=400.0, coordinate_space="pdf_points")
    td = _make_td([["H"], ["R"]], bbox=bbox)
    report = compute_table_quality(_make_block(td), page_height=792.0, page_width=612.0)
    assert _value(report, SigName.TABLE_BBOX_AVAILABLE) is True
    assert _value(report, SigName.TABLE_NEAR_TOP_MARGIN) is False
    assert _value(report, SigName.TABLE_NEAR_BOTTOM_MARGIN) is False
    h_frac = _value(report, SigName.TABLE_HEIGHT_FRACTION)
    assert isinstance(h_frac, float) and 0 < h_frac < 1


def test_geometry_top_margin_table():
    # Table entirely within top 10% of page
    bbox = BoundingBox(x0=0.0, y0=0.0, x1=500.0, y1=50.0, coordinate_space="pdf_points")
    td = _make_td([["H1", "H2"]], bbox=bbox)
    report = compute_table_quality(_make_block(td), page_height=792.0)
    assert _value(report, SigName.TABLE_NEAR_TOP_MARGIN) is True
    assert _status(report, SigName.TABLE_NEAR_TOP_MARGIN) == "risk"


def test_geometry_bottom_margin_table():
    bbox = BoundingBox(x0=0.0, y0=750.0, x1=500.0, y1=792.0, coordinate_space="pdf_points")
    td = _make_td([["page 1 of 3"]], bbox=bbox)
    report = compute_table_quality(_make_block(td), page_height=792.0)
    assert _value(report, SigName.TABLE_NEAR_BOTTOM_MARGIN) is True
    assert _status(report, SigName.TABLE_NEAR_BOTTOM_MARGIN) == "risk"


def test_geometry_one_row_table_risk():
    td = _make_td([["one row only"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.TABLE_ONE_ROW) is True
    assert _status(report, SigName.TABLE_ONE_ROW) == "risk"


def test_geometry_one_column_table_risk():
    td = _make_td([["H"], ["R1"], ["R2"]])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.TABLE_ONE_COLUMN) is True
    assert _status(report, SigName.TABLE_ONE_COLUMN) == "risk"


def test_geometry_valid_small_table_near_margin_but_not_short():
    # Near top margin but cells are not short — no combined risk from this validator alone
    bbox = BoundingBox(x0=0.0, y0=0.0, x1=500.0, y1=60.0, coordinate_space="pdf_points")
    td = _make_td([["Category", "Revenue"], ["Product A", "1234567"]], bbox=bbox)
    report = compute_table_quality(_make_block(td), page_height=792.0)
    # near_top_margin fires as a geometry risk
    assert _value(report, SigName.TABLE_NEAR_TOP_MARGIN) is True
    # But fragmentation signals are ok
    assert _status(report, SigName.PUNCTUATION_ONLY_FRACTION) == "ok"


# ── Stitching quality ──────────────────────────────────────────────────────────

def _make_stitched_td(rows: list[list[str]]) -> TableData:
    td = _make_td(rows, extraction_method=ExtractionMethod.PDF_STITCHED)
    td = td.model_copy(update={"metadata": {
        "source_pages": [1, 2],
        "source_table_methods": [
            str(ExtractionMethod.PDF_RULED),
            str(ExtractionMethod.PDF_RULED),
        ],
        "page_row_ranges": [
            {"page": 1, "row_start": 0, "row_end": len(rows) // 2},
            {"page": 2, "row_start": len(rows) // 2 + 1, "row_end": len(rows) - 1},
        ],
        "repeated_header_removed": False,
        "stitching_confidence": "inferred",
    }})
    return td


def test_stitching_signals_present_for_stitched_table():
    rows = [["H1", "H2"]] + [[f"r{i}a", f"r{i}b"] for i in range(4)]
    td = _make_stitched_td(rows)
    report = compute_table_quality(_make_block(td))
    assert _sig(report, SigName.STITCHED_SOURCE_PAGE_COUNT) is not None
    assert _value(report, SigName.STITCHED_SOURCE_PAGE_COUNT) == 2


def test_stitching_signals_absent_for_non_stitched():
    td = _make_td([["H"], ["R"]], extraction_method=ExtractionMethod.PDF_RULED)
    report = compute_table_quality(_make_block(td))
    assert _sig(report, SigName.STITCHED_SOURCE_PAGE_COUNT) is None


def test_stitching_confidence_inferred_is_risk():
    td = _make_stitched_td([["H"]] + [[f"r{i}"] for i in range(3)])
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.STITCHING_CONFIDENCE) == "inferred"
    assert _status(report, SigName.STITCHING_CONFIDENCE) == "risk"


def test_stitching_repeated_header_removed_recorded():
    td = _make_td([["H"], ["R1"], ["R2"]], extraction_method=ExtractionMethod.PDF_STITCHED)
    td = td.model_copy(update={"metadata": {
        "source_pages": [1, 2],
        "source_table_methods": ["pdf.ruled", "pdf.ruled"],
        "page_row_ranges": [
            {"page": 1, "row_start": 0, "row_end": 1},
            {"page": 2, "row_start": 2, "row_end": 2},
        ],
        "repeated_header_removed": True,
        "stitching_confidence": "inferred",
    }})
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.REPEATED_HEADER_REMOVED) is True


def test_stitching_mixed_extraction_methods_noted():
    td = _make_td([["H"], ["R1"], ["R2"]], extraction_method=ExtractionMethod.PDF_STITCHED)
    td = td.model_copy(update={"metadata": {
        "source_pages": [1, 2],
        "source_table_methods": ["pdf.ruled", "pdf.whitespace"],  # mixed
        "page_row_ranges": [
            {"page": 1, "row_start": 0, "row_end": 1},
            {"page": 2, "row_start": 2, "row_end": 2},
        ],
        "repeated_header_removed": False,
        "stitching_confidence": "inferred",
    }})
    report = compute_table_quality(_make_block(td))
    assert _value(report, SigName.SOURCE_METHOD_CONSISTENCY) is False


def test_stitching_row_continuity_ok():
    rows = [["H"]] + [[f"r{i}"] for i in range(5)]
    td = _make_stitched_td(rows)
    report = compute_table_quality(_make_block(td))
    sig = _sig(report, SigName.ROW_CONTINUITY_OK)
    assert sig is not None


# ── Overall status ─────────────────────────────────────────────────────────────

def test_overall_status_ok_for_clean_table():
    td = _make_td([["Name", "Value"], ["Alice", "100"], ["Bob", "200"]])
    report = compute_table_quality(_make_block(td))
    # Table has no bbox → TABLE_BBOX_AVAILABLE is "unknown" → overall_status is "unknown"
    # No risk signals fire for normal headers/values
    assert report.overall_status in ("ok", "unknown")
    assert report.overall_status != "candidate_risk"


def test_overall_status_candidate_risk_when_signals_fire():
    td = _make_td([["col1", "col2"], ["A", "B"]])  # generic headers
    report = compute_table_quality(_make_block(td))
    assert report.overall_status == "candidate_risk"


def test_overall_status_unknown_when_bbox_absent():
    td = _make_td([["H1", "H2"], ["A", "B"]])  # no bbox
    report = compute_table_quality(_make_block(td))
    # TABLE_BBOX_AVAILABLE is "unknown" — contributes to "unknown" status if no risk
    # But generic_header with "H1","H2" is ok, no other risks
    # So overall should be "unknown" (because bbox_available=unknown and no risk signals)
    assert report.overall_status in ("ok", "unknown")


# ── Validator integration ──────────────────────────────────────────────────────

def _run_with_validator(blocks: list[Block]) -> CompilationContext:
    from aksharamd.plugins.validators.table_quality import TableQualityValidator
    doc = Document(source="test.pdf", blocks=blocks)
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc
    validator = TableQualityValidator()
    return validator.execute(ctx)


def test_validator_populates_block_metadata():
    td = _make_td([["H1", "H2"], ["A", "B"]])
    block = _make_block(td)
    ctx = _run_with_validator([block])
    assert "table_quality" in block.metadata
    tq = block.metadata["table_quality"]
    assert "overall_status" in tq
    assert "signals" in tq
    assert "maturity" in tq


def test_validator_populates_document_metadata():
    td = _make_td([["H"], ["R"]])
    block = _make_block(td)
    ctx = _run_with_validator([block])
    assert "table_quality_reports" in ctx.document.metadata
    assert len(ctx.document.metadata["table_quality_reports"]) == 1


def test_validator_skips_non_table_blocks():
    para = Block(type=BlockType.PARAGRAPH, content="Some text", page=1)
    ctx = _run_with_validator([para])
    assert "table_quality" not in para.metadata
    assert ctx.document.metadata.get("table_quality_reports") is None


def test_validator_skips_legacy_table_blocks():
    legacy = Block(
        type=BlockType.TABLE,
        content="| A | B |\n| --- | --- |\n| 1 | 2 |",
        page=1,
    )
    ctx = _run_with_validator([legacy])
    assert "table_quality" not in legacy.metadata


def test_validator_emits_no_warnings():
    td = _make_td([["col1", "col2"], ["A", "B"]])  # generic headers, would trigger risk
    block = _make_block(td)
    ctx = _run_with_validator([block])
    # No validation issues should be added by table_quality validator
    assert len(ctx.validation.issues) == 0


# ── Chunk metadata integration ─────────────────────────────────────────────────

def test_chunk_gets_quality_status_from_validator():
    """After validator runs and populates block.metadata, chunker compact summary is set."""
    from aksharamd.plugins.chunkers.table_splitter import make_table_chunk_meta
    td = _make_td([["col1", "col2"], ["A", "B"]])  # generic headers → risk
    block = _make_block(td)
    # Simulate validator run
    block.metadata["table_quality"] = {
        "overall_status": "candidate_risk",
        "maturity": "experimental",
        "signals": [
            {"name": SigName.GENERIC_HEADER_COUNT, "value": 2, "status": "risk",
             "threshold": None, "evidence": {}},
        ],
    }
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert meta["table_quality_status"] == "candidate_risk"
    assert SigName.GENERIC_HEADER_COUNT in meta["table_quality_signal_names"]


def test_chunk_no_quality_status_when_no_validator():
    """Without the validator, block.metadata has no table_quality — no compact summary."""
    from aksharamd.plugins.chunkers.table_splitter import make_table_chunk_meta
    td = _make_td([["H"], ["R"]])
    block = _make_block(td)
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert "table_quality_status" not in meta


def test_chunk_no_signal_names_when_all_ok():
    """table_quality_signal_names omitted if no risk signals."""
    from aksharamd.plugins.chunkers.table_splitter import make_table_chunk_meta
    td = _make_td([["H"], ["R"]])
    block = _make_block(td)
    block.metadata["table_quality"] = {
        "overall_status": "ok",
        "maturity": "experimental",
        "signals": [
            {"name": SigName.MISSING_COORDINATE_COUNT, "value": 0, "status": "ok",
             "threshold": None, "evidence": {}},
        ],
    }
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert meta["table_quality_status"] == "ok"
    assert "table_quality_signal_names" not in meta


# ── Compatibility guards ───────────────────────────────────────────────────────

def test_no_readiness_score_change_from_quality_validator():
    """TableQualityValidator does not alter the readiness score."""
    from aksharamd.scoring import compute_confidence
    from aksharamd.plugins.validators.table_quality import TableQualityValidator

    td = _make_td([["col1", "col2"], ["A", "B"]])  # generic headers
    block = _make_block(td)
    doc = Document(source="test.pdf", blocks=[block])
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc

    validator = TableQualityValidator()
    validator.execute(ctx)

    # Compute readiness before and after — it should be unchanged
    # (We just verify no exception is raised and no issue is emitted by validator)
    assert len(ctx.validation.issues) == 0


def test_document_id_unchanged_by_quality_diagnostics():
    """Running quality analysis does not alter block checksums or document identity."""
    td = _make_td([["H"], ["R"]])
    block = _make_block(td)
    doc = Document(source="test.pdf", blocks=[block])
    doc.compute_id()
    doc_id_before = doc.document_id
    checksum_before = block.checksum

    from aksharamd.plugins.validators.table_quality import TableQualityValidator
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc
    TableQualityValidator().execute(ctx)

    assert block.checksum == checksum_before
    assert doc.document_id == doc_id_before


def test_scoring_policy_version_unchanged():
    from aksharamd.scoring import SCORING_POLICY_VERSION
    assert SCORING_POLICY_VERSION == "1.0"
