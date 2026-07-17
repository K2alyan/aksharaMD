"""Tests for aksharamd/models/table.py — TableData, TableCell, BoundingBox."""
from __future__ import annotations

import pytest

from aksharamd.models.table import (
    BoundingBox,
    ExtractionMethod,
    TableCell,
    TableData,
    _normalize_cell_text,
)

# ── BoundingBox ───────────────────────────────────────────────────────────────

def test_bounding_box_round_trip():
    bb = BoundingBox(x0=1.0, y0=2.0, x1=3.0, y1=4.0, coordinate_space="page")
    data = bb.model_dump()
    assert data["x0"] == 1.0
    assert data["coordinate_space"] == "page"
    bb2 = BoundingBox(**data)
    assert bb2 == bb


def test_bounding_box_optional_coordinate_space():
    bb = BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    assert bb.coordinate_space is None


# ── TableCell ─────────────────────────────────────────────────────────────────

def test_table_cell_empty_text():
    cell = TableCell(text="", row=0, column=0)
    assert cell.text == ""
    assert cell.row == 0
    assert cell.column == 0


def test_table_cell_multiline_text_stored_as_is():
    cell = TableCell(text="line1\nline2", row=0, column=0)
    assert "\n" in cell.text  # model stores it; renderer normalizes


def test_table_cell_formula_fields():
    cell = TableCell(
        text="42",
        row=0,
        column=0,
        formula="=SUM(A1:A10)",
        data_type="f",
        number_format="#,##0.00",
        raw_value=42,
    )
    assert cell.formula == "=SUM(A1:A10)"
    assert cell.data_type == "f"
    assert cell.number_format == "#,##0.00"
    assert cell.raw_value == 42


def test_table_cell_defaults():
    cell = TableCell(text="hello", row=1, column=2)
    assert cell.row_span == 1
    assert cell.column_span == 1
    assert cell.is_header is False
    assert cell.id == ""


# ── TableData basic construction ──────────────────────────────────────────────

def test_tabledata_minimal():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            TableCell(text="D", row=1, column=1),
        ],
    )
    assert td.row_count == 2
    assert td.column_count == 2
    assert len(td.cells) == 4


def test_tabledata_zero_rows_no_cells():
    td = TableData(row_count=0, column_count=0, cells=[])
    assert td.row_count == 0


def test_tabledata_zero_rows_with_cells_raises():
    with pytest.raises(ValueError, match="Zero-row table cannot contain cells"):
        TableData(row_count=0, column_count=2, cells=[TableCell(text="x", row=0, column=0)])


def test_tabledata_zero_cols_with_cells_raises():
    with pytest.raises(ValueError, match="Zero-column table cannot contain cells"):
        TableData(row_count=1, column_count=0, cells=[TableCell(text="x", row=0, column=0)])


def test_tabledata_negative_row_count_raises():
    with pytest.raises(ValueError, match="row_count must be >= 0"):
        TableData(row_count=-1, column_count=1, cells=[])


def test_tabledata_cell_out_of_bounds_row():
    with pytest.raises(ValueError, match="Cell row=2 out of bounds"):
        TableData(
            row_count=2,
            column_count=2,
            cells=[TableCell(text="x", row=2, column=0)],
        )


def test_tabledata_cell_out_of_bounds_col():
    with pytest.raises(ValueError, match="Cell column=3 out of bounds"):
        TableData(
            row_count=2,
            column_count=3,
            cells=[TableCell(text="x", row=0, column=3)],
        )


def test_tabledata_duplicate_cell_raises():
    with pytest.raises(ValueError, match="Duplicate or overlapping cell"):
        TableData(
            row_count=1,
            column_count=2,
            cells=[
                TableCell(text="a", row=0, column=0),
                TableCell(text="b", row=0, column=0),
            ],
        )


def test_tabledata_span_overlap_raises():
    with pytest.raises(Exception, match="Span overlap|overlapping"):
        TableData(
            row_count=2,
            column_count=2,
            cells=[
                TableCell(text="a", row=0, column=0, row_span=2),  # covers (1,0)
                TableCell(text="b", row=1, column=0),               # conflicts with span
            ],
        )


