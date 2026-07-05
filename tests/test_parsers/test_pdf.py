from __future__ import annotations

from unittest.mock import MagicMock, patch

import fitz

from aksharamd.plugins.parsers.pdf import (
    _PDFPLUMBER_CHAR_LIMIT,
    _cells_to_markdown,
    _detect_column_boundaries,
    _extract_raw_page,
    _filter_latex_line_numbers,
    _has_interior_intersections,
    _is_quality_table,
    _try_pdfplumber_tables,
)

# ── _detect_column_boundaries ───────────────────────────────────────────────

def _make_span(x: float, y: float) -> dict:
    return {"x": x, "y": y, "text": "word", "size": 10, "bold": False, "bbox": (x, y, x + 30, y + 10)}


def test_two_column_layout_detected():
    """Spans mimicking arXiv two-column layout should produce one boundary.

    In a real two-column PDF the two columns have independent text flow,
    so each column has many y-values not shared by the other column.
    Line starts then cluster at two x-positions (~12% and ~52% of page width).
    """
    page_width = 612.0
    spans = []
    # Column 1 lines: x≈72 (12%), each line at a unique y
    for line in range(20):
        y = 100 + line * 14
        spans.append(_make_span(72, y))
        spans.append(_make_span(110, y))
        spans.append(_make_span(150, y))
    # Column 2 lines: x≈320 (52%), at different y values (independent text flow)
    for line in range(20):
        y = 107 + line * 14  # offset by 7 pt — avoids 3 pt y-grouping window
        spans.append(_make_span(320, y))
        spans.append(_make_span(360, y))
        spans.append(_make_span(400, y))

    boundaries = _detect_column_boundaries(spans, page_width)
    assert len(boundaries) == 1
    # Boundary should fall between column 1 (≈12%) and column 2 (≈52%)
    assert 0.20 < boundaries[0] < 0.45


def test_single_column_no_boundary():
    """A single-column layout with consistent left margin should not produce boundaries."""
    page_width = 612.0
    spans = []
    for line in range(20):
        y = 100 + line * 14
        spans.append(_make_span(72, y))
        spans.append(_make_span(110, y))
        spans.append(_make_span(170, y))
        spans.append(_make_span(240, y))

    boundaries = _detect_column_boundaries(spans, page_width)
    assert boundaries == []


def test_empty_spans_returns_empty():
    assert _detect_column_boundaries([], 612.0) == []


def test_zero_page_width_returns_empty():
    spans = [_make_span(72, 100)]
    assert _detect_column_boundaries(spans, 0.0) == []


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


def test_quality_rejects_word_split_two_data_rows():
    """Pattern B fires on tables with exactly 2 data rows (threshold lowered from 3)."""
    # Both rows have mid-word splits: "Engin|eering", "Analys|is"
    md = "| Split | Header |\n| --- | --- |\n| Engin | eering |\n| Analys | is |"
    assert not _is_quality_table(md)


def test_quality_accepts_two_data_rows_clean():
    """A legitimate 2-row table must not be rejected by Pattern B."""
    md = "| Name | Score |\n| --- | --- |\n| Alice | 95 |\n| Bob | 87 |"
    assert _is_quality_table(md)


def test_quality_rejects_header_adj_split_borderplate():
    """Header row word-split pushes combined adj_split ratio over 30% threshold."""
    # Data rows alone: 1 split / 4 pairs = 25% (below threshold).
    # Header "Company Nam L"|"e, Inc." adds 1 split → 2/5 = 40% → rejected.
    md = (
        "| Company Nam L | e, Inc. |\n| --- | --- |\n"
        "| word end | ed item |\n"
        "| normal | 123 |\n"
        "| normal | 456 |\n"
        "| normal | 789 |"
    )
    assert not _is_quality_table(md)


def test_quality_rejects_mostly_empty_cells():
    """Tables with >50% empty data cells are rejected as layout column artifacts."""
    md = (
        "| Col A | Col B | Col C |\n| --- | --- | --- |\n"
        "| Long paragraph text here | | |\n"
        "| Another paragraph here | | |"
    )
    assert not _is_quality_table(md)


