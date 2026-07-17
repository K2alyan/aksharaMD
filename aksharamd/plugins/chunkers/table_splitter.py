"""Greedy row-range splitting for table-aware chunking."""
from __future__ import annotations

from typing import NamedTuple

from ...models.block import Block
from ...models.table import TableData
from ...renderers.table_markdown import render_row_range
from ...utils import count_tokens


class TableRangePlan(NamedTuple):
    """A planned row range for one table chunk.

    oversize is True when the range contains exactly one body row that alone
    exceeds the token budget. The chunk is emitted intact; estimated_tokens
    records the measured cost so callers can surface this in chunk metadata.
    """
    row_start: int
    row_end: int
    oversize: bool = False
    estimated_tokens: int = 0


def _relevant_source_pages(
    td_meta: dict,
    row_start: int,
    row_end: int,
) -> list[int] | None:
    """Return source pages that contributed to [row_start, row_end].

    For stitched PDF tables with page_row_ranges metadata, this narrows the
    list to only pages whose rows overlap the requested range — avoiding
    the situation where a body-only chunk carries the page numbers of every
    page in the full stitched table.
    """
    page_row_ranges = td_meta.get("page_row_ranges", [])
    if not page_row_ranges:
        return td_meta.get("source_pages")
    relevant = [
        entry["page"]
        for entry in page_row_ranges
        if entry["row_start"] <= row_end and entry["row_end"] >= row_start
    ]
    return relevant if relevant else td_meta.get("source_pages")


def split_table_into_ranges(
    table: TableData,
    max_tokens: int,
) -> list[TableRangePlan]:
    """Return TableRangePlan list covering all body rows.

    The first range starts at 0 (includes the header rows). Subsequent ranges
    start at their first body row; render_row_range prepends the header when
    rendering body-only slices.

    Single-row groups that exceed the token budget are emitted with
    oversize=True rather than dropped or split further.
    """
    header_rows = sorted(table.header_rows) if table.header_rows else [0]
    last_header = header_rows[-1]
    body_rows = [r for r in range(table.row_count) if r > last_header]

    if not body_rows:
        return [TableRangePlan(0, table.row_count - 1)]

    # Fast path: whole table fits
    if count_tokens(render_row_range(table, 0, table.row_count - 1)) <= max_tokens:
        return [TableRangePlan(0, table.row_count - 1)]

    # Compute header cost once (header rows + separator line)
    header_md = render_row_range(table, 0, last_header)
    header_tokens = count_tokens(header_md)

    # Per-body-row incremental token cost
    row_costs: dict[int, int] = {}
    for r in body_rows:
        single_slice = render_row_range(table, r, r)
        row_costs[r] = max(1, count_tokens(single_slice) - header_tokens)

    ranges: list[TableRangePlan] = []
    group_start = 0          # first range covers header (starts at row 0)
    group_body_rows: list[int] = []
    accumulated_body_tokens = 0

    def _flush_group() -> None:
        is_single = len(group_body_rows) == 1
        row_cost = accumulated_body_tokens  # equals row_costs[group_body_rows[0]] when single
        oversize = is_single and (header_tokens + row_cost > max_tokens)
        estimated = header_tokens + accumulated_body_tokens if oversize else 0
        ranges.append(TableRangePlan(
            group_start,
            group_body_rows[-1],
            oversize=oversize,
            estimated_tokens=estimated,
        ))

    for row in body_rows:
        rt = row_costs[row]
        if not group_body_rows:
            # Always accept at least one body row (handles oversized single rows)
            group_body_rows.append(row)
            accumulated_body_tokens += rt
        elif header_tokens + accumulated_body_tokens + rt <= max_tokens:
            group_body_rows.append(row)
            accumulated_body_tokens += rt
        else:
            _flush_group()
            group_start = row
            group_body_rows = [row]
            accumulated_body_tokens = rt

    if group_body_rows:
        _flush_group()

    return ranges


def make_table_chunk_meta(
    block: Block,
    row_start: int,
    row_end: int,
    plan: TableRangePlan | None = None,
    chunk_budget_tokens: int = 0,
) -> dict:
    """Build Chunk.metadata for a table row-range chunk."""
    td = block.table_data
    assert td is not None

    meta: dict = {
        "content_type": "table_chunk",
        "table_id": block.checksum,
        "table_block_id": block.id,
        "row_start": row_start,
        "row_end": row_end,
        "header_rows": list(td.header_rows) if td.header_rows else [0],
    }

    if td.extraction_method is not None:
        meta["extraction_method"] = str(td.extraction_method)

    # Source-page provenance: narrow to the pages that contributed this range
    td_meta = td.metadata or {}
    relevant_pages = _relevant_source_pages(td_meta, row_start, row_end)
    if relevant_pages is not None:
        meta["source_pages"] = relevant_pages

    # Sheet/slide provenance (XLSX, PPTX)
    blk_meta = block.metadata or {}
    for key in ("sheet", "slide"):
        if key in blk_meta:
            meta[key] = blk_meta[key]

    # Oversize row: single row that exceeds the token budget, emitted intact
    if plan is not None and plan.oversize:
        meta["oversize_row"] = True
        meta["estimated_tokens"] = plan.estimated_tokens
        if chunk_budget_tokens > 0:
            meta["budget_tokens"] = chunk_budget_tokens

    # Compact table-quality summary (populated by TableQualityValidator before chunking)
    tq = blk_meta.get("table_quality")
    if tq:
        meta["table_quality_status"] = tq.get("overall_status", "unknown")
        risk_names = [
            s["name"] for s in tq.get("signals", []) if s.get("status") == "risk"
        ]
        if risk_names:
            meta["table_quality_signal_names"] = risk_names

    return meta