def test_tabledata_span_out_of_bounds_raises():
    with pytest.raises(Exception, match="extends outside table bounds"):
        TableData(
            row_count=2,
            column_count=2,
            cells=[TableCell(text="a", row=0, column=0, column_span=3)],
        )


def test_tabledata_header_rows_out_of_bounds_raises():
    with pytest.raises(ValueError, match="header_row 5 out of bounds"):
        TableData(row_count=2, column_count=1, cells=[
            TableCell(text="a", row=0, column=0),
            TableCell(text="b", row=1, column=0),
        ], header_rows=[5])


def test_tabledata_duplicate_header_row_raises():
    with pytest.raises(ValueError, match="Duplicate header_row index"):
        TableData(row_count=2, column_count=1, cells=[
            TableCell(text="a", row=0, column=0),
            TableCell(text="b", row=1, column=0),
        ], header_rows=[0, 0])


# ── header_rows normalization ─────────────────────────────────────────────────

def test_header_rows_normalizes_is_header():
    td = TableData(
        row_count=3,
        column_count=1,
        cells=[
            TableCell(text="h", row=0, column=0, is_header=False),  # will be set True
            TableCell(text="a", row=1, column=0, is_header=True),   # will be set False
            TableCell(text="b", row=2, column=0, is_header=False),
        ],
        header_rows=[0],
    )
    assert td.cells[0].is_header is True
    assert td.cells[1].is_header is False
    assert td.cells[2].is_header is False


def test_no_header_rows_means_no_is_header():
    td = TableData(
        row_count=2,
        column_count=1,
        cells=[
            TableCell(text="a", row=0, column=0, is_header=True),
            TableCell(text="b", row=1, column=0),
        ],
        header_rows=[],
    )
    assert td.cells[0].is_header is False


# ── canonical_payload ─────────────────────────────────────────────────────────

def _make_simple_table() -> TableData:
    return TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            TableCell(text="D", row=1, column=1),
        ],
        header_rows=[0],
        header_detection="assumed_first_row",
        extraction_method=ExtractionMethod.CSV_NATIVE,
    )


def test_canonical_payload_deterministic():
    td1 = _make_simple_table()
    td2 = _make_simple_table()
    assert td1.canonical_payload() == td2.canonical_payload()


def test_canonical_payload_excludes_bbox():
    td = _make_simple_table()
    td2 = _make_simple_table()
    td2 = TableData(
        row_count=td.row_count,
        column_count=td.column_count,
        cells=td.cells,
        header_rows=td.header_rows,
        bbox=BoundingBox(x0=0, y0=0, x1=100, y1=100),
    )
    assert td.canonical_payload() == td2.canonical_payload()


def test_canonical_payload_excludes_extraction_method():
    td1 = TableData(
        row_count=1, column_count=1,
        cells=[TableCell(text="x", row=0, column=0)],
        extraction_method=ExtractionMethod.CSV_NATIVE,
    )
    td2 = TableData(
        row_count=1, column_count=1,
        cells=[TableCell(text="x", row=0, column=0)],
        extraction_method=ExtractionMethod.HTML_NATIVE,
    )
    assert td1.canonical_payload() == td2.canonical_payload()


def test_canonical_payload_excludes_confidence():
    td1 = TableData(row_count=1, column_count=1,
                    cells=[TableCell(text="x", row=0, column=0)], confidence="extracted")
    td2 = TableData(row_count=1, column_count=1,
                    cells=[TableCell(text="x", row=0, column=0)], confidence="inferred")
    assert td1.canonical_payload() == td2.canonical_payload()


def test_canonical_payload_includes_formula():
    td_with = TableData(
        row_count=1, column_count=1,
        cells=[TableCell(text="42", row=0, column=0, formula="=SUM(A1:A10)")],
    )
    td_without = TableData(
        row_count=1, column_count=1,
        cells=[TableCell(text="42", row=0, column=0)],
    )
    assert td_with.canonical_payload() != td_without.canonical_payload()
    cell_payload = td_with.canonical_payload()["cells"][0]
    assert "formula" in cell_payload


