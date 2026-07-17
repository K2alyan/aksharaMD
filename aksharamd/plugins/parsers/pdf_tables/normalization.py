"""PDF-specific cell-grid to TableData normalization."""
from __future__ import annotations

import re
from typing import Any

from ....models.table import BoundingBox, ExtractionMethod, TableCell, TableData

_CID_RE = re.compile(r"\(cid:\d+\)")
_CELL_FURNITURE_RE = re.compile(
    r"^\d+/\d+/\d{2,4}\s+\d+:\d+\s*(AM|PM)\s+Page\s+\S+$"
    r"|^page\s+\d+(\s+of\s+\d+)?$"
    r"|^\d{4}\s+©",
    re.IGNORECASE,
)

_SOURCE_TO_METHOD: dict[str, ExtractionMethod] = {
    "ruled": ExtractionMethod.PDF_RULED,
    "whitespace": ExtractionMethod.PDF_WHITESPACE,
    "hrule": ExtractionMethod.PDF_BOOKTABS,
}


def normalize_pdf_cell_text(v: str | None) -> str:
    """Normalize PDF cell text for TableCell storage.

    Applies the same transforms as the old _cells_to_markdown inner norm(),
    EXCEPT does NOT escape pipe characters — render_table_markdown handles that.
    """
    text = v or ""
    text = _CID_RE.sub("", text).replace("�", "")
    # Strip Unicode zero-width and formatting characters
    text = re.sub(r"[\xad​‌‍﻿]", "", text)
    # Strip trailing footnote superscripts
    text = re.sub(r"[\xb2\xb3\xb9⁰-⁹]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if _CELL_FURNITURE_RE.match(text) else text


def cell_bbox_from_spans(spans: list[dict]) -> BoundingBox | None:
    """Compute bounding box as union of span bboxes."""
    if not spans:
        return None
    x0 = min(s["bbox"][0] for s in spans)
    y0 = min(s["bbox"][1] for s in spans)
    x1 = max(s["bbox"][2] for s in spans)
    y1 = max(s["bbox"][3] for s in spans)
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1, coordinate_space="pdf_points")


def cells_to_tabledata(
    cells: list[list[Any]],
    *,
    bbox: tuple[float, float, float, float],
    source: str,
    page: int,
    cell_bboxes: list[list[BoundingBox | None]] | None = None,
) -> TableData:
    """Convert a 2D cell grid to canonical TableData.

    Applies the same text normalization and ghost-cell blanking as the old
    _cells_to_markdown function. Rows that are entirely empty after normalization
    are kept (they may represent blank rows in the source document).
    Short rows are padded to the maximum column count.
    """
    if not cells:
        return TableData(row_count=0, column_count=0, cells=[])

    rows = [[normalize_pdf_cell_text(c) for c in row] for row in cells]
    ncols = max((len(r) for r in rows), default=0)
    if ncols == 0:
        return TableData(row_count=0, column_count=0, cells=[])

    # Pad short rows to uniform width
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    # Ghost-cell blanking: in multi-row headers (before first numeric row),
    # cells that repeat the value directly above are merged-cell artefacts.
    first_data = next(
        (i for i, r in enumerate(rows) if any(re.search(r"\d", c) for c in r)),
        len(rows),
    )
    for i in range(1, first_data):
        for j in range(ncols):
            if rows[i][j] and rows[i][j] == rows[i - 1][j]:
                rows[i][j] = ""

    extraction_method = _SOURCE_TO_METHOD.get(source, ExtractionMethod.OTHER)
    tbl_bbox = BoundingBox(
        x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3],
        coordinate_space="pdf_points",
    )

    table_cells: list[TableCell] = []
    for r_idx, row in enumerate(rows):
        for c_idx, text in enumerate(row):
            cb: BoundingBox | None = None
            if (
                cell_bboxes is not None
                and r_idx < len(cell_bboxes)
                and c_idx < len(cell_bboxes[r_idx])
            ):
                cb = cell_bboxes[r_idx][c_idx]
            table_cells.append(TableCell(
                text=text,
                row=r_idx,
                column=c_idx,
                bbox=cb,
            ))

    return TableData(
        row_count=len(rows),
        column_count=ncols,
        cells=table_cells,
        header_rows=[0] if rows else [],
        header_detection="assumed_first_row",
        span_detection="unsupported",
        bbox=tbl_bbox,
        page=page,
        extraction_method=extraction_method,
        metadata={"source": source},
    )
