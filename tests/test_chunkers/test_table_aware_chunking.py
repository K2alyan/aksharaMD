"""Tests for table-aware chunking (Milestone 4)."""
from __future__ import annotations

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.chunk import Chunk
from aksharamd.models.document import Document
from aksharamd.models.table import ExtractionMethod, TableCell, TableData
from aksharamd.plugins.chunkers.semantic import SemanticChunker
from aksharamd.plugins.chunkers.table_splitter import (
    TableRangePlan,
    make_table_chunk_meta,
    split_table_into_ranges,
)
from aksharamd.renderers.table_markdown import render_row_range


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_td(
    rows: list[list[str]],
    header_rows: list[int] | None = None,
    extraction_method: ExtractionMethod | None = None,
) -> TableData:
    """Build a TableData from a 2-D list of strings."""
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
        extraction_method=extraction_method,
    )


def _make_table_block(
    rows: list[list[str]],
    page: int = 1,
    index: int = 0,
    metadata: dict | None = None,
    extraction_method: ExtractionMethod | None = None,
) -> Block:
    td = _make_td(rows, extraction_method=extraction_method)
    return Block.from_table(
        td,
        page=page,
        index=index,
        metadata=metadata or {},
    )


def _make_ctx(blocks: list[Block]) -> CompilationContext:
    doc = Document(source="test.pdf", blocks=blocks)
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc
    return ctx


def _run_chunker(blocks: list[Block], max_tokens: int = 512) -> list[Chunk]:
    ctx = _make_ctx(blocks)
    chunker = SemanticChunker(max_tokens=max_tokens)
    result = chunker.execute(ctx)
    return result.chunks


# ── render_row_range ───────────────────────────────────────────────────────────

def test_render_row_range_full_matches_render_table_markdown():
    from aksharamd.renderers.table_markdown import render_table_markdown
    td = _make_td([["H1", "H2"], ["R1a", "R1b"], ["R2a", "R2b"]])
    assert render_row_range(td, 0, 2) == render_table_markdown(td)


def test_render_row_range_body_only_prepends_header():
    td = _make_td([["H1", "H2"], ["R1a", "R1b"], ["R2a", "R2b"]])
    result = render_row_range(td, 1, 2)
    lines = result.splitlines()
    assert lines[0] == "| H1 | H2 |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| R1a | R1b |"
    assert lines[3] == "| R2a | R2b |"


def test_render_row_range_single_body_row():
    td = _make_td([["H1", "H2"], ["R1a", "R1b"]])
    result = render_row_range(td, 1, 1)
    lines = result.splitlines()
    assert lines[0] == "| H1 | H2 |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| R1a | R1b |"
    assert len(lines) == 3


def test_render_row_range_header_only():
    td = _make_td([["H1", "H2"]])
    result = render_row_range(td, 0, 0)
    lines = result.splitlines()
    assert lines[0] == "| H1 | H2 |"
    assert lines[1] == "| --- | --- |"


def test_render_row_range_clamps_out_of_bounds():
    td = _make_td([["H1"], ["R1"], ["R2"]])
    result = render_row_range(td, 0, 999)
    assert render_row_range(td, 0, 2) == result


def test_render_row_range_empty_table_returns_empty():
    td = TableData(row_count=0, column_count=0, cells=[])
    assert render_row_range(td, 0, 0) == ""


# ── split_table_into_ranges ────────────────────────────────────────────────────

def test_split_small_table_stays_single_range():
    td = _make_td([["H"], ["R1"], ["R2"]])
    ranges = split_table_into_ranges(td, max_tokens=512)
    assert len(ranges) == 1
    assert ranges[0].row_start == 0
    assert ranges[0].row_end == 2
    assert not ranges[0].oversize


def test_split_header_only_table():
    td = _make_td([["H1", "H2"]])
    ranges = split_table_into_ranges(td, max_tokens=512)
    assert len(ranges) == 1
    assert ranges[0].row_start == 0
    assert ranges[0].row_end == 0


def test_split_large_table_produces_multiple_ranges():
    # Create a table with many rows; use a very small max_tokens to force splitting
    rows = [["Col1", "Col2"]] + [[f"val{r}a", f"val{r}b"] for r in range(20)]
    td = _make_td(rows)
    ranges = split_table_into_ranges(td, max_tokens=20)
    assert len(ranges) > 1


def test_split_ranges_are_contiguous_and_complete():
    rows = [["Col"]] + [[f"row{i}"] for i in range(15)]
    td = _make_td(rows)
    ranges = split_table_into_ranges(td, max_tokens=15)
    assert ranges[0].row_start == 0  # first range includes header (row 0)
    # All body rows are covered
    covered = set()
    for plan in ranges:
        for r in range(plan.row_start, plan.row_end + 1):
            covered.add(r)
    for r in range(1, td.row_count):  # body rows 1..N
        assert r in covered