def test_quality_rejects_prose_cells_avg_word_count():
    """Tables where avg cell has > 8 words are prose wrapped as columns, not real tables."""
    # 2-column chapter page: each cell is a sentence fragment
    md = (
        "| Col A | Col B |\n| --- | --- |\n"
        "| the algorithm must process all incoming requests before | returning the aggregated result to the calling service |\n"
        "| cache invalidation is triggered whenever the upstream | data source signals a change to the subscriber |\n"
        "| retry logic applies an exponential backoff strategy | so that transient failures do not cascade downstream |\n"
    )
    assert not _is_quality_table(md)


def test_quality_accepts_real_table_short_cells():
    """A real table with short data cells must not be rejected by the prose-cell check."""
    md = (
        "| Category | Item | Value |\n| --- | --- | --- |\n"
        "| Memory | Cache | 24.5 |\n"
        "| Compute | Worker | 18.0 |\n"
        "| Storage | Buffer | 21.0 |\n"
    )
    assert _is_quality_table(md)


# ── _has_interior_intersections ─────────────────────────────────────────────

def test_page_border_has_no_interior_intersections():
    """A single rectangle (page border) only produces corner intersections — rejected."""
    # Rectangle from (50,50) to (550,750): 2 h-lines + 2 v-lines, all corners
    h_lines = [(50.0, 50.0, 550.0), (750.0, 50.0, 550.0)]
    v_lines = [(50.0, 50.0, 750.0), (550.0, 50.0, 750.0)]
    assert not _has_interior_intersections(h_lines, v_lines)


def test_two_column_table_has_interior_intersections():
    """A 2-column, 3-row table (3 h-lines, 3 v-lines including 1 column divider) passes."""
    # Horizontal lines at y=100, y=200, y=300 spanning x=50..550
    h_lines = [(100.0, 50.0, 550.0), (200.0, 50.0, 550.0), (300.0, 50.0, 550.0)]
    # Vertical lines: left border x=50, column divider x=300, right border x=550
    v_lines = [(50.0, 100.0, 300.0), (300.0, 100.0, 300.0), (550.0, 100.0, 300.0)]
    # x=300 (divider) is interior to h-lines spanning 50..550 → 3 interior crossings
    assert _has_interior_intersections(h_lines, v_lines)


def test_border_plus_one_interior_divider_not_enough():
    """A decorative box with a single column divider produces only 2 interior crossings."""
    # Rectangle border: 2 h-lines, 2 v-lines (at endpoints)
    h_lines = [(50.0, 50.0, 550.0), (750.0, 50.0, 550.0)]
    v_lines = [(50.0, 50.0, 750.0), (550.0, 50.0, 750.0), (300.0, 50.0, 750.0)]
    # x=300 is interior to both h-lines → 2 interior crossings, below threshold of 3
    assert not _has_interior_intersections(h_lines, v_lines)


def test_empty_lines_returns_false():
    assert not _has_interior_intersections([], [])
    assert not _has_interior_intersections([(100.0, 0.0, 500.0)], [])


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


# ── _is_quality_table — LaTeX line-number rejection ─────────────────────────

def test_quality_rejects_line_number_bleed_header():
    """Header first cell 'N LETTER' (e.g. '1 S') is a LaTeX line-number bleed."""
    md = "| 1 S | upplementary Analysis |\n| --- | --- |\n| 2 | Results Supporting |\n| 3 Gu | ideline 44 |"
    assert not _is_quality_table(md)


def test_quality_rejects_small_integer_header_narrow_table():
    """A 2-column table whose header first cell is a small integer (≤20) is a line-number table."""
    md = "| 1 | 3 Model Parameters 5 |\n| --- | --- |\n| 2 | 3.1 Antonopoulos 5 |\n| 3 | 3.2 Derived Cycling 5 |\n| 4 | 3.3 Commercial Building 6 |\n| 5 | 4 Numerical Results 6 |"
    assert not _is_quality_table(md)


def test_quality_accepts_real_two_column_numbered_table():
    """A two-column table with a proper named header must not be rejected."""
    md = "| No. | Description |\n| --- | --- |\n| 1 | First item |\n| 2 | Second item |"
    assert _is_quality_table(md)


