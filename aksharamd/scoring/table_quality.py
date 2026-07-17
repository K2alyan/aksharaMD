"""Structured table-quality diagnostics.

All findings start as maturity="experimental" with penalty=0.
No readiness-score changes are made during Milestone 5.
"""
from __future__ import annotations

import re
import statistics

from pydantic import BaseModel, Field

from ..models.block import Block
from ..models.table import ExtractionMethod, TableData

# ── Data models ────────────────────────────────────────────────────────────────

class TableQualitySignal(BaseModel):
    """A single measured table-quality signal."""
    name: str
    value: float | int | str | bool | None
    threshold: float | int | None = None
    status: str  # "ok" | "risk" | "unknown"
    evidence: dict = Field(default_factory=dict)


class TableQualityReport(BaseModel):
    """Quality analysis for one structured table block.

    Deterministic, serializable, and independent of readiness penalties.
    The overall_status field summarizes whether any risk signals fired.
    All new signals start with maturity="experimental".
    """
    table_id: str            # block.checksum — content-derived
    block_id: str            # block.id
    row_count: int
    column_count: int
    signals: list[TableQualitySignal] = Field(default_factory=list)
    overall_status: str      # "ok" | "candidate_risk" | "unknown"
    extraction_method: str | None = None
    maturity: str = "experimental"


# ── Signal name constants ──────────────────────────────────────────────────────

class SigName:
    # Structural completeness
    EXPLICIT_CELL_COUNT      = "explicit_cell_count"
    EXPECTED_GRID_SIZE       = "expected_grid_size"
    EXPLICIT_EMPTY_CELL_COUNT = "explicit_empty_cell_count"
    MISSING_COORDINATE_COUNT = "missing_coordinate_count"
    SPAN_COVERED_COUNT       = "span_covered_coordinate_count"
    NONEMPTY_CELL_RATIO      = "nonempty_cell_ratio"
    EMPTY_ROW_COUNT          = "empty_row_count"
    EMPTY_COLUMN_COUNT       = "empty_column_count"
    RAGGED_ROW_COUNT         = "ragged_row_count"
    DUPLICATE_ROW_COUNT      = "duplicate_row_count"
    # Cell fragmentation
    AVG_NONEMPTY_CELL_LENGTH    = "avg_nonempty_cell_length"
    MEDIAN_CELL_LENGTH          = "median_cell_length"
    SINGLE_CHAR_CELL_FRACTION   = "single_char_cell_fraction"
    PUNCTUATION_ONLY_FRACTION   = "punctuation_only_cell_fraction"
    NUMERIC_ONLY_FRACTION       = "numeric_only_cell_fraction"
    SHORT_CELL_FRACTION         = "short_cell_fraction"
    WHITESPACE_ONLY_CELL_COUNT  = "whitespace_only_cell_count"
    # Header quality
    HEADER_DETECTION            = "header_detection"
    HEADER_ROW_COUNT            = "header_row_count"
    HEADER_CELL_COVERAGE        = "header_cell_coverage"
    GENERIC_HEADER_COUNT        = "generic_header_count"
    DUPLICATE_HEADER_NAMES      = "duplicate_header_names"
    EMPTY_HEADER_CELLS          = "empty_header_cells"
    NUMERIC_ONLY_HEADERS        = "numeric_only_headers"
    HEADER_BODY_WIDTH_MISMATCH  = "header_body_width_mismatch"
    REPEATED_HEADER_IN_BODY     = "repeated_header_in_body"
    # Geometry
    TABLE_BBOX_AVAILABLE        = "table_bbox_available"
    TABLE_NEAR_TOP_MARGIN       = "table_near_top_margin"
    TABLE_NEAR_BOTTOM_MARGIN    = "table_near_bottom_margin"
    TABLE_ONE_ROW               = "table_one_row"
    TABLE_ONE_COLUMN            = "table_one_column"
    TABLE_HEIGHT_FRACTION       = "table_height_fraction"
    TABLE_WIDTH_FRACTION        = "table_width_fraction"
    # Stitching
    STITCHED_SOURCE_PAGE_COUNT  = "stitched_source_page_count"
    REPEATED_HEADER_REMOVED     = "repeated_header_removed"
    STITCHING_CONFIDENCE        = "stitching_confidence"
    SOURCE_METHOD_CONSISTENCY   = "source_method_consistency"
    PAGE_ROW_RANGES_AVAILABLE   = "page_row_ranges_available"
    ROW_CONTINUITY_OK           = "stitching_row_continuity"


