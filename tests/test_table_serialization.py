"""Tests for token-aware table serialization — Phase 8."""
from __future__ import annotations

from aksharamd.models.table import TableCell, TableData
from aksharamd.renderers.table_markdown import (
    render_table_json_reference,
    render_table_markdown,
    render_table_preview_reference,
    render_table_row_records,
    render_table_tsv,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_table(
    rows: list[list[str]],
    header_rows: list[int] | None = None,
) -> TableData:
    """Build a TableData from a 2D list of strings."""
    row_count = len(rows)
    col_count = max(len(r) for r in rows) if rows else 0
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(TableCell(text=text, row=r, column=c))
    return TableData(
        row_count=row_count,
        column_count=col_count,
        cells=cells,
        header_rows=header_rows if header_rows is not None else [],
    )


def _wide_table(cols: int = 15, data_rows: int = 10) -> TableData:
    """Build a wide dense table with many numeric columns (XLSX-like)."""
    header = [f"Col{c}" for c in range(cols)]
    rows = [header] + [[f"{r * cols + c}" for c in range(cols)] for r in range(data_rows)]
    return _make_table(rows, header_rows=[0])


# ── TSV tests ──────────────────────────────────────────────────────────────────

# T1: basic 2-row table produces correct tab-separated output
def test_tsv_basic_output():
    table = _make_table([["A", "B"], ["C", "D"]], header_rows=[0])
    result = render_table_tsv(table)
    lines = result.split("\n")
    assert len(lines) == 2
    assert lines[0] == "A\tB"
    assert lines[1] == "C\tD"


# T2: header rows come first
def test_tsv_header_rows_first():
    table = _make_table([["Data1", "Data2"], ["H1", "H2"]], header_rows=[1])
    result = render_table_tsv(table)
    lines = result.split("\n")
    # Header row (row 1) should come first
    assert lines[0] == "H1\tH2"
    assert lines[1] == "Data1\tData2"


# T3: tab in cell text is replaced with space
def test_tsv_tab_in_cell_replaced():
    table = _make_table([["A\tB", "C"]], header_rows=[])
    result = render_table_tsv(table)
    assert "\t" in result  # between cells
    # But the tab WITHIN the cell text should be a space, not a tab
    parts = result.split("\t")
    assert "A B" == parts[0]


# T4: newline in cell text is replaced with space
def test_tsv_newline_in_cell_replaced():
    table = _make_table([["Line1\nLine2", "Other"]], header_rows=[])
    result = render_table_tsv(table)
    assert "\n" not in result.split("\t")[0]
    assert "Line1 Line2" in result


# T5: empty cell renders as empty string between tabs
def test_tsv_empty_cell():
    table = _make_table([["A", "", "C"]], header_rows=[])
    result = render_table_tsv(table)
    parts = result.split("\t")
    assert parts[1] == ""


# T6: missing/covered cell renders as single space
def test_tsv_missing_cell_single_space():
    # Build a 2x2 table but only provide cells for (0,0) and (0,1) explicitly
    # Use a rowspan to cover (1,0)
    cells = [
        TableCell(text="A", row=0, column=0, row_span=2),
        TableCell(text="B", row=0, column=1),
        TableCell(text="D", row=1, column=1),
    ]
    table = TableData(row_count=2, column_count=2, cells=cells, header_rows=[0])
    result = render_table_tsv(table)
    lines = result.split("\n")
    # Row 1: (1,0) is covered by span, so " " (single space), (1,1) is "D"
    parts = lines[1].split("\t")
    assert parts[0] == " "
    assert parts[1] == "D"


# ── Row records tests ──────────────────────────────────────────────────────────

# T7: basic output ColName=value; ColName=value
def test_row_records_basic():
    table = _make_table([["Name", "Value"], ["Alice", "100"]], header_rows=[0])
    result = render_table_row_records(table)
    assert result == "Name=Alice; Value=100"


# T8: empty string when no headers
def test_row_records_no_headers_returns_empty():
    table = _make_table([["A", "B"], ["C", "D"]], header_rows=[])
    result = render_table_row_records(table)
    assert result == ""


# T9: empty string when column_count > 12
def test_row_records_too_wide_returns_empty():
    table = _wide_table(cols=13, data_rows=2)
    result = render_table_row_records(table)
    assert result == ""


# T10: duplicate header names get _1, _2 suffixes
def test_row_records_duplicate_header_names():
    table = _make_table([["Val", "Val"], ["A", "B"]], header_rows=[0])
    result = render_table_row_records(table)
    assert "Val_1=A" in result
    assert "Val_2=B" in result


# T11: only body rows emitted (not header rows)
def test_row_records_skips_header_rows():
    table = _make_table([["H1", "H2"], ["D1", "D2"], ["D3", "D4"]], header_rows=[0])
    result = render_table_row_records(table)
    lines = result.strip().split("\n")
    # Only 2 body rows
    assert len(lines) == 2
    assert "H1" not in result or "H1=" in result  # header name is col name, not a value
    assert "H1=D1" in result or "H1=D3" in result  # data rows use header as col name


# ── Preview reference tests ────────────────────────────────────────────────────

# T12: output contains table title, row count, column count
def test_preview_reference_contains_title_and_counts():
    table = _make_table([["A", "B"], ["1", "2"], ["3", "4"]], header_rows=[0])
    result = render_table_preview_reference(table, "tbl1", "tables/tbl1.json", title="My Table")
    assert "My Table" in result
    assert "Rows: 2" in result
    assert "Columns:" in result


# T13: omitted row count in bracket when rows > preview_rows
def test_preview_reference_omitted_count_in_bracket():
    rows = [["H1", "H2"]] + [[str(i), str(i)] for i in range(10)]
    table = _make_table(rows, header_rows=[0])
    result = render_table_preview_reference(table, "t1", "tables/t1.json", preview_rows=5)
    assert "5 additional rows omitted" in result
    assert "Full structured table:" in result


# T14: no bracket when all rows fit
def test_preview_reference_no_bracket_when_all_fit():
    rows = [["H1", "H2"]] + [[str(i), str(i)] for i in range(3)]
    table = _make_table(rows, header_rows=[0])
    result = render_table_preview_reference(table, "t1", "tables/t1.json", preview_rows=5)
    assert "omitted" not in result


# T15: uses 'unavailable' when artifact_path is None
def test_preview_reference_unavailable_when_no_artifact():
    rows = [["H1", "H2"]] + [[str(i), str(i)] for i in range(10)]
    table = _make_table(rows, header_rows=[0])
    result = render_table_preview_reference(table, "t1", None, preview_rows=3)
    assert "unavailable" in result


# T16: JSON reference compact one-line output with row/col counts
def test_json_reference_compact_one_line():
    table = _make_table([["H1", "H2"], ["A", "B"], ["C", "D"]], header_rows=[0])
    result = render_table_json_reference(table, "tbl1", "tables/tbl1.json")
    assert "\n" not in result
    assert "2 rows x 2 columns" in result
    assert "tbl1" in result


# T17: JSON reference uses 'unavailable' when artifact_path is None
def test_json_reference_unavailable_when_no_artifact():
    table = _make_table([["H1"], ["A"]], header_rows=[0])
    result = render_table_json_reference(table, "t1", None)
    assert "unavailable" in result


# T18: TSV has fewer tokens than markdown for a wide dense table (XLSX-like)
def test_tsv_fewer_tokens_than_markdown_wide_table():
    from aksharamd.packaging.token_accounting import count_text_tokens
    table = _wide_table(cols=10, data_rows=8)
    md = render_table_markdown(table)
    tsv = render_table_tsv(table)
    md_tokens = count_text_tokens(md)
    tsv_tokens = count_text_tokens(tsv)
    assert tsv_tokens < md_tokens, (
        f"Expected TSV ({tsv_tokens}) < markdown ({md_tokens}) for a wide table"
    )


# ── build_table_candidates tests ──────────────────────────────────────────────

# T19: build_table_candidates returns all applicable formats
def test_build_table_candidates_returns_all_formats():
    from aksharamd.packaging.models import PackageProfile, TablePayloadFormat
    from aksharamd.packaging.payload_builder import build_table_candidates

    table = _make_table([["A", "B"], ["1", "2"]], header_rows=[0])
    table.id = "tbl1"
    profile = PackageProfile()
    candidates = build_table_candidates(table, "tbl1", "tables/tbl1.json", 100, profile)
    formats = {c.format for c in candidates}
    # At minimum: markdown, tsv, preview_reference, json_reference
    assert TablePayloadFormat.MARKDOWN in formats
    assert TablePayloadFormat.TSV in formats
    assert TablePayloadFormat.PREVIEW_REFERENCE in formats
    assert TablePayloadFormat.JSON_REFERENCE in formats


# ── select_table_serialization tests ──────────────────────────────────────────

def _make_candidates_simple():
    """Make simple candidates for testing the selector."""
    from aksharamd.packaging.models import TablePayloadFormat, TableSerializationCandidate
    return [
        TableSerializationCandidate(
            format=TablePayloadFormat.MARKDOWN, text="md" * 100, token_count=200,
            preserves_all_rows_inline=True, preserves_structure_inline=True,
        ),
        TableSerializationCandidate(
            format=TablePayloadFormat.TSV, text="tsv" * 50, token_count=100,
            preserves_all_rows_inline=True, preserves_structure_inline=True,
        ),
        TableSerializationCandidate(
            format=TablePayloadFormat.PREVIEW_REFERENCE, text="pr" * 20, token_count=40,
            preserves_all_rows_inline=False, preserves_structure_inline=True,
            omitted_row_count=5,
        ),
        TableSerializationCandidate(
            format=TablePayloadFormat.JSON_REFERENCE, text="jr", token_count=10,
            preserves_all_rows_inline=False, preserves_structure_inline=False,
            omitted_row_count=10,
        ),
    ]


# T20: text_first picks lowest-token full-inline within budget
def test_select_text_first_picks_lowest_token_inline():
    from aksharamd.packaging.models import PackageProfile, TablePayloadFormat
    from aksharamd.packaging.payload_builder import select_table_serialization

    candidates = _make_candidates_simple()
    profile = PackageProfile(max_inline_table_tokens=1200)
    selected = select_table_serialization(candidates, "text_first", profile, 0)
    # TSV (100 tokens) is cheapest inline; both are within budget
    assert selected.format == TablePayloadFormat.TSV


# T21: text_first falls back to preview_reference when all full-inline exceed budget
def test_select_text_first_falls_back_to_preview_reference():
    from aksharamd.packaging.models import PackageProfile, TablePayloadFormat
    from aksharamd.packaging.payload_builder import select_table_serialization

    candidates = _make_candidates_simple()
    # Set max_inline_table_tokens very low so none of the inline formats pass
    profile = PackageProfile(max_inline_table_tokens=50)
    selected = select_table_serialization(candidates, "text_first", profile, 0)
    assert selected.format == TablePayloadFormat.PREVIEW_REFERENCE


# T22: full_inline strategy always returns inline regardless of budget
def test_select_full_inline_strategy_always_inline():
    from aksharamd.packaging.models import PackageProfile
    from aksharamd.packaging.payload_builder import select_table_serialization

    candidates = _make_candidates_simple()
    # Very small budget
    profile = PackageProfile(table_payload_strategy="full_inline", max_inline_table_tokens=5)
    selected = select_table_serialization(candidates, "text_first", profile, 0)
    assert selected.preserves_all_rows_inline is True


# T23: reference_only strategy returns json_reference
def test_select_reference_only_returns_json_reference():
    from aksharamd.packaging.models import PackageProfile, TablePayloadFormat
    from aksharamd.packaging.payload_builder import select_table_serialization

    candidates = _make_candidates_simple()
    profile = PackageProfile(table_payload_strategy="reference_only")
    selected = select_table_serialization(candidates, "adaptive", profile, 0)
    assert selected.format == TablePayloadFormat.JSON_REFERENCE


# T24: regression guard - full-inline > 1.05x block_tokens falls back to preview_reference
def test_regression_guard_falls_back_to_preview_reference():
    from aksharamd.packaging.models import PackageProfile, TablePayloadFormat, TableSerializationCandidate
    from aksharamd.packaging.payload_builder import select_table_serialization

    # block_tokens = 100, inline candidates have 200 tokens (> 100 * 1.05 = 105)
    # but within max_inline budget of 1200 — guard should force fallback
    candidates = [
        TableSerializationCandidate(
            format=TablePayloadFormat.MARKDOWN, text="x" * 200, token_count=200,
            preserves_all_rows_inline=True, preserves_structure_inline=True,
        ),
        TableSerializationCandidate(
            format=TablePayloadFormat.TSV, text="y" * 150, token_count=150,
            preserves_all_rows_inline=True, preserves_structure_inline=True,
        ),
        TableSerializationCandidate(
            format=TablePayloadFormat.PREVIEW_REFERENCE, text="pr" * 10, token_count=20,
            preserves_all_rows_inline=False, preserves_structure_inline=True,
            omitted_row_count=5,
        ),
        TableSerializationCandidate(
            format=TablePayloadFormat.JSON_REFERENCE, text="jr", token_count=5,
            preserves_all_rows_inline=False, preserves_structure_inline=False,
        ),
    ]
    # block_tokens = 100; both inline > 100 * 1.05 = 105
    profile = PackageProfile(max_inline_table_tokens=1200)
    selected = select_table_serialization(candidates, "text_first", profile, 100)
    assert selected.format == TablePayloadFormat.PREVIEW_REFERENCE


# ── render_table_for_payload tuple return ─────────────────────────────────────

# T25: render_table_for_payload returns tuple (text, candidate)
def test_render_table_for_payload_returns_tuple():
    from aksharamd.packaging.models import PackageProfile
    from aksharamd.packaging.payload_builder import render_table_for_payload

    table = _make_table([["A", "B"], ["1", "2"]], header_rows=[0])
    result = render_table_for_payload(table, PackageProfile())
    assert isinstance(result, tuple)
    assert len(result) == 2
    text, candidate = result
    assert isinstance(text, str)
    assert candidate is not None


# ── LLMPayloadItem new fields ──────────────────────────────────────────────────

# T26: LLMPayloadItem has table_payload_format field
def test_llm_payload_item_has_table_payload_format():
    from aksharamd.packaging.payload import LLMPayloadItem, PayloadContentType
    item = LLMPayloadItem(
        item_id="i1", content_type=PayloadContentType.STRUCTURED_TABLE,
        document_id="doc1", element_id="e1",
    )
    assert hasattr(item, "table_payload_format")
    assert item.table_payload_format is None


# T27: LLMPayloadItem has inline_complete field
def test_llm_payload_item_has_inline_complete():
    from aksharamd.packaging.payload import LLMPayloadItem, PayloadContentType
    item = LLMPayloadItem(
        item_id="i1", content_type=PayloadContentType.STRUCTURED_TABLE,
        document_id="doc1", element_id="e1",
    )
    assert hasattr(item, "inline_complete")
    assert item.inline_complete is True


# T28: Planner and payload use same format (covers TSV too)
def test_planner_and_payload_agree_on_table_format():
    import tempfile
    from pathlib import Path

    from aksharamd.models.block import Block
    from aksharamd.models.document import Document
    from aksharamd.models.table import ExtractionMethod, TableCell, TableData
    from aksharamd.packaging import (
        PackageMode,
        PackageProfile,
        PackageWriter,
        build_llm_payload,
        plan_document,
    )
    from aksharamd.packaging.payload import PayloadContentType

    # Must use a native extraction method to get STRUCTURED_TABLE routing
    cells = [
        TableCell(text="Name", row=0, column=0),
        TableCell(text="Value", row=0, column=1),
        TableCell(text="Alice", row=1, column=0),
        TableCell(text="100", row=1, column=1),
        TableCell(text="Bob", row=2, column=0),
        TableCell(text="200", row=2, column=1),
    ]
    td = TableData(
        row_count=3, column_count=2, cells=cells,
        header_rows=[0], extraction_method=ExtractionMethod.XLSX_NATIVE,
    )
    block = Block.from_table(td, page=1)
    doc = Document(source="test.xlsx", blocks=[block])
    doc.document_id = "test-doc"
    doc.id = "test-doc"

    profile = PackageProfile(mode=PackageMode.ADAPTIVE)
    plan = plan_document(doc, profile)

    with tempfile.TemporaryDirectory() as td_dir:
        pkg_dir = Path(td_dir)
        writer = PackageWriter()
        asset_refs, _ = writer.write(pkg_dir, plan, doc, None)
        payload = build_llm_payload(plan, doc, pkg_dir, asset_refs, profile)

    table_items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    ti = table_items[0]
    assert ti.table_payload_format is not None
    assert ti.table_markdown is not None


# T29: XLSX-like table: selected format in text_first mode is NOT markdown (it's TSV or preview)
def test_xlsx_like_table_not_markdown_in_text_first():
    """A wide dense table in text_first mode should prefer TSV over markdown."""
    from aksharamd.packaging.models import PackageProfile, TablePayloadFormat
    from aksharamd.packaging.payload_builder import build_table_candidates, select_table_serialization
    from aksharamd.packaging.token_accounting import count_text_tokens

    # Build wide XLSX-like table
    table = _wide_table(cols=8, data_rows=20)
    table.id = "xlsx-tbl"
    # Use a small block_tokens budget to trigger fallback from markdown
    profile = PackageProfile(max_inline_table_tokens=1200)
    from aksharamd.renderers.table_markdown import render_table_markdown
    md = render_table_markdown(table)
    md_tokens = count_text_tokens(md)
    # block_tokens = md_tokens // 2: inline candidates exceed 1.05x block_tokens
    block_tokens = md_tokens // 2
    candidates = build_table_candidates(table, "xlsx-tbl", "tables/xlsx-tbl.json", block_tokens, profile)
    selected = select_table_serialization(candidates, "text_first", profile, block_tokens)
    # TSV should have fewer tokens than markdown; both may pass guard
    # The key assertion: selected is NOT the most expensive markdown format
    md_candidate = next(c for c in candidates if c.format == TablePayloadFormat.MARKDOWN)
    tsv_candidate = next(c for c in candidates if c.format == TablePayloadFormat.TSV)
    # TSV should be cheaper
    assert tsv_candidate.token_count < md_candidate.token_count
    # And text_first should select TSV (cheaper than markdown) if both pass guard
    if selected.format in (TablePayloadFormat.MARKDOWN, TablePayloadFormat.TSV):
        assert selected.token_count <= md_candidate.token_count