def test_quality_accepts_wide_numbered_table():
    """A >3-column table with integer first column should NOT be rejected by the narrow-table guard."""
    md = "| 1 | Parameter | Value | Unit |\n| --- | --- | --- | --- |\n| 2 | Temperature | 28 | C |\n| 3 | Humidity | 45 | pct |"
    assert _is_quality_table(md)


# ── _filter_latex_line_numbers ───────────────────────────────────────────────

def _make_ln_span(n: int, page_width: float = 612.0) -> dict:
    """Simulate a LaTeX \\lineno margin span at x ≈ 30 pt."""
    return {"x": 30.0, "y": 100.0 + n * 12, "text": str(n), "size": 8,
            "bold": False, "bbox": (30, 100 + n * 12, 45, 110 + n * 12)}


def test_filter_removes_sequential_left_margin_integers():
    """Sequential integers at x < 8 % of page width are removed."""
    page_width = 612.0
    spans = [_make_ln_span(i) for i in range(1, 16)]  # 15 line-number spans
    spans.append({"x": 90.0, "y": 110.0, "text": "body text", "size": 10,
                  "bold": False, "bbox": (90, 110, 200, 120)})
    result = _filter_latex_line_numbers(spans, page_width)
    assert len(result) == 1
    assert result[0]["text"] == "body text"


def test_filter_leaves_sparse_integers_alone():
    """Fewer than 6 candidate spans = no filtering (avoids removing page numbers)."""
    page_width = 612.0
    spans = [_make_ln_span(i) for i in range(1, 5)]  # only 4 spans
    result = _filter_latex_line_numbers(spans, page_width)
    assert len(result) == 4


def test_filter_leaves_non_sequential_integers_alone():
    """Non-sequential integers (avg step > 3) are not line numbers."""
    page_width = 612.0
    spans = [_make_ln_span(i * 10) for i in range(1, 12)]  # steps of 10
    result = _filter_latex_line_numbers(spans, page_width)
    assert len(result) == 11  # unchanged


def test_filter_leaves_interior_integers_alone():
    """Integers at x > 8 % of page width are not margin line numbers."""
    page_width = 612.0
    spans = [{"x": 100.0, "y": 100.0 + i * 12, "text": str(i), "size": 10,
              "bold": False, "bbox": (100, 100 + i * 12, 120, 110 + i * 12)}
             for i in range(1, 15)]
    result = _filter_latex_line_numbers(spans, page_width)
    assert len(result) == 14  # unchanged


