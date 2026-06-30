from __future__ import annotations

from aksharamd.plugins.parsers.pdf import _cells_to_markdown, _is_quality_table

# ── _cells_to_markdown ──────────────────────────────────────────────────────

def test_basic_table():
    cells = [["Name", "Score"], ["Alice", "95"], ["Bob", "87"]]
    md = _cells_to_markdown(cells)
    lines = md.splitlines()
    assert len(lines) == 4
    assert "Name" in lines[0] and "Score" in lines[0]
    assert "---" in lines[1]
    assert "Alice" in lines[2]
    assert "Bob" in lines[3]


def test_empty_returns_empty():
    assert _cells_to_markdown([]) == ""


def test_none_values_become_empty():
    cells = [["Region", None, "Sales"], [None, None, "100"]]
    md = _cells_to_markdown(cells)
    assert "Region" in md
    assert "100" in md


def test_pipe_in_cell_escaped():
    cells = [["A|B", "C"], ["1", "2"]]
    md = _cells_to_markdown(cells)
    assert "A\\|B" in md


def test_uneven_rows_padded():
    cells = [["A", "B", "C"], ["1", "2"]]
    md = _cells_to_markdown(cells)
    lines = md.splitlines()
    # All rows should have same number of pipes
    counts = [ln.count("|") for ln in lines if ln]
    assert len(set(counts)) == 1


def test_multirow_header_dedup():
    # tab.extract() repeats merged-cell values vertically across header rows.
    # "Region" appears in col 0 for both row 0 and row 1 (vertical merge ghost).
    cells = [
        ["Region",  "North",  "Metric"],
        ["Region",  "South",  "Total"],   # "Region" in col 0 is a ghost of the merged cell above
        ["1000",    "2000",   "3000"],
    ]
    md = _cells_to_markdown(cells)
    lines = md.splitlines()
    # lines[2] is the second header row (after separator line)
    row1_cells = [c.strip() for c in lines[2].split("|")[1:-1]]
    assert row1_cells[0] == ""      # ghost "Region" blanked
    assert row1_cells[1] == "South" # other values preserved


def test_multirow_header_dedup_financial():
    # Three-row header: outer group label repeated across all sub-rows
    cells = [
        ["Revenue", "Revenue", "Revenue"],
        ["Q1",      "Q2",      "Q3"],
        ["100",     "200",     "300"],
    ]
    md = _cells_to_markdown(cells)
    lines = md.splitlines()
    # Row 1 ("Q1/Q2/Q3") has no digit → first_data = 2. Dedup applies to row 1.
    # "Revenue" in cols 1 and 2 of row 1 would be blanked if they equal row 0.
    # But row 1 is "Q1/Q2/Q3" which doesn't equal "Revenue" — only the next case matters.
    # Verify row 0 still has all three "Revenue" cells (we don't dedup row 0 itself).
    assert lines[0].count("Revenue") == 3


def test_single_row_no_dedup():
    cells = [["A", "B"], ["1", "2"]]
    md = _cells_to_markdown(cells)
    assert "A" in md and "B" in md


def test_whitespace_normalized():
    cells = [["  Hello  World  ", "B"], ["1", "2"]]
    md = _cells_to_markdown(cells)
    assert "Hello World" in md


# ── _is_quality_table ───────────────────────────────────────────────────────

def test_quality_accepts_two_col_with_data():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert _is_quality_table(md)


def test_quality_rejects_single_column():
    md = "| Only |\n| --- |\n| value |"
    assert not _is_quality_table(md)


def test_quality_rejects_no_data_rows():
    md = "| A | B |\n| --- | --- |"
    assert not _is_quality_table(md)


def test_quality_rejects_empty():
    assert not _is_quality_table("")


def test_quality_accepts_many_columns():
    md = "| A | B | C | D |\n| --- | --- | --- | --- |\n| 1 | 2 | 3 | 4 |"
    assert _is_quality_table(md)
