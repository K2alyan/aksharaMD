from __future__ import annotations

from aksharamd.plugins.parsers.pdf import (
    _PDFPLUMBER_CHAR_LIMIT,
    _cells_to_markdown,
    _is_quality_table,
    _try_pdfplumber_tables,
)

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


# ── _try_pdfplumber_tables ───────────────────────────────────────────────────

def test_pdfplumber_skips_dense_page():
    """Pages over _PDFPLUMBER_CHAR_LIMIT should be skipped without calling pdfplumber."""
    result = _try_pdfplumber_tables(
        pdf_pl=None,  # would crash if called
        page_num=1,
        total_chars=_PDFPLUMBER_CHAR_LIMIT + 1,
        page_height=842,
    )
    assert result == []


def test_pdfplumber_skips_sparse_page():
    """Pages with too few chars (likely scanned) should be skipped."""
    from aksharamd.plugins.parsers.pdf import _OCR_TEXT_THRESHOLD
    result = _try_pdfplumber_tables(
        pdf_pl=None,
        page_num=1,
        total_chars=_OCR_TEXT_THRESHOLD - 1,
        page_height=842,
    )
    assert result == []


def test_pdfplumber_handles_unavailable_gracefully():
    """A bad pdf_pl object (raises on .pages) should return [] not raise."""
    class BadPdfPl:
        @property
        def pages(self):
            raise RuntimeError("simulated failure")

    result = _try_pdfplumber_tables(
        pdf_pl=BadPdfPl(),
        page_num=1,
        total_chars=500,
        page_height=842,
    )
    assert result == []


def _make_borderless_pdf(tmp_path):
    """Build a PDF whose table has no ruling lines — pdfplumber text strategy needed."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    col_x = [60, 220, 380]
    rows = [
        ("Department", "Headcount", "Budget"),
        ("Engineering", "45", "2000000"),
        ("Marketing", "12", "500000"),
        ("Operations", "30", "800000"),
    ]
    for row_idx, row in enumerate(rows):
        y = 120 + row_idx * 20
        for col_idx, cell in enumerate(row):
            page.insert_text((col_x[col_idx], y), cell, fontsize=11)
    path = tmp_path / "borderless.pdf"
    doc.save(str(path))
    return path


def test_pdfplumber_detects_borderless_table(tmp_path):
    """Full pipeline: a text-layout table with no ruling lines is found via pdfplumber."""
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.pdf import PDFParser

    path = _make_borderless_pdf(tmp_path)
    ctx = CompilationContext(source=str(path), output_dir=str(tmp_path / "out"))
    ctx = PDFParser().execute(ctx)

    assert ctx.document is not None
    tables = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 1
    content = tables[0].content
    assert "Department" in content
    assert "Engineering" in content
    assert "45" in content


def test_pdfplumber_bbox_converts_to_pymupdf_coords(tmp_path):
    """Verify that pdfplumber bboxes are flipped to PyMuPDF (bottom-left) coords."""
    import pdfplumber

    path = _make_borderless_pdf(tmp_path)
    with pdfplumber.open(str(path)) as pdf_pl:
        page_height = 842.0
        results = _try_pdfplumber_tables(pdf_pl, 1, 500, page_height)

    assert len(results) == 1
    x0, y0, x1, y1 = results[0]["bbox"]
    # y0 should be less than y1 in PyMuPDF coords (bottom-left origin)
    assert y0 < y1
    # and the bbox should be within page bounds
    assert 0 <= y0 <= page_height
    assert 0 <= y1 <= page_height


def test_pdfplumber_empty_rows_filtered(tmp_path):
    """Empty rows pdfplumber inserts for inter-row gaps must not appear in output."""
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.pdf import PDFParser

    path = _make_borderless_pdf(tmp_path)
    ctx = CompilationContext(source=str(path), output_dir=str(tmp_path / "out"))
    ctx = PDFParser().execute(ctx)

    tables = [b for b in ctx.document.blocks if b.type.name == "TABLE"]
    assert tables
    # No row should be entirely empty cells (|  |  |  |)
    for line in tables[0].content.splitlines():
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if cells and all(c == "---" for c in cells):
            continue  # separator row is fine
        assert not all(c == "" for c in cells), f"Empty row found: {line!r}"
