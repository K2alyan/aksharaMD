"""Tests for HeaderFooterTableValidator.

Calibration fixture: text_multicolumns__pwc (2026-07-13)
  - table_bbox y_top=-36 (above page), page_height=841.89
  - short_frac=0.58 (58% of cells ≤6 chars)
  - Both signals fire → W_HEADER_FOOTER_TABLE_GARBLED

Regression cases:
  - PWC analogue: margin table with high short-frac → WARN
  - Margin table with normal cells → no warn (one signal only)
  - Mid-page table with high short-frac → no warn (one signal only)
  - Mid-page table with normal cells → no warn (no signals)
  - No table_bbox metadata → silently skipped
  - No page_height in col_info → silently skipped
"""
from __future__ import annotations

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins.validators.header_footer_table import (
    HeaderFooterTableValidator,
    _analyse_table,
    _parse_table_cells,
)


# ── helpers ───────────────────────────────────────────────────────────────────

_PAGE_HEIGHT = 841.89  # A3 landscape, matches PWC page

_PWC_TABLE_CONTENT = """\
| Tokio M | Financial |
| --- | --- |
| arine | and |
| Holdings, Inc | Non-Financial |
| . \\| Integrated Annual Report 2021 | Data |
| 176 | 17 |
| Indeendent Audtos Reot Indeendent Audtos Reot | |"""

_GENUINE_TABLE_CONTENT = """\
| Metric | Q1 2021 | Q2 2021 | Q3 2021 |
| --- | --- | --- | --- |
| Revenue ($M) | 125.4 | 138.2 | 142.7 |
| Operating Income | 42.1 | 45.8 | 48.9 |
| Net Margin % | 18.5% | 19.2% | 20.1% |"""

_SHORT_NUMBER_TABLE = """\
| 2021 | 2022 | 2023 | 2024 |
| --- | --- | --- | --- |
| 123 | 456 | 789 | 012 |"""

# Table whose every cell has > 6 non-whitespace chars — isolates the margin signal.
_LONG_CELL_TABLE_CONTENT = """\
| Product Category | Previous Quarter | Current Quarter | Year-over-Year |
| --- | --- | --- | --- |
| Software Subscriptions | 125,400,000 | 138,200,000 | +10.2% increase |
| Professional Services | 42,100,000 | 45,800,000 | +8.8% increase |"""


def _table_block(
    content: str,
    bbox: tuple[float, float, float, float],
    page: int = 1,
) -> Block:
    return Block(
        type=BlockType.TABLE,
        content=content,
        page=page,
        index=0,
        metadata={"table_bbox": bbox},
    )


def _make_ctx(blocks: list[Block], page_height: float = _PAGE_HEIGHT) -> CompilationContext:
    doc = Document(
        source="test.pdf",
        file_type="pdf",
        pages=1,
        blocks=blocks,
        metadata={"pdf_column_info": {1: {"page_width": 595.0, "page_height": page_height}}},
    )
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf", output_dir="/tmp/out")
    ctx.document = doc
    return ctx


def _warning_codes(ctx: CompilationContext) -> set[str]:
    return {
        getattr(i, "code", None)
        for i in ctx.validation.issues
        if i.severity.value == "warning"
    }


# ── _parse_table_cells ────────────────────────────────────────────────────────

class TestParseTableCells:
    def test_skips_separator_rows(self) -> None:
        cells = _parse_table_cells("| A | B |\n| --- | --- |\n| C | D |")
        assert cells == ["A", "B", "C", "D"]

    def test_empty_content(self) -> None:
        assert _parse_table_cells("") == []

    def test_strips_cell_whitespace(self) -> None:
        cells = _parse_table_cells("| hello world | foo |")
        assert "hello world" in cells


# ── _analyse_table unit tests ─────────────────────────────────────────────────

