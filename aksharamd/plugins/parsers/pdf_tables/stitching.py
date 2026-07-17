"""Page-break table stitching for PDF structured and legacy table blocks."""
from __future__ import annotations

import re

from ....models.block import Block, BlockType, ExtractionConfidence
from ....models.table import ExtractionMethod, TableCell, TableData


# ── Markdown-string helpers (used for legacy blocks without table_data) ────────

def _tbl_header_line(md: str) -> str:
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("|") and "---" not in s:
            return s
    return ""


def _tbl_col_count(header_line: str) -> int:
    return len([c for c in header_line.split("|") if c.strip()])


def _tbl_continuation_rows(b_md: str, a_header: str) -> list[str]:
    lines = [ln for ln in b_md.splitlines() if ln.strip()]
    if not lines:
        return []
    start = 0
    if lines[0].strip() == a_header:
        start = 1
    if start < len(lines) and "---" in lines[start] and lines[start].strip().startswith("|"):
        start += 1
    return [ln for ln in lines[start:] if ln.strip()]


# ── Structured helpers ─────────────────────────────────────────────────────────

def _header_texts(td: TableData) -> list[str]:
    """Ordered cell texts for row 0, normalized (stripped)."""
    cells = sorted((c for c in td.cells if c.row == 0), key=lambda c: c.column)
    return [c.text.strip() for c in cells]


def _stitch_structured(
    a_td: TableData,
    b_td: TableData,
    *,
    a_page: int,
    b_page: int,
    repeated_header: bool,
) -> TableData:
    """Combine two TableData objects into one stitched table.

    b's rows are reindexed starting at a.row_count. When repeated_header is True,
    b's row 0 (the duplicate column header) is omitted.
    """
    b_start_row = 1 if repeated_header else 0
    row_offset = a_td.row_count

    combined: list[TableCell] = list(a_td.cells)
    for cell in b_td.cells:
        if cell.row < b_start_row:
            continue
        new_row = cell.row - b_start_row + row_offset
        combined.append(cell.model_copy(update={"row": new_row, "id": ""}))

    new_row_count = row_offset + (b_td.row_count - b_start_row)

    metadata: dict = {
        **{k: v for k, v in (a_td.metadata or {}).items()
           if k not in ("source_pages", "page_row_ranges", "source_table_methods",
                        "repeated_header_removed", "stitching_confidence")},
        "source_pages": [a_page, b_page],
        "source_table_methods": [
            str(a_td.extraction_method or ""),
            str(b_td.extraction_method or ""),
        ],
        "page_row_ranges": [
            {"page": a_page, "row_start": 0, "row_end": row_offset - 1},
            {"page": b_page, "row_start": row_offset,
             "row_end": new_row_count - 1},
        ],
        "repeated_header_removed": repeated_header,
        "stitching_confidence": "inferred",
    }

    return TableData(
        row_count=new_row_count,
        column_count=a_td.column_count,
        cells=combined,
        header_rows=list(a_td.header_rows),
        header_detection=a_td.header_detection,
        span_detection="unsupported",
        bbox=a_td.bbox,
        page=a_td.page,
        extraction_method=ExtractionMethod.PDF_STITCHED,
        metadata=metadata,
    )


# ── Public entry point ─────────────────────────────────────────────────────────

def stitch_page_break_tables(
    blocks: list[Block],
    page_heights: dict[int, float],
    edge_tolerance: float = 30.0,
) -> list[Block]:
    """Merge TABLE blocks that are continuations of a table split by a page break.

    Structured blocks (block.table_data is not None) are combined using TableData
    stitching. Legacy blocks (block.table_data is None, e.g. from Marker) fall back
    to Markdown string stitching.

    Two detection cases (both require table a on page N, table b on page N+1):

    Case 1 — repeated header: table b's first row is identical to table a's first row.
    Case 2 — spatial adjacency: table a ends within edge_tolerance pts of page N bottom
              AND table b starts within edge_tolerance pts of page N+1 top AND column
              counts match (requires table_bbox metadata on both blocks).
    """
    if len(blocks) < 2:
        return blocks

    changed = True
    while changed:
        changed = False
        result: list[Block] = []
        absorbed: set[int] = set()

        for i, a in enumerate(blocks):
            if i in absorbed:
                continue
            if a.type != BlockType.TABLE or a.page is None:
                result.append(a)
                continue

            merged = False
            for j in range(i + 1, len(blocks)):
                if j in absorbed:
                    continue
                b = blocks[j]
                if b.type != BlockType.TABLE:
                    continue
                if b.page != a.page + 1:
                    break

                # Dispatch: structured vs legacy
                if a.table_data is not None and b.table_data is not None:
                    merged_block = _try_stitch_structured(
                        a, b, page_heights, edge_tolerance
                    )
                else:
                    merged_block = _try_stitch_legacy(
                        a, b, page_heights, edge_tolerance
                    )

                if merged_block is not None:
                    result.append(merged_block)
                    absorbed.add(j)
                    changed = True
                    merged = True
                break

            if not merged:
                result.append(a)

        blocks = result

    return blocks