_GENERIC_HEADER_RE = re.compile(
    r"^(col(?:umn)?[_\s]*\d+|field[_\s]*\d+|header[_\s]*\d+|f\d+)$",
    re.IGNORECASE,
)
_PUNCT_ONLY_RE = re.compile(r"^[^\w\s]+$")
_NUMERIC_CELL_RE = re.compile(r"^[-+]?\d[\d,.%\s]*$")
_SHORT_CELL_THRESHOLD = 3


# ── Internal helpers ───────────────────────────────────────────────────────────

def _span_covered_positions(td: TableData) -> set[tuple[int, int]]:
    """Positions covered by a span from another cell (not the anchor cell itself)."""
    covered: set[tuple[int, int]] = set()
    for cell in td.cells:
        for dr in range(cell.row_span):
            for dc in range(cell.column_span):
                if dr > 0 or dc > 0:
                    covered.add((cell.row + dr, cell.column + dc))
    return covered


def _row_texts(td: TableData) -> dict[int, list[str]]:
    by_row: dict[int, list[str]] = {}
    for cell in td.cells:
        by_row.setdefault(cell.row, []).append(cell.text)
    return by_row


def _col_texts(td: TableData) -> dict[int, list[str]]:
    by_col: dict[int, list[str]] = {}
    for cell in td.cells:
        by_col.setdefault(cell.column, []).append(cell.text)
    return by_col


# ── Structural completeness ────────────────────────────────────────────────────