def _make_borderless_pdf(tmp_path):
    """Build a PDF whose table has no ruling lines — pdfplumber text strategy needed.

    Uses 6 rows so each column contains >= 5 words, meeting min_words_vertical=5.
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    col_x = [60, 220, 380]
    rows = [
        ("Department", "Headcount", "Budget"),
        ("Engineering", "45", "2000000"),
        ("Marketing", "12", "500000"),
        ("Operations", "30", "800000"),
        ("HR", "8", "250000"),
        ("Legal", "5", "300000"),
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


# ── OCR-unavailable notice ───────────────────────────────────────────────────

def _make_image_only_pdf(tmp_path):
    """Build a PDF whose single page is a rasterised image (no text layer)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Draw a filled rectangle to simulate image content (PyMuPDF drawing, not a text span)
    page.draw_rect(fitz.Rect(50, 50, 545, 792), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    path = tmp_path / "image_only.pdf"
    doc.save(str(path))
    return path


def test_ocr_unavailable_emits_notice_on_scanned_page(tmp_path, monkeypatch):
    """A page with no text layer must emit an OCR-unavailable notice when pytesseract is absent."""
    import aksharamd.plugins.parsers.pdf as pdf_mod
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.pdf import PDFParser

    monkeypatch.setattr(pdf_mod, "_TESSERACT_AVAILABLE", False)

    path = _make_image_only_pdf(tmp_path)
    ctx = CompilationContext(source=str(path), output_dir=str(tmp_path / "out"))
    ctx = PDFParser().execute(ctx)

    all_text = " ".join(b.content for b in ctx.document.blocks)
    assert "pytesseract" in all_text, "OCR-unavailable notice not emitted for scanned page"


def test_content_image_label_not_empty(tmp_path):
    """IMAGE blocks from content images must carry a descriptive label, not empty string."""
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.pdf import PDFParser

    path = _make_borderless_pdf(tmp_path)
    ctx = CompilationContext(source=str(path), output_dir=str(tmp_path / "out"))
    ctx = PDFParser().execute(ctx)

    image_blocks = [b for b in ctx.document.blocks if b.type == BlockType.IMAGE]
    for blk in image_blocks:
        assert blk.content, f"IMAGE block on page {blk.page} has empty content label"


# ── Invisible text (PDF rendering mode Tr=3) ─────────────────────────────────

def _make_fake_page(rawdict: dict) -> MagicMock:
    """Return a minimal mock fitz.Page that returns the given rawdict from get_text()."""
    page = MagicMock(spec=fitz.Page)
    page.get_text.return_value = rawdict
    page.get_drawings.return_value = []          # no ruled tables
    page.get_images.return_value = []            # no embedded images
    page.rect = MagicMock()
    page.rect.width = 612.0
    page.rect.height = 792.0
    return page


def _make_char(char: str, rendering_mode: int) -> dict:
    """Minimal rawdict character dict."""
    return {
        "c": char,
        "flags": rendering_mode,   # lower 4 bits = PDF text rendering mode
        "origin": (10.0, 20.0),
        "bbox": (10.0, 15.0, 20.0, 25.0),
        "color": 0,
        "size": 12.0,
    }


def test_invisible_chars_are_filtered():
    """Characters with rendering mode 3 (Tr=3, invisible) must not appear in output spans."""
    visible_char = _make_char("H", rendering_mode=0)   # normal fill
    invisible_char = _make_char("X", rendering_mode=3) # no fill, no stroke — invisible

    span = {
        "chars": [visible_char, invisible_char],
        "size": 12.0,
        "flags": 0,
        "origin": (10.0, 20.0),
        "bbox": (10.0, 15.0, 40.0, 25.0),
        "color": 0,
    }
    rawdict = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}

    fake_pdf = MagicMock(spec=fitz.Document)
    fake_pdf.__getitem__ = MagicMock(return_value=_make_fake_page(rawdict))

    result = _extract_raw_page(fake_pdf, page_num=1)

    all_text = " ".join(s["text"] for s in result.spans)
    assert "H" in all_text, "visible char should survive"
    assert "X" not in all_text, "invisible (Tr=3) char must be filtered out"


def test_visible_chars_pass_through():
    """Characters with rendering mode 0 (normal) are kept unchanged."""
    chars = [_make_char(c, rendering_mode=0) for c in "Hello"]
    span = {
        "chars": chars,
        "size": 12.0,
        "flags": 0,
        "origin": (10.0, 20.0),
        "bbox": (10.0, 15.0, 80.0, 25.0),
        "color": 0,
    }
    rawdict = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}

    fake_pdf = MagicMock(spec=fitz.Document)
    fake_pdf.__getitem__ = MagicMock(return_value=_make_fake_page(rawdict))

    result = _extract_raw_page(fake_pdf, page_num=1)

    all_text = " ".join(s["text"] for s in result.spans)
    assert "Hello" in all_text


def test_span_with_only_invisible_chars_is_dropped():
    """A span where every character is invisible should produce no output span at all."""
    chars = [_make_char(c, rendering_mode=3) for c in "ghost"]
    span = {
        "chars": chars,
        "size": 12.0,
        "flags": 0,
        "origin": (10.0, 20.0),
        "bbox": (10.0, 15.0, 80.0, 25.0),
        "color": 0,
    }
    rawdict = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}

    fake_pdf = MagicMock(spec=fitz.Document)
    fake_pdf.__getitem__ = MagicMock(return_value=_make_fake_page(rawdict))

    result = _extract_raw_page(fake_pdf, page_num=1)

    assert result.spans == [], "all-invisible span should produce zero output spans"