def test_split_first_range_starts_at_zero():
    rows = [["H"]] + [[f"r{i}"] for i in range(10)]
    td = _make_td(rows)
    ranges = split_table_into_ranges(td, max_tokens=15)
    assert ranges[0].row_start == 0


def test_split_oversized_single_row_emitted_intact():
    # One body row that is itself larger than max_tokens
    long_text = "word " * 200
    td = _make_td([["Header"], [long_text]])
    ranges = split_table_into_ranges(td, max_tokens=10)
    # Must produce at least one range; the oversized row is not dropped
    assert len(ranges) >= 1
    # The long row (row 1) must appear in some range
    covered = {r for plan in ranges for r in range(plan.row_start, plan.row_end + 1)}
    assert 1 in covered


# ── make_table_chunk_meta ──────────────────────────────────────────────────────

def test_make_table_chunk_meta_basic_fields():
    block = _make_table_block([["H1", "H2"], ["R1a", "R1b"]])
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert meta["content_type"] == "table_chunk"
    assert meta["table_block_id"] == block.id
    assert meta["table_id"] == block.checksum
    assert meta["row_start"] == 0
    assert meta["row_end"] == 1
    assert meta["header_rows"] == [0]


def test_make_table_chunk_meta_oversize_row():
    from aksharamd.plugins.chunkers.table_splitter import TableRangePlan
    block = _make_table_block([["H"], ["R1"]])
    plan = TableRangePlan(row_start=0, row_end=1, oversize=True, estimated_tokens=900)
    meta = make_table_chunk_meta(block, row_start=0, row_end=1, plan=plan, chunk_budget_tokens=512)
    assert meta["oversize_row"] is True
    assert meta["estimated_tokens"] == 900
    assert meta["budget_tokens"] == 512


def test_make_table_chunk_meta_no_oversize_flag_when_false():
    from aksharamd.plugins.chunkers.table_splitter import TableRangePlan
    block = _make_table_block([["H"], ["R1"]])
    plan = TableRangePlan(row_start=0, row_end=1, oversize=False)
    meta = make_table_chunk_meta(block, row_start=0, row_end=1, plan=plan)
    assert "oversize_row" not in meta


def test_make_table_chunk_meta_relevant_source_pages():
    from aksharamd.models.table import ExtractionMethod
    td = _make_td([["H"], ["R1"], ["R2"], ["R3"]])
    td = td.model_copy(update={"metadata": {
        "source_pages": [1, 2],
        "page_row_ranges": [
            {"page": 1, "row_start": 0, "row_end": 1},
            {"page": 2, "row_start": 2, "row_end": 3},
        ],
    }, "extraction_method": ExtractionMethod.PDF_STITCHED})
    block = Block.from_table(td, page=1, index=0)
    # A chunk covering rows 0-1 should only reference page 1
    meta0 = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert meta0["source_pages"] == [1]
    # A chunk covering rows 2-3 should only reference page 2
    meta1 = make_table_chunk_meta(block, row_start=2, row_end=3)
    assert meta1["source_pages"] == [2]


def test_make_table_chunk_meta_extraction_method():
    block = _make_table_block(
        [["H"], ["R"]],
        extraction_method=ExtractionMethod.PDF_RULED,
    )
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert "extraction_method" in meta
    assert "ruled" in meta["extraction_method"].lower()


def test_make_table_chunk_meta_sheet_provenance():
    block = _make_table_block([["H"], ["R"]], metadata={"sheet": "Sheet1"})
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert meta.get("sheet") == "Sheet1"


def test_make_table_chunk_meta_source_pages():
    td = _make_td([["H"], ["R"]])
    td = td.model_copy(update={"metadata": {"source_pages": [1, 2]}})
    block = Block.from_table(td, page=1, index=0)
    meta = make_table_chunk_meta(block, row_start=0, row_end=1)
    assert meta.get("source_pages") == [1, 2]


# ── SemanticChunker integration ────────────────────────────────────────────────

def test_small_table_emits_single_chunk():
    blocks = [_make_table_block([["H1", "H2"], ["R1a", "R1b"]])]
    chunks = _run_chunker(blocks)
    assert len(chunks) == 1
    assert chunks[0].metadata.get("content_type") == "table_chunk"