def _structural_signals(td: TableData) -> list[TableQualitySignal]:
    total_positions = td.row_count * td.column_count
    cell_positions = {(c.row, c.column) for c in td.cells}
    span_covered = _span_covered_positions(td)
    all_positions = {(r, c) for r in range(td.row_count) for c in range(td.column_count)}

    explicit_count = len(td.cells)
    empty_explicit = sum(1 for c in td.cells if not c.text.strip())
    missing_count = len(all_positions - cell_positions - span_covered)
    span_count = len(span_covered)

    nonempty = sum(1 for c in td.cells if c.text.strip())
    nonempty_ratio = nonempty / explicit_count if explicit_count > 0 else 0.0

    row_txt = _row_texts(td)
    empty_row_count = sum(
        1 for r in range(td.row_count)
        if not any(t.strip() for t in row_txt.get(r, []))
    )

    col_txt = _col_texts(td)
    empty_col_count = sum(
        1 for c in range(td.column_count)
        if not any(t.strip() for t in col_txt.get(c, []))
    )

    # Ragged row: fewer explicit cells than column_count, not explained by spans
    ragged_count = 0
    for r in range(td.row_count):
        r_cells = sum(1 for pos in cell_positions if pos[0] == r)
        r_covered = sum(1 for pos in span_covered if pos[0] == r)
        if r_cells + r_covered < td.column_count:
            ragged_count += 1

    # Duplicate rows (excluding header rows)
    header_set = set(td.header_rows)
    row_sigs: dict[tuple[str, ...], int] = {}
    for r in range(td.row_count):
        if r in header_set:
            continue
        sig = tuple(
            next((c.text.strip() for c in td.cells if c.row == r and c.column == col), "")
            for col in range(td.column_count)
        )
        row_sigs[sig] = row_sigs.get(sig, 0) + 1
    duplicate_row_count = sum(v - 1 for v in row_sigs.values() if v > 1)

    return [
        TableQualitySignal(name=SigName.EXPLICIT_CELL_COUNT, value=explicit_count, status="ok"),
        TableQualitySignal(name=SigName.EXPECTED_GRID_SIZE, value=total_positions, status="ok"),
        TableQualitySignal(name=SigName.EXPLICIT_EMPTY_CELL_COUNT, value=empty_explicit, status="ok"),
        TableQualitySignal(
            name=SigName.MISSING_COORDINATE_COUNT,
            value=missing_count,
            status="risk" if missing_count > 0 else "ok",
            evidence={"span_detection": str(td.span_detection)},
        ),
        TableQualitySignal(name=SigName.SPAN_COVERED_COUNT, value=span_count, status="ok"),
        TableQualitySignal(
            name=SigName.NONEMPTY_CELL_RATIO,
            value=round(nonempty_ratio, 3),
            threshold=0.3,
            status="risk" if nonempty_ratio < 0.3 and explicit_count > 0 else "ok",
        ),
        TableQualitySignal(
            name=SigName.EMPTY_ROW_COUNT,
            value=empty_row_count,
            status="risk" if empty_row_count > max(1, td.row_count // 2) else "ok",
        ),
        TableQualitySignal(name=SigName.EMPTY_COLUMN_COUNT, value=empty_col_count, status="ok"),
        TableQualitySignal(
            name=SigName.RAGGED_ROW_COUNT,
            value=ragged_count,
            status="risk" if ragged_count > 0 else "ok",
        ),
        TableQualitySignal(name=SigName.DUPLICATE_ROW_COUNT, value=duplicate_row_count, status="ok"),
    ]


# ── Cell fragmentation ─────────────────────────────────────────────────────────

def _fragmentation_signals(td: TableData) -> list[TableQualitySignal]:
    nonempty_cells = [c for c in td.cells if c.text.strip()]
    lengths = [len(c.text.strip()) for c in nonempty_cells]
    nc = len(nonempty_cells)

    avg_len = sum(lengths) / nc if nc > 0 else 0.0
    med_len = float(statistics.median(lengths)) if lengths else 0.0

    single_char = sum(1 for ln in lengths if ln == 1)
    single_char_frac = single_char / nc if nc > 0 else 0.0

    punct_only = sum(1 for c in nonempty_cells if _PUNCT_ONLY_RE.match(c.text.strip()))
    punct_frac = punct_only / nc if nc > 0 else 0.0

    numeric_only = sum(1 for c in nonempty_cells if _NUMERIC_CELL_RE.match(c.text.strip()))
    numeric_frac = numeric_only / nc if nc > 0 else 0.0

    short_count = sum(1 for ln in lengths if ln <= _SHORT_CELL_THRESHOLD)
    short_frac = short_count / nc if nc > 0 else 0.0

    # Whitespace-only: cell text is non-empty but all whitespace
    ws_only = sum(1 for c in td.cells if c.text and not c.text.strip())

    return [
        TableQualitySignal(
            name=SigName.AVG_NONEMPTY_CELL_LENGTH,
            value=round(avg_len, 1),
            status="ok",
        ),
        TableQualitySignal(
            name=SigName.MEDIAN_CELL_LENGTH,
            value=round(med_len, 1),
            status="ok",
        ),
        TableQualitySignal(
            name=SigName.SINGLE_CHAR_CELL_FRACTION,
            value=round(single_char_frac, 3),
            threshold=0.5,
            status="risk" if single_char_frac > 0.5 else "ok",
        ),
        TableQualitySignal(
            name=SigName.PUNCTUATION_ONLY_FRACTION,
            value=round(punct_frac, 3),
            threshold=0.3,
            status="risk" if punct_frac > 0.3 else "ok",
        ),
        TableQualitySignal(
            name=SigName.NUMERIC_ONLY_FRACTION,
            value=round(numeric_frac, 3),
            status="ok",   # high numeric is normal for financial tables
        ),
        TableQualitySignal(
            name=SigName.SHORT_CELL_FRACTION,
            value=round(short_frac, 3),
            threshold=0.5,
            status="ok",   # informational; combined with other signals by consumer
        ),
        TableQualitySignal(
            name=SigName.WHITESPACE_ONLY_CELL_COUNT,
            value=ws_only,
            status="risk" if ws_only > 0 else "ok",
        ),
    ]


# ── Header quality ─────────────────────────────────────────────────────────────

def _header_signals(td: TableData) -> list[TableQualitySignal]:
    header_set = set(td.header_rows)
    header_row_count = len(header_set)
    total_header_positions = header_row_count * td.column_count
    header_cells = [c for c in td.cells if c.row in header_set]

    coverage = len(header_cells) / total_header_positions if total_header_positions > 0 else 0.0

    generic_count = sum(
        1 for c in header_cells if _GENERIC_HEADER_RE.match(c.text.strip())
    )

    header_texts = [c.text.strip() for c in header_cells if c.text.strip()]
    dup_count = len(header_texts) - len(set(header_texts))

    empty_hdrs = sum(1 for c in header_cells if not c.text.strip())
    numeric_hdrs = sum(
        1 for c in header_cells
        if c.text.strip() and _NUMERIC_CELL_RE.match(c.text.strip())
    )

    # Header/body width mismatch
    body_rows_ids = [r for r in range(td.row_count) if r not in header_set]
    max_hdr_cells = max(
        (sum(1 for c in td.cells if c.row == r) for r in header_set),
        default=0,
    )
    max_body_cells = max(
        (sum(1 for c in td.cells if c.row == r) for r in body_rows_ids),
        default=0,
    ) if body_rows_ids else 0
    width_mismatch = (
        bool(body_rows_ids) and max_hdr_cells != max_body_cells
        and max_hdr_cells > 0 and max_body_cells > 0
    )

    # Repeated header signature found in body rows
    hdr_text_set = set(header_texts)
    repeated_in_body = 0
    if hdr_text_set:
        for r in body_rows_ids:
            row_texts = {
                c.text.strip() for c in td.cells
                if c.row == r and c.text.strip()
            }
            if row_texts and row_texts == hdr_text_set:
                repeated_in_body += 1

    return [
        TableQualitySignal(name=SigName.HEADER_DETECTION, value=str(td.header_detection), status="ok"),
        TableQualitySignal(name=SigName.HEADER_ROW_COUNT, value=header_row_count, status="ok"),
        TableQualitySignal(
            name=SigName.HEADER_CELL_COVERAGE,
            value=round(coverage, 3),
            threshold=0.8,
            status="risk" if header_row_count > 0 and coverage < 0.8 else "ok",
        ),
        TableQualitySignal(
            name=SigName.GENERIC_HEADER_COUNT,
            value=generic_count,
            status="risk" if generic_count > 0 else "ok",
        ),
        TableQualitySignal(
            name=SigName.DUPLICATE_HEADER_NAMES,
            value=dup_count,
            status="risk" if dup_count > 0 else "ok",
        ),
        TableQualitySignal(name=SigName.EMPTY_HEADER_CELLS, value=empty_hdrs, status="ok"),
        TableQualitySignal(name=SigName.NUMERIC_ONLY_HEADERS, value=numeric_hdrs, status="ok"),
        TableQualitySignal(
            name=SigName.HEADER_BODY_WIDTH_MISMATCH,
            value=width_mismatch,
            status="risk" if width_mismatch else "ok",
        ),
        TableQualitySignal(
            name=SigName.REPEATED_HEADER_IN_BODY,
            value=repeated_in_body,
            status="risk" if repeated_in_body > 0 else "ok",
        ),
    ]


# ── Geometry ───────────────────────────────────────────────────────────────────

def _geometry_signals(
    td: TableData,
    page_height: float = 0.0,
    page_width: float = 0.0,
) -> list[TableQualitySignal]:
    if td.bbox is None:
        return [
            TableQualitySignal(name=SigName.TABLE_BBOX_AVAILABLE, value=False, status="unknown"),
            TableQualitySignal(name=SigName.TABLE_ONE_ROW, value=td.row_count == 1,
                               status="risk" if td.row_count == 1 else "ok"),
            TableQualitySignal(name=SigName.TABLE_ONE_COLUMN, value=td.column_count == 1,
                               status="risk" if td.column_count == 1 else "ok"),
        ]

    bbox = td.bbox
    signals: list[TableQualitySignal] = [
        TableQualitySignal(name=SigName.TABLE_BBOX_AVAILABLE, value=True, status="ok"),
    ]

    if page_height > 0:
        margin = page_height * 0.10
        near_top = bbox.y0 < margin
        near_bottom = bbox.y1 > (page_height - margin)
        h_frac = (bbox.y1 - bbox.y0) / page_height
        signals += [
            TableQualitySignal(
                name=SigName.TABLE_NEAR_TOP_MARGIN,
                value=near_top,
                status="risk" if near_top else "ok",
                evidence={"y0": round(bbox.y0, 1), "margin_threshold": round(margin, 1)},
            ),
            TableQualitySignal(
                name=SigName.TABLE_NEAR_BOTTOM_MARGIN,
                value=near_bottom,
                status="risk" if near_bottom else "ok",
                evidence={"y1": round(bbox.y1, 1)},
            ),
            TableQualitySignal(name=SigName.TABLE_HEIGHT_FRACTION, value=round(h_frac, 3), status="ok"),
        ]

    if page_width > 0:
        w_frac = (bbox.x1 - bbox.x0) / page_width
        signals.append(
            TableQualitySignal(name=SigName.TABLE_WIDTH_FRACTION, value=round(w_frac, 3), status="ok")
        )

    signals += [
        TableQualitySignal(
            name=SigName.TABLE_ONE_ROW,
            value=td.row_count == 1,
            status="risk" if td.row_count == 1 else "ok",
        ),
        TableQualitySignal(
            name=SigName.TABLE_ONE_COLUMN,
            value=td.column_count == 1,
            status="risk" if td.column_count == 1 else "ok",
        ),
    ]
    return signals


# ── Stitching quality ──────────────────────────────────────────────────────────

def _stitching_signals(td: TableData) -> list[TableQualitySignal]:
    em = td.extraction_method
    if em is None or str(em) != str(ExtractionMethod.PDF_STITCHED):
        return []

    meta = td.metadata or {}
    source_pages: list = meta.get("source_pages", [])
    source_methods: list[str] = meta.get("source_table_methods", [])
    page_row_ranges: list[dict] = meta.get("page_row_ranges", [])
    repeated_removed: bool | None = meta.get("repeated_header_removed")
    stitching_confidence: str = meta.get("stitching_confidence", "unknown")

    # Row continuity: page_row_ranges should cover all rows with no gaps
    row_continuity_ok = True
    if page_row_ranges:
        sorted_ranges = sorted(page_row_ranges, key=lambda e: e["row_start"])
        for i, entry in enumerate(sorted_ranges):
            if i == 0 and entry["row_start"] != 0:
                row_continuity_ok = False
                break
            if i > 0 and entry["row_start"] != sorted_ranges[i - 1]["row_end"] + 1:
                row_continuity_ok = False
                break
        if sorted_ranges and sorted_ranges[-1]["row_end"] != td.row_count - 1:
            row_continuity_ok = False

    method_consistent = len(set(m for m in source_methods if m)) <= 1

    return [
        TableQualitySignal(
            name=SigName.STITCHED_SOURCE_PAGE_COUNT,
            value=len(source_pages),
            status="ok",
        ),
        TableQualitySignal(
            name=SigName.REPEATED_HEADER_REMOVED,
            value=repeated_removed,
            status="ok",
        ),
        TableQualitySignal(
            name=SigName.STITCHING_CONFIDENCE,
            value=stitching_confidence,
            status="risk" if stitching_confidence == "inferred" else "ok",
            evidence={"source_pages": source_pages},
        ),
        TableQualitySignal(
            name=SigName.SOURCE_METHOD_CONSISTENCY,
            value=method_consistent,
            status="ok",
        ),
        TableQualitySignal(
            name=SigName.PAGE_ROW_RANGES_AVAILABLE,
            value=bool(page_row_ranges),
            status="ok",
        ),
        TableQualitySignal(
            name=SigName.ROW_CONTINUITY_OK,
            value=row_continuity_ok,
            status="risk" if not row_continuity_ok else "ok",
        ),
    ]


# ── Overall status ─────────────────────────────────────────────────────────────

def _determine_overall_status(signals: list[TableQualitySignal]) -> str:
    if any(s.status == "risk" for s in signals):
        return "candidate_risk"
    if any(s.status == "unknown" for s in signals):
        return "unknown"
    return "ok"


# ── Public entry point ─────────────────────────────────────────────────────────

def compute_table_quality(
    block: Block,
    page_height: float = 0.0,
    page_width: float = 0.0,
) -> TableQualityReport:
    """Compute all table-quality signals for a structured TABLE block.

    All signals carry maturity="experimental". This function emits no warnings
    and does not affect the readiness score.
    """
    td = block.table_data
    assert td is not None, "compute_table_quality requires block.table_data"

    signals: list[TableQualitySignal] = []
    signals.extend(_structural_signals(td))
    signals.extend(_fragmentation_signals(td))
    signals.extend(_header_signals(td))
    signals.extend(_geometry_signals(td, page_height=page_height, page_width=page_width))
    signals.extend(_stitching_signals(td))

    return TableQualityReport(
        table_id=block.checksum,
        block_id=block.id,
        row_count=td.row_count,
        column_count=td.column_count,
        signals=signals,
        overall_status=_determine_overall_status(signals),
        extraction_method=str(td.extraction_method) if td.extraction_method else None,
        maturity="experimental",
    )