def test_canonical_payload_excludes_formula_when_none():
    td = TableData(
        row_count=1, column_count=1,
        cells=[TableCell(text="x", row=0, column=0)],
    )
    cell_payload = td.canonical_payload()["cells"][0]
    assert "formula" not in cell_payload


def test_canonical_payload_cells_sorted_by_row_col():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="D", row=1, column=1),
            TableCell(text="A", row=0, column=0),
            TableCell(text="C", row=1, column=0),
            TableCell(text="B", row=0, column=1),
        ],
    )
    texts = [c["text"] for c in td.canonical_payload()["cells"]]
    assert texts == ["A", "B", "C", "D"]


def test_canonical_payload_header_rows_sorted():
    td = TableData(
        row_count=3,
        column_count=1,
        cells=[
            TableCell(text="a", row=0, column=0),
            TableCell(text="b", row=1, column=0),
            TableCell(text="c", row=2, column=0),
        ],
        header_rows=[1, 0],
    )
    assert td.canonical_payload()["header_rows"] == [0, 1]


# ── compute_ids ───────────────────────────────────────────────────────────────

def test_compute_ids_assigns_table_id():
    td = _make_simple_table()
    td.compute_ids("mytableid")
    assert td.id == "mytableid"


def test_compute_ids_assigns_cell_ids():
    td = _make_simple_table()
    td.compute_ids("mytableid")
    for cell in td.cells:
        assert len(cell.id) == 16
        assert cell.id != ""


def test_compute_ids_deterministic():
    td1 = _make_simple_table()
    td2 = _make_simple_table()
    td1.compute_ids("tid1")
    td2.compute_ids("tid1")
    for c1, c2 in zip(
        sorted(td1.cells, key=lambda c: (c.row, c.column)),
        sorted(td2.cells, key=lambda c: (c.row, c.column)),
    ):
        assert c1.id == c2.id


def test_compute_ids_different_table_id_gives_different_cell_ids():
    td1 = _make_simple_table()
    td2 = _make_simple_table()
    td1.compute_ids("id_alpha")
    td2.compute_ids("id_beta")
    for c1, c2 in zip(
        sorted(td1.cells, key=lambda c: (c.row, c.column)),
        sorted(td2.cells, key=lambda c: (c.row, c.column)),
    ):
        assert c1.id != c2.id


# ── helper methods ────────────────────────────────────────────────────────────

def test_missing_coordinates_none_when_full():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="a", row=0, column=0),
            TableCell(text="b", row=0, column=1),
            TableCell(text="c", row=1, column=0),
            TableCell(text="d", row=1, column=1),
        ],
    )
    assert td.missing_coordinates() == set()


def test_missing_coordinates_detects_gap():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="a", row=0, column=0),
            TableCell(text="b", row=0, column=1),
            TableCell(text="c", row=1, column=0),
            # (1,1) is missing
        ],
    )
    assert td.missing_coordinates() == {(1, 1)}


def test_covered_coordinates_from_span():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="a", row=0, column=0, column_span=2),
            TableCell(text="c", row=1, column=0),
            TableCell(text="d", row=1, column=1),
        ],
    )
    covered = td.covered_coordinates()
    assert (0, 1) in covered
    assert (0, 0) not in covered  # master is not covered


def test_explicit_empty_coordinates():
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="a", row=0, column=0),
            TableCell(text="", row=0, column=1),
            TableCell(text="c", row=1, column=0),
            TableCell(text="d", row=1, column=1),
        ],
    )
    assert td.explicit_empty_coordinates() == {(0, 1)}


# ── _normalize_cell_text ──────────────────────────────────────────────────────

def test_normalize_cell_text_nfc():
    # e + combining accent (NFD) -> normalized (NFC) e-acute
    nfd = "é"
    normalized = _normalize_cell_text(nfd)
    assert normalized == "é"


def test_normalize_cell_text_crlf():
    assert _normalize_cell_text("a\r\nb") == "a\nb"
    assert _normalize_cell_text("a\rb") == "a\nb"