def test_table_chunk_has_correct_content():
    from aksharamd.renderers.table_markdown import render_table_markdown
    td = _make_td([["H1", "H2"], ["R1a", "R1b"]])
    block = Block.from_table(td, page=1, index=0)
    chunks = _run_chunker([block])
    assert len(chunks) == 1
    assert chunks[0].content == render_table_markdown(td)


def test_preceding_blocks_flushed_before_table():
    para = Block(type=BlockType.PARAGRAPH, content="Some text.", page=1, index=0)
    table = _make_table_block([["H"], ["R"]], page=1, index=1)
    chunks = _run_chunker([para, table])
    # At least 2 chunks: one for the paragraph, one for the table
    assert len(chunks) >= 2
    table_chunks = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    assert len(table_chunks) == 1


def test_table_chunk_block_id_matches_source_block():
    block = _make_table_block([["H"], ["R"]])
    chunks = _run_chunker([block])
    table_chunks = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    assert table_chunks[0].metadata["table_block_id"] == block.id


def test_table_chunk_page_range():
    block = _make_table_block([["H"], ["R"]], page=3)
    chunks = _run_chunker([block])
    tc = chunks[0]
    assert tc.page_start == 3
    assert tc.page_end == 3


def test_table_chunk_heading_preserved():
    heading = Block(type=BlockType.HEADING, content="Results", level=2, page=1, index=0)
    table = _make_table_block([["H"], ["R"]], page=1, index=1)
    chunks = _run_chunker([heading, table])
    table_chunks = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    assert table_chunks[0].heading == "Results"


def test_large_table_splits_into_multiple_chunks():
    rows = [["Col1", "Col2"]] + [[f"val{i}a", f"val{i}b"] for i in range(30)]
    block = _make_table_block(rows)
    chunks = _run_chunker([block], max_tokens=20)
    table_chunks = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    assert len(table_chunks) > 1


def test_large_table_chunks_contiguous_row_ranges():
    rows = [["H"]] + [[f"row{i}"] for i in range(20)]
    block = _make_table_block(rows)
    chunks = _run_chunker([block], max_tokens=15)
    tc = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    assert tc[0].metadata["row_start"] == 0
    # Each subsequent body chunk starts after the previous chunk's row_end + 1
    for i in range(1, len(tc)):
        prev_end = tc[i - 1].metadata["row_end"]
        curr_start = tc[i].metadata["row_start"]
        # Body-only chunks start at the first body row after the previous group
        assert curr_start > prev_end


def test_large_table_body_chunks_have_header_in_content():
    rows = [["H1", "H2"]] + [[f"r{i}a", f"r{i}b"] for i in range(20)]
    block = _make_table_block(rows)
    chunks = _run_chunker([block], max_tokens=20)
    tc = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    # All chunks (not just first) should contain the header row text
    for chunk in tc:
        assert "H1" in chunk.content
        assert "H2" in chunk.content


def test_legacy_table_block_uses_standard_chunking():
    # A TABLE block WITHOUT table_data goes through the old greedy path
    block = Block(
        type=BlockType.TABLE,
        content="| A | B |\n| --- | --- |\n| 1 | 2 |",
        page=1,
        index=0,
    )
    chunks = _run_chunker([block])
    assert len(chunks) >= 1
    # No table_chunk metadata on legacy blocks
    assert chunks[0].metadata.get("content_type") != "table_chunk"


def test_chunk_id_is_deterministic():
    block = _make_table_block([["H1", "H2"], ["R1a", "R1b"]])
    doc = Document(source="test.pdf", blocks=[block])
    doc.compute_id()
    ctx1 = CompilationContext(source="test.pdf")
    ctx1.document = doc
    ctx2 = CompilationContext(source="test.pdf")
    ctx2.document = doc

    chunker = SemanticChunker()
    c1 = chunker.execute(ctx1).chunks
    c2 = chunker.execute(ctx2).chunks
    assert [c.id for c in c1] == [c.id for c in c2]


def test_table_chunk_token_count_matches_content():
    from aksharamd.utils import count_tokens
    block = _make_table_block([["H1", "H2"], ["R1a", "R1b"]])
    chunks = _run_chunker([block])
    tc = chunks[0]
    assert tc.token_count == count_tokens(tc.content)


def test_multiple_tables_each_get_own_chunks():
    t1 = _make_table_block([["H1"], ["R1"]], index=0)
    t2 = _make_table_block([["H2"], ["R2"]], index=1)
    chunks = _run_chunker([t1, t2])
    tc = [c for c in chunks if c.metadata.get("content_type") == "table_chunk"]
    assert len(tc) == 2
    block_ids = [c.metadata["table_block_id"] for c in tc]
    assert t1.id in block_ids
    assert t2.id in block_ids
