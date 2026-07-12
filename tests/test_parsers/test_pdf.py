from __future__ import annotations

from unittest.mock import MagicMock

import fitz

from aksharamd.plugins.parsers.pdf import (
    _PDFPLUMBER_CHAR_LIMIT,
    _apply_inline_fmt,
    _cells_to_markdown,
    _detect_column_boundaries,
    _extract_raw_page,
    _filter_latex_line_numbers,
    _has_interior_intersections,
    _is_bold,
    _is_italic,
    _is_monospace,
    _is_quality_table,
    _is_repetitive_text,
    _is_subscript,
    _is_superscript,
    _parse_marker_markdown,
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
    """Tables with >65% empty data cells are rejected as layout column artifacts."""
    # 2/3 empty per row = 66.7% — above the 65% threshold
    md = (
        "| Col A | Col B | Col C |\n| --- | --- | --- |\n"
        "| Long paragraph text here | | |\n"
        "| Another paragraph here | | |"
    )
    assert not _is_quality_table(md)


def test_quality_accepts_sparse_data_table():
    """Sparse tables up to 65% empty should be accepted (legitimate optional fields)."""
    # 6 data cells, 3 empty = 50% empty — below the 65% threshold
    md = (
        "| Quarter | Revenue | Notes |\n| --- | --- | --- |\n"
        "| Q1 2024 | 1.2M | |\n"
        "| Q2 2024 | | Restated |\n"
        "| Q3 2024 | 1.8M | |\n"
    )
    assert _is_quality_table(md)


def test_quality_accepts_wide_financial_table():
    """9-column financial tables should be accepted (raised cap from 8 to 12)."""
    cols = ["Year", "Q1", "Q2", "Q3", "Q4", "Total", "YoY", "Budget", "Var"]
    md = "| " + " | ".join(cols) + " |\n"
    md += "| " + " | ".join("---" for _ in cols) + " |\n"
    md += "| 2024 | 100 | 200 | 300 | 400 | 1000 | 10% | 950 | 50 |"
    assert _is_quality_table(md)


def test_quality_rejects_thirteen_col():
    """Tables with > 12 columns are rejected as almost certainly mis-detected."""
    cols = [f"C{i}" for i in range(13)]
    md = "| " + " | ".join(cols) + " |\n"
    md += "| " + " | ".join("---" for _ in cols) + " |\n"
    md += "| " + " | ".join(str(i) for i in range(13)) + " |"
    assert not _is_quality_table(md)


def test_quality_rejects_prose_cells_avg_word_count():
    """Tables where avg cell exceeds 12 words are prose wrapped as columns."""
    # Each cell averages ~14 words — above the 12-word threshold
    md = (
        "| Col A | Col B |\n| --- | --- |\n"
        "| the quick brown fox jumped over the lazy dog near the river bank | "
        "a second long sentence fragment continuing the same narrative paragraph |\n"
        "| yet another verbose cell with far too many words to be a real data value | "
        "and one more overlong cell to push the overall average well above twelve words |\n"
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


# ── _PDFPLUMBER_TEXT_SETTINGS values ────────────────────────────────────────

def test_pdfplumber_text_settings_thresholds():
    """Verify the pdfplumber text-strategy settings match expected tuned values."""
    from aksharamd.plugins.parsers.pdf import _PDFPLUMBER_TEXT_SETTINGS
    assert _PDFPLUMBER_TEXT_SETTINGS["min_words_vertical"] == 4
    assert _PDFPLUMBER_TEXT_SETTINGS["min_words_horizontal"] == 2
    assert _PDFPLUMBER_TEXT_SETTINGS["intersection_tolerance"] == 3


# ── _heading_level guards ────────────────────────────────────────────────────

def test_heading_level_rejects_long_prose():
    """Spans longer than 15 words must never be headings regardless of size or bold."""
    from aksharamd.plugins.parsers.pdf import _heading_level
    long_text = "This is a very long sentence that clearly constitutes body prose and not a heading at all"
    assert len(long_text.split()) > 15
    assert _heading_level(24.0, bold=True, median=10.0, text=long_text, centered=False) is None

def test_heading_level_h5_requires_ratio_1_10():
    """H5 now requires ratio >= 1.10 (was 1.05); just-below must not fire.

    Use a 4-word text so the bold body-font path (≤3 words) doesn't fire,
    isolating the ratio check for H5.
    """
    from aksharamd.plugins.parsers.pdf import _heading_level
    # ratio = 1.08, 4 words — below 1.10 and beyond bold body-font limit → None
    assert _heading_level(10.8, bold=True, median=10.0, text="This Bold Section Header", centered=False) is None
    # ratio = 1.12, 4 words — above 1.10 → H5
    level = _heading_level(11.2, bold=True, median=10.0, text="This Bold Section Header", centered=False)
    assert level == 5

def test_heading_level_bold_body_font_max_3_words():
    """Bold body-font headings require ≤3 words (was ≤4); 4-word text must not fire."""
    from aksharamd.plugins.parsers.pdf import _heading_level
    # 4 words, ratio ≈ 1.0, bold — previously matched, now should not
    assert _heading_level(10.0, bold=True, median=10.0, text="Four Word Bold Title", centered=False) is None
    # 3 words, ratio ≈ 1.0, bold — should still match
    assert _heading_level(10.0, bold=True, median=10.0, text="Three Word Title", centered=False) == 4


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


# ── _is_superscript / _is_subscript / _is_monospace ──────────────────────────

def test_superscript_detected_by_flag():
    assert _is_superscript(flags=1, font_name="")  # bit 0 = TEXT_FONT_SUPERSCRIPT

def test_superscript_not_detected_on_plain_span():
    assert not _is_superscript(flags=0, font_name="Arial")

def test_superscript_detected_by_font_name():
    assert _is_superscript(flags=0, font_name="CMSY10-SuperscriptMath")

def test_subscript_detected_by_font_name():
    assert _is_subscript(font_name="ChemSub10")

def test_subscript_not_detected_on_plain_font():
    assert not _is_subscript(font_name="Helvetica")

def test_monospace_detected_by_flag():
    assert _is_monospace(flags=8, font_name="")  # bit 3 = TEXT_FONT_MONOSPACED

def test_monospace_detected_by_courier():
    assert _is_monospace(flags=0, font_name="Courier-Bold")

def test_monospace_detected_by_consolas():
    assert _is_monospace(flags=0, font_name="Consolas")

def test_monospace_not_detected_on_serif():
    assert not _is_monospace(flags=0, font_name="TimesNewRoman")


# ── _apply_inline_fmt — sup / sub ─────────────────────────────────────────────

def test_apply_sup_wraps_text():
    assert _apply_inline_fmt("ref", False, False, False, False, sup=True) == "<sup>ref</sup>"

def test_apply_sub_wraps_text():
    assert _apply_inline_fmt("2", False, False, False, False, sub=True) == "<sub>2</sub>"

def test_apply_sup_outermost_over_bold():
    result = _apply_inline_fmt("text", bold=True, italic=False, strikethrough=False, underline=False, sup=True)
    assert result == "<sup>**text**</sup>"

def test_apply_sup_and_sub_mutually_exclusive():
    # sup takes priority
    result = _apply_inline_fmt("x", False, False, False, False, sup=True, sub=True)
    assert "<sup>" in result
    assert "<sub>" not in result

def test_apply_no_sup_no_sub_unchanged():
    assert _apply_inline_fmt("plain", False, False, False, False) == "plain"


# ── _cells_to_markdown — footnote superscript stripping ──────────────────────

def test_cells_strips_trailing_superscript_number():
    """Footnote markers appended to cell values should not affect cell text."""
    cells = [["Product¹", "Value²"], ["---", "---"], ["Apples³", "100"]]
    md = _cells_to_markdown(cells)
    assert "¹" not in md
    assert "²" not in md
    assert "³" not in md
    assert "Product" in md
    assert "Apples" in md

def test_cells_strips_zero_width_chars():
    """Zero-width spaces embedded by some PDF producers should be removed."""
    cells = [["Col​A", "Col​B"], ["---", "---"], ["val1", "val2"]]
    md = _cells_to_markdown(cells)
    assert "​" not in md


# ── _is_repetitive_text ───────────────────────────────────────────────────────

def test_repetitive_text_detects_marker_loop():
    """Marker OCR hallucination: endless 4-gram repetition must be flagged."""
    loop = "the state of the state of " * 50
    assert _is_repetitive_text(loop)


def test_repetitive_text_accepts_normal_prose():
    """Normal prose with incidental repeated function-word 4-grams must pass."""
    prose = (
        "The quick brown fox jumps over the lazy dog. "
        "A fast red car drives past the slow blue truck. "
        "Revenue increased by twelve percent in the third quarter. "
        "All indicators point toward continued growth in the sector."
    )
    assert not _is_repetitive_text(prose)


def test_repetitive_text_short_text_always_passes():
    """Texts shorter than 16 words are never flagged regardless of content."""
    short = "the the the the the the the the the the"
    assert not _is_repetitive_text(short)


def test_repetitive_text_threshold_respected():
    """A text just under the 15% threshold must not be flagged."""
    # 100 unique 4-grams then 10 duplicates = 9.1% < 15%
    unique_words = [f"word{i}" for i in range(104)]
    text = " ".join(unique_words)
    assert not _is_repetitive_text(text)


# ── _parse_marker_markdown — hallucination detection ─────────────────────────

def test_parse_marker_markdown_suppresses_repetitive_block():
    """A paragraph block that is pure OCR repetition must be dropped and had_hallucination=True."""
    loop_text = "the state of the state of " * 60
    blocks, assets, had_hallucination = _parse_marker_markdown(loop_text, page_num=1)
    assert had_hallucination
    assert not blocks


def test_parse_marker_markdown_normal_text_not_flagged():
    """Normal OCR output must pass through with had_hallucination=False."""
    text = (
        "Revenue increased by twelve percent in the third quarter of fiscal year twenty twenty four. "
        "All key performance indicators remain within expected bounds for the reporting period."
    )
    blocks, assets, had_hallucination = _parse_marker_markdown(text, page_num=1)
    assert not had_hallucination
    assert blocks


def test_parse_marker_markdown_mixed_page():
    """Page with one good paragraph and one repetitive paragraph: hallucination=True, good block kept."""
    good = (
        "Revenue increased by twelve percent in the third quarter of fiscal year twenty twenty four. "
        "All key performance indicators remain within expected bounds for the reporting period."
    )
    bad = "the state of the state of " * 60
    md = good + "\n\n" + bad
    blocks, assets, had_hallucination = _parse_marker_markdown(md, page_num=1)
    assert had_hallucination
    assert any(good[:30] in b.content for b in blocks)


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
    """A page with no text layer must emit an OCR-unavailable notice when neither Tesseract nor Marker is available."""
    import aksharamd.plugins.parsers.pdf as pdf_mod
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.pdf import PDFParser

    monkeypatch.setattr(pdf_mod, "_TESSERACT_AVAILABLE", False)
    monkeypatch.setattr(pdf_mod, "_MARKER_AVAILABLE", False)

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


# ── _is_bold / _is_italic — font-name fallback ──────────────────────────────

def test_bold_flag_takes_priority():
    assert _is_bold(2**4, "")
    assert _is_bold(2**4, "ArialMT")


def test_bold_font_name_fallback_common_variants():
    assert _is_bold(0, "Arial-BoldMT")
    assert _is_bold(0, "Helvetica-Bold")
    assert _is_bold(0, "TimesNewRomanPS-BoldMT")
    assert _is_bold(0, "BCDEEF+Calibri-Bold")
    assert _is_bold(0, "NimbusSans-Heavy")
    assert _is_bold(0, "GillSans-Black")


def test_bold_no_false_positive_plain_fonts():
    assert not _is_bold(0, "ArialMT")
    assert not _is_bold(0, "Helvetica")
    assert not _is_bold(0, "TimesNewRomanPSMT")
    assert not _is_bold(0, "")


def test_italic_flag_takes_priority():
    assert _is_italic(2**1, "")
    assert _is_italic(2**1, "ArialMT")


def test_italic_font_name_fallback_common_variants():
    assert _is_italic(0, "Arial-ItalicMT")
    assert _is_italic(0, "TimesNewRomanPS-ItalicMT")
    assert _is_italic(0, "Helvetica-Oblique")
    assert _is_italic(0, "BCDEEF+Calibri-Italic")


def test_italic_no_false_positive_plain_fonts():
    assert not _is_italic(0, "ArialMT")
    assert not _is_italic(0, "Helvetica")
    assert not _is_italic(0, "")
