from __future__ import annotations

import hashlib
import json
import unicodedata
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ExtractionMethod(StrEnum):
    XLSX_NATIVE     = "xlsx.native"
    XLS_NATIVE      = "xls.native"
    CSV_NATIVE      = "csv.native"
    TSV_NATIVE      = "tsv.native"
    DOCX_NATIVE     = "docx.native"
    HTML_NATIVE     = "html.native"
    PDF_RULED       = "pdf.ruled"
    PDF_BOOKTABS    = "pdf.booktabs"
    PDF_WHITESPACE  = "pdf.whitespace"
    PDF_STITCHED    = "pdf.stitched"
    PPTX_NATIVE     = "pptx.native"
    ODF_NATIVE      = "odf.native"
    PANDOC_AST      = "pandoc.ast"
    OTHER           = "other"


SpanDetection   = Literal["native", "inferred", "unsupported"]
HeaderDetection = Literal["native", "inferred", "assumed_first_row", "none", "unknown"]


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: str | None = None


class TableCell(BaseModel):
    text: str                               # display text; always set; renderer uses this
    row: int                                # zero-based
    column: int                             # zero-based
    row_span: int = 1
    column_span: int = 1
    is_header: bool = False
    bbox: BoundingBox | None = None
    confidence: str | None = None           # ExtractionConfidence value; None = inherit table level
    raw_value: Any | None = None            # pre-formatting value (int, float, date, ...)
    formula: str | None = None              # "=SUM(A1:A10)" for formula cells
    data_type: str | None = None            # XLSX type code: "n", "s", "b", "f", "e"
    number_format: str | None = None        # "#,##0.00", "0.00%", etc.
    metadata: dict = Field(default_factory=dict)
    id: str = ""                            # set by TableData.compute_ids()


class TableData(BaseModel):
    row_count: int
    column_count: int
    cells: list[TableCell]
    header_rows: list[int] = Field(default_factory=list)
    header_detection: HeaderDetection = "unknown"
    span_detection: SpanDetection = "unsupported"
    caption: str | None = None
    bbox: BoundingBox | None = None
    page: int | None = None
    sheet: str | None = None
    slide: int | None = None
    extraction_method: ExtractionMethod | None = None
    confidence: str | None = None
    metadata: dict = Field(default_factory=dict)
    id: str = ""                            # set to block.id after block context is known

    @model_validator(mode="after")
    def _validate_and_normalize(self) -> TableData:
        # Validate bounds
        if self.row_count < 0:
            raise ValueError(f"row_count must be >= 0, got {self.row_count}")
        if self.column_count < 0:
            raise ValueError(f"column_count must be >= 0, got {self.column_count}")
        if self.row_count == 0 and self.cells:
            raise ValueError("Zero-row table cannot contain cells")
        if self.column_count == 0 and self.cells:
            raise ValueError("Zero-column table cannot contain cells")

        # Validate header_rows
        seen_hr: set[int] = set()
        for hr in self.header_rows:
            if not (0 <= hr < self.row_count):
                raise ValueError(f"header_row {hr} out of bounds (row_count={self.row_count})")
            if hr in seen_hr:
                raise ValueError(f"Duplicate header_row index {hr}")
            seen_hr.add(hr)

        # Validate cells and build occupancy
        occupied: dict[tuple[int, int], str] = {}
        for cell in self.cells:
            if not (0 <= cell.row < self.row_count):
                raise ValueError(f"Cell row={cell.row} out of bounds (row_count={self.row_count})")
            if not (0 <= cell.column < self.column_count):
                raise ValueError(f"Cell column={cell.column} out of bounds (column_count={self.column_count})")
            if cell.row_span < 1:
                raise ValueError(f"row_span must be >= 1, got {cell.row_span}")
            if cell.column_span < 1:
                raise ValueError(f"column_span must be >= 1, got {cell.column_span}")
            coord = (cell.row, cell.column)
            if coord in occupied:
                raise ValueError(f"Duplicate or overlapping cell at {coord}")
            occupied[coord] = "master"
            for r in range(cell.row, cell.row + cell.row_span):
                for c in range(cell.column, cell.column + cell.column_span):
                    if r >= self.row_count or c >= self.column_count:
                        raise ValueError(
                            f"Span at ({cell.row},{cell.column}) extends outside "
                            f"table bounds ({self.row_count}x{self.column_count})"
                        )
                    if (r, c) != coord:
                        if (r, c) in occupied:
                            raise ValueError(f"Span overlap at ({r},{c})")
                        occupied[(r, c)] = "covered"

        # Normalize is_header from header_rows (header_rows is canonical)
        header_set = set(self.header_rows)
        for cell in self.cells:
            cell.is_header = cell.row in header_set

        return self

    def canonical_payload(self) -> dict:
        """Deterministic dict for semantic hashing. Excludes provenance/diagnostic fields."""
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "caption": self.caption,
            "header_rows": sorted(self.header_rows),
            "cells": [
                {
                    "text": _normalize_cell_text(c.text),
                    "row": c.row,
                    "column": c.column,
                    "row_span": c.row_span,
                    "column_span": c.column_span,
                    "is_header": c.is_header,
                    **({"formula": c.formula} if c.formula is not None else {}),
                }
                for c in sorted(self.cells, key=lambda c: (c.row, c.column))
            ],
        }

    def compute_ids(self, table_id: str) -> None:
        """Assign id to self and all cells. Call after block.id is known.

        table_id should be block.id — one table per block, so block identity IS table identity.
        cell_id = SHA256(table_id + canonical cell payload JSON)[:16]
        """
        self.id = table_id
        for cell in self.cells:
            cell_payload = json.dumps(
                {
                    "row": cell.row,
                    "column": cell.column,
                    "row_span": cell.row_span,
                    "column_span": cell.column_span,
                    "text": _normalize_cell_text(cell.text),
                    "is_header": cell.is_header,
                    **({"formula": cell.formula} if cell.formula is not None else {}),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            cell.id = hashlib.sha256(
                f"{table_id}:{cell_payload}".encode()
            ).hexdigest()[:16]

    def missing_coordinates(self) -> set[tuple[int, int]]:
        """Coordinates that are neither occupied by a cell nor covered by a span."""
        occupied = self._occupancy()
        return {
            (r, c)
            for r in range(self.row_count)
            for c in range(self.column_count)
            if (r, c) not in occupied
        }

    def covered_coordinates(self) -> set[tuple[int, int]]:
        """Coordinates covered by a spanning master cell (not the master itself)."""
        covered: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.row_span > 1 or cell.column_span > 1:
                for r in range(cell.row, cell.row + cell.row_span):
                    for c in range(cell.column, cell.column + cell.column_span):
                        if (r, c) != (cell.row, cell.column):
                            covered.add((r, c))
        return covered

    def explicit_empty_coordinates(self) -> set[tuple[int, int]]:
        """Coordinates where a cell is present with text=\"\"."""
        return {(c.row, c.column) for c in self.cells if c.text == ""}

    def _occupancy(self) -> set[tuple[int, int]]:
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            occupied.add((cell.row, cell.column))
            for r in range(cell.row, cell.row + cell.row_span):
                for c in range(cell.column, cell.column + cell.column_span):
                    occupied.add((r, c))
        return occupied


def _normalize_cell_text(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