class TestAnalyseTable:
    def test_pwc_analogue_warns(self) -> None:
        """PWC: y_top=-36 (in top margin), short_frac=0.58 → warn."""
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        result = _analyse_table(block, _PAGE_HEIGHT)
        assert result["in_top_margin"] is True
        assert result["high_short_frac"] is True
        assert result["warn"] is True
        assert any("top_margin" in s for s in result["signals"])
        assert any("short_frac" in s for s in result["signals"])

    def test_margin_only_does_not_warn(self) -> None:
        """Table in margin but cells have normal length — no warn (one signal only)."""
        block = _table_block(_LONG_CELL_TABLE_CONTENT, bbox=(50.0, 5.0, 500.0, 120.0))
        result = _analyse_table(block, _PAGE_HEIGHT)
        assert result["in_top_margin"] is True
        assert result["high_short_frac"] is False
        assert result["warn"] is False

    def test_short_frac_only_does_not_warn(self) -> None:
        """High short-frac table in the middle of the page — no warn."""
        mid_y = _PAGE_HEIGHT * 0.40
        block = _table_block(
            _SHORT_NUMBER_TABLE, bbox=(50.0, mid_y, 400.0, mid_y + 80.0)
        )
        result = _analyse_table(block, _PAGE_HEIGHT)
        assert result["in_top_margin"] is False
        assert result["in_bottom_margin"] is False
        assert result["warn"] is False

    def test_genuine_mid_page_table_silent(self) -> None:
        block = _table_block(_GENUINE_TABLE_CONTENT, bbox=(50.0, 200.0, 500.0, 350.0))
        result = _analyse_table(block, _PAGE_HEIGHT)
        assert result["warn"] is False

    def test_bottom_margin_with_short_frags_warns(self) -> None:
        """Short fragment table near bottom margin should also warn."""
        bottom_y = _PAGE_HEIGHT * 0.92
        block = _table_block(
            _PWC_TABLE_CONTENT, bbox=(50.0, bottom_y, 500.0, _PAGE_HEIGHT + 5.0)
        )
        result = _analyse_table(block, _PAGE_HEIGHT)
        assert result["in_bottom_margin"] is True
        assert result["high_short_frac"] is True
        assert result["warn"] is True

    def test_no_bbox_returns_no_warn(self) -> None:
        block = Block(type=BlockType.TABLE, content=_PWC_TABLE_CONTENT, page=1, index=0)
        result = _analyse_table(block, _PAGE_HEIGHT)
        assert result["warn"] is False

    def test_zero_page_height_returns_no_warn(self) -> None:
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        result = _analyse_table(block, page_height=0.0)
        assert result["warn"] is False


# ── validator integration tests ───────────────────────────────────────────────

class TestHeaderFooterTableValidator:
    def test_pwc_analogue_emits_warning(self) -> None:
        """Table matching PWC profile should emit W_HEADER_FOOTER_TABLE_GARBLED."""
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" in _warning_codes(ctx)

    def test_genuine_table_no_warning(self) -> None:
        """A real data table mid-page should not warn."""
        block = _table_block(_GENUINE_TABLE_CONTENT, bbox=(50.0, 150.0, 500.0, 300.0))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx)

    def test_no_document_is_noop(self) -> None:
        ctx = CompilationContext(source="test.pdf", output_dir="/tmp/out")
        ctx.document = None
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx)

    def test_non_table_blocks_ignored(self) -> None:
        block = Block(
            type=BlockType.PARAGRAPH,
            content="some text",
            page=1,
            index=0,
            metadata={"table_bbox": (-0.003, -36.85, 1172.08, 406.77)},
        )
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx)

    def test_missing_page_height_skips_table(self) -> None:
        """Table with no page_height in col_info should be silently skipped."""
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        doc = Document(
            source="test.pdf",
            file_type="pdf",
            pages=1,
            blocks=[block],
            metadata={"pdf_column_info": {}},
        )
        doc.compute_id()
        ctx = CompilationContext(source="test.pdf", output_dir="/tmp/out")
        ctx.document = doc
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx)

    def test_diagnostics_stored_always(self) -> None:
        block = _table_block(_GENUINE_TABLE_CONTENT, bbox=(50.0, 150.0, 500.0, 300.0))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        diag = ctx.document.metadata.get("header_footer_table_diagnostics", {})
        assert "table_analyses" in diag
        assert diag["warned"] is False

    def test_diagnostics_stored_when_warning(self) -> None:
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        diag = ctx.document.metadata.get("header_footer_table_diagnostics", {})
        assert diag["warned"] is True
        assert len(diag["problem_tables"]) >= 1

    def test_table_content_preserved(self) -> None:
        """Warning must not alter the table block's content."""
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert ctx.document.blocks[0].content == _PWC_TABLE_CONTENT

    def test_no_score_cap(self) -> None:
        """W_HEADER_FOOTER_TABLE_GARBLED is warning-only; no score cap."""
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(-0.003, -36.85, 1172.08, 406.77))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None

    # ── margin boundary cases ─────────────────────────────────────────────────

    def test_table_exactly_at_margin_boundary_no_warn(self) -> None:
        """Table starting exactly at the margin threshold should not fire margin signal."""
        margin_y = _PAGE_HEIGHT * 0.10  # exactly at boundary — not inside
        block = _table_block(_PWC_TABLE_CONTENT, bbox=(50.0, margin_y, 500.0, margin_y + 80.0))
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        # at the boundary: y_top == margin_pts → not strictly less than → in_top_margin=False
        diag = ctx.document.metadata.get("header_footer_table_diagnostics", {})
        analyses = diag.get("table_analyses", [])
        assert len(analyses) == 1
        assert analyses[0]["in_top_margin"] is False
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx)
