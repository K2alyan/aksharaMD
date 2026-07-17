"""Tests for aksharamd/renderers/table_markdown.py."""
from __future__ import annotations

from aksharamd.models.table import TableCell, TableData
from aksharamd.renderers.table_markdown import render_table_markdown


def _make_table(rows: list[list[str]], header_rows: list[int] | None = None) -> TableData:
    """Helper: build TableData from a 2-D list of strings."""
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
    )


# ── basic rendering ───────────────────────────────────────────────────────────

def test_basic_2x2():
    td = _make_table([["A", "B"], ["C", "D"]])
    md = render_table_markdown(td)
    lines = md.splitlines()
    assert len(lines) == 3  # row0 + separator + row1
    assert "| A | B |" == lines[0]
    assert "| --- | --- |" == lines[1]
    assert "| C | D |" == lines[2]


def test_separator_always_emitted_no_header():
    """header_detection='none' still emits separator after row 0 for valid GFM."""
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            TableCell(text="D", row=1, column=1),
        ],
        header_rows=[],
        header_detection="none",
    )
    md = render_table_markdown(td)
    lines = md.splitlines()
    assert "| --- | --- |" in lines


def test_empty_cell_text_renders_as_empty():
    td = _make_table([["A", ""], ["C", "D"]])
    md = render_table_markdown(td)
    first_line = md.splitlines()[0]
    # Empty text -> no text between pipes (|  | = just the join spaces)
    assert first_line == "| A |  |"


def test_pipe_char_escaped():
    td = _make_table([["a|b", "c"], ["d", "e"]])
    md = render_table_markdown(td)
    assert r"a\|b" in md
    assert "|b" not in md.replace(r"\|", "")


def test_multiline_cell_text_replaced_with_space():
    td = _make_table([["line1\nline2", "B"], ["C", "D"]])
    md = render_table_markdown(td)
    assert "\n" not in md.splitlines()[0]
    assert "line1 line2" in md.splitlines()[0]


def test_single_column_table():
    td = _make_table([["Header"], ["Row1"], ["Row2"]])
    md = render_table_markdown(td)
    lines = md.splitlines()
    assert lines[0] == "| Header |"
    assert lines[1] == "| --- |"
    assert lines[2] == "| Row1 |"


def test_empty_tabledata_returns_empty_string():
    td = TableData(row_count=0, column_count=0, cells=[])
    assert render_table_markdown(td) == ""


def test_zero_columns_returns_empty_string():
    td = TableData(row_count=1, column_count=0, cells=[])
    assert render_table_markdown(td) == ""


def test_span_covered_position_renders_as_empty():
    """Master at (0,0) column_span=2, table 1x3: position (0,1) is covered -> empty."""
    td = TableData(
        row_count=1,
        column_count=3,
        cells=[
            TableCell(text="Merged", row=0, column=0, column_span=2),
            TableCell(text="Single", row=0, column=2),
        ],
    )
    md = render_table_markdown(td)
    lines = md.splitlines()
    # (0,0)=Merged, (0,1)=covered(space), (0,2)=Single
    assert lines[0] == "| Merged |   | Single |"


def test_missing_position_renders_as_empty():
    """Position (1,1) not occupied -> empty cell."""
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            # (1,1) missing
        ],
    )
    md = render_table_markdown(td)
    lines = md.splitlines()
    assert lines[2] == "| C |   |"


def test_deterministic_same_input_same_output():
    td1 = _make_table([["X", "Y"], ["1", "2"]])
    td2 = _make_table([["X", "Y"], ["1", "2"]])
    assert render_table_markdown(td1) == render_table_markdown(td2)


def test_3x3_table():
    td = _make_table([
        ["Name", "Age", "City"],
        ["Alice", "30", "London"],
        ["Bob", "25", "Paris"],
    ])
    md = render_table_markdown(td)
    lines = md.splitlines()
    assert len(lines) == 4  # row0 + sep + 2 data rows
    assert "Name" in lines[0]
    assert "Alice" in lines[2]
    assert "Bob" in lines[3]