def _try_stitch_structured(
    a: Block,
    b: Block,
    page_heights: dict[int, float],
    edge_tolerance: float,
) -> Block | None:
    a_td = a.table_data
    b_td = b.table_data
    assert a_td is not None and b_td is not None

    if a_td.column_count != b_td.column_count:
        return None

    a_texts = _header_texts(a_td)
    b_texts = _header_texts(b_td)
    repeated = bool(a_texts and b_texts and a_texts == b_texts)

    if not repeated:
        # Case 2: spatial check
        a_bbox = a_td.bbox or _BoundingBoxFromMeta(a.metadata.get("table_bbox"))
        b_bbox = b_td.bbox or _BoundingBoxFromMeta(b.metadata.get("table_bbox"))
        if a_bbox is None or b_bbox is None:
            return None
        a_height = page_heights.get(a.page, 0.0)
        if a_height <= 0 or (a_height - a_bbox.y1) > edge_tolerance:
            return None
        if b_bbox.y0 > edge_tolerance:
            return None

    stitched_td = _stitch_structured(
        a_td, b_td,
        a_page=a.page,
        b_page=b.page,
        repeated_header=repeated,
    )

    # Preserve table_bbox metadata from a (for potential further stitching rounds)
    meta = {**a.metadata}
    if stitched_td.bbox is not None:
        meta["table_bbox"] = (
            stitched_td.bbox.x0, stitched_td.bbox.y0,
            stitched_td.bbox.x1, stitched_td.bbox.y1,
        )

    return Block.from_table(
        stitched_td,
        page=a.page,
        index=a.index,
        confidence=a.confidence,
        metadata=meta,
    )


def _BoundingBoxFromMeta(bbox_tuple: tuple | None):
    """Convert a (x0,y0,x1,y1) metadata tuple to a simple object with .y0/.y1."""
    if bbox_tuple is None:
        return None
    return _SimpleBBox(*bbox_tuple)


class _SimpleBBox:
    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, x0, y0, x1, y1):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


def _try_stitch_legacy(
    a: Block,
    b: Block,
    page_heights: dict[int, float],
    edge_tolerance: float,
) -> Block | None:
    """Markdown string stitching for legacy blocks (e.g. from Marker).

    Known unsupported: the Marker path (_parse_marker_markdown -> flush_table)
    creates Block(type=TABLE, content=md) without table_data. Those blocks are
    handled here via Markdown string manipulation. Migrating Marker to structured
    TableData is deferred (Phase 4 Milestone 4+).
    """
    a_hdr = _tbl_header_line(a.content)
    b_hdr = _tbl_header_line(b.content)
    repeated = bool(a_hdr and b_hdr and a_hdr == b_hdr)

    if not repeated:
        a_bbox = a.metadata.get("table_bbox")
        b_bbox = b.metadata.get("table_bbox")
        if not a_bbox or not b_bbox:
            return None
        a_height = page_heights.get(a.page, 0.0)
        if a_height <= 0 or (a_height - a_bbox[3]) > edge_tolerance:
            return None
        if b_bbox[1] > edge_tolerance:
            return None
        if _tbl_col_count(a_hdr) == 0 or _tbl_col_count(a_hdr) != _tbl_col_count(b_hdr):
            return None

    rows = _tbl_continuation_rows(b.content, a_hdr)
    if not rows:
        return None
    return a.model_copy(update={
        "content": a.content + "\n" + "\n".join(rows),
    })
