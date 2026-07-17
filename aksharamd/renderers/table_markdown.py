from __future__ import annotations

from ..models.table import TableData


def render_table_markdown(table: TableData) -> str:
    """Render TableData to deterministic GFM pipe-delimited Markdown.

    Policy:
    - Rows ordered by row index (ascending).
    - Columns ordered by column index (ascending).
    - Separator always emitted after row 0, regardless of header_detection.
      When header_detection='none', the semantic truth (header_rows=[]) is preserved
      in table_data; the separator is a compatibility artifact for valid GFM.
    - Pipe chars in cell text escaped as \\|.
    - Newlines in cell text replaced with space.
    - Empty cell text (text="") renders as no text between pipes (|  |).
    - Span-covered positions rendered as single-space cells (|   |) to preserve column count.
    - Missing extraction positions also rendered as single-space cells.
    """
    if table.row_count == 0 or table.column_count == 0:
        return ""

    # Build display grid: (row, col) -> rendered text
    grid: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        rendered = _render_cell_text(cell.text)
        grid[(cell.row, cell.column)] = rendered

    lines: list[str] = []
    for r in range(table.row_count):
        row_parts: list[str] = []
        for c in range(table.column_count):
            text = grid.get((r, c), " ")   # missing/covered -> single space
            row_parts.append(text)
        lines.append("| " + " | ".join(row_parts) + " |")
        if r == 0:
            sep_parts = ["---"] * table.column_count
            lines.append("| " + " | ".join(sep_parts) + " |")

    return "\n".join(lines)


def render_row_range(table: TableData, row_start: int, row_end: int) -> str:
    """Render a row range [row_start, row_end] inclusive.

    Body-only ranges (row_start > last header row) get header rows prepended
    automatically, with the separator emitted after the last header row.
    Used by the table-aware chunker to render table sub-ranges for embedding.
    """
    if table.row_count == 0 or table.column_count == 0:
        return ""
    row_start = max(0, row_start)
    row_end = min(table.row_count - 1, row_end)
    if row_start > row_end:
        return ""

    header_rows_sorted = sorted(table.header_rows) if table.header_rows else [0]
    last_header = header_rows_sorted[-1]

    # Prepend header for body-only ranges
    if row_start > last_header:
        rows_to_render = header_rows_sorted + list(range(row_start, row_end + 1))
    else:
        rows_to_render = list(range(row_start, row_end + 1))

    grid: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        grid[(cell.row, cell.column)] = _render_cell_text(cell.text)

    lines: list[str] = []
    for r in rows_to_render:
        row_parts = [grid.get((r, c), " ") for c in range(table.column_count)]
        lines.append("| " + " | ".join(row_parts) + " |")
        if r == last_header:
            lines.append("| " + " | ".join(["---"] * table.column_count) + " |")

    return "\n".join(lines)


def _render_cell_text(text: str) -> str:
    """Prepare cell text for pipe-table rendering."""
    # Normalize newlines to space
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    # Escape pipe characters
    text = text.replace("|", r"\|")
    return text


def _tsv_cell_text(text: str) -> str:
    """Prepare cell text for TSV rendering: replace tabs and newlines with space."""
    text = text.replace("\t", " ")
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text


def render_table_tsv(table: TableData) -> str:
    """Tab-separated values. One row per line. Header rows first.

    Escape tabs in cell text as a space. Escape newlines as space.
    Empty cell: empty string between tabs. Missing/covered: single space.
    Preserve header rows in order.
    """
    if table.row_count == 0 or table.column_count == 0:
        return ""

    # Build display grid
    grid: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        grid[(cell.row, cell.column)] = _tsv_cell_text(cell.text)

    # Determine row order: header rows first, then body rows
    header_rows_sorted = sorted(table.header_rows) if table.header_rows else []
    header_set = set(header_rows_sorted)
    body_rows = [r for r in range(table.row_count) if r not in header_set]
    row_order = header_rows_sorted + body_rows

    lines: list[str] = []
    for r in row_order:
        row_parts: list[str] = []
        for c in range(table.column_count):
            text = grid.get((r, c), " ")  # missing/covered -> single space
            row_parts.append(text)
        lines.append("\t".join(row_parts))

    return "\n".join(lines)


def render_table_row_records(table: TableData) -> str:
    """ColName=value; ColName=value format. One logical data row per line.

    Only usable when header_rows is non-empty and column_count <= 12.
    Resolve duplicate column names by appending _1, _2 etc.
    Empty/missing cell value: (empty string after =).
    Skip header rows themselves; only emit body rows.
    Return empty string if no headers or table is too wide.
    """
    if not table.header_rows or table.column_count > 12:
        return ""
    if table.row_count == 0 or table.column_count == 0:
        return ""

    # Build display grid
    grid: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        grid[(cell.row, cell.column)] = _tsv_cell_text(cell.text)

    # Collect header names from the last header row
    last_header_row = max(table.header_rows)
    raw_names: list[str] = []
    for c in range(table.column_count):
        raw_names.append(grid.get((last_header_row, c), "").strip())

    # Deduplicate column names
    col_names: list[str] = []
    seen_counts: dict[str, int] = {}
    for name in raw_names:
        if name in seen_counts:
            seen_counts[name] += 1
            col_names.append(f"{name}_{seen_counts[name]}")
        else:
            seen_counts[name] = 0
            col_names.append(name)

    # Fix: if a name was seen more than once, rename the first occurrence too
    # Re-do with correct dedup: track first occurrence position
    col_names = []
    name_positions: dict[str, list[int]] = {}
    for i, name in enumerate(raw_names):
        if name not in name_positions:
            name_positions[name] = []
        name_positions[name].append(i)

    final_names = [""] * table.column_count
    for name, positions in name_positions.items():
        if len(positions) == 1:
            final_names[positions[0]] = name
        else:
            for idx, pos in enumerate(positions):
                final_names[pos] = f"{name}_{idx + 1}"
    col_names = final_names

    # Determine body rows (exclude all header rows)
    header_set = set(table.header_rows)
    body_rows = [r for r in range(table.row_count) if r not in header_set]

    if not body_rows:
        return ""

    lines: list[str] = []
    for r in body_rows:
        parts: list[str] = []
        for c in range(table.column_count):
            val = grid.get((r, c), "")
            if val == " ":  # missing/covered cell
                val = ""
            parts.append(f"{col_names[c]}={val}")
        lines.append("; ".join(parts))

    return "\n".join(lines)


def render_table_preview_reference(
    table: TableData,
    table_id: str,
    artifact_path: "str | None",
    preview_rows: int = 5,
    title: "str | None" = None,
) -> str:
    """Preview + artifact reference.

    Format:
      Table: <title or 'Untitled'>
      Rows: <row_count - len(header_rows)>
      Columns: <comma-separated header names or Col1,Col2,...>

      <TSV preview of first preview_rows body rows (tab-separated, no trailing tab)>

      [<omitted_count> additional rows omitted. Full structured table: <artifact_path or 'unavailable'>]

    If all body rows fit in preview_rows, omit the final bracketed line.
    If artifact_path is None: use 'unavailable' in the bracket.
    """
    if table.row_count == 0 or table.column_count == 0:
        return f"[Table {table_id}: empty]"

    header_count = len(table.header_rows) if table.header_rows else 0
    body_row_count = max(0, table.row_count - header_count)
    ap = artifact_path or "unavailable"
    display_title = title or "Untitled"

    # Build grid
    grid: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        grid[(cell.row, cell.column)] = _tsv_cell_text(cell.text)

    # Column names
    header_rows_sorted = sorted(table.header_rows) if table.header_rows else []
    if header_rows_sorted:
        last_header_row = max(header_rows_sorted)
        col_names = [grid.get((last_header_row, c), f"Col{c + 1}").strip() or f"Col{c + 1}"
                     for c in range(table.column_count)]
    else:
        col_names = [f"Col{c + 1}" for c in range(table.column_count)]

    header_set = set(table.header_rows)
    body_rows = [r for r in range(table.row_count) if r not in header_set]
    preview_body = body_rows[:preview_rows]
    omitted_count = max(0, len(body_rows) - preview_rows)

    lines: list[str] = [
        f"Table: {display_title}",
        f"Rows: {body_row_count}",
        f"Columns: {', '.join(col_names)}",
        "",
    ]

    for r in preview_body:
        row_parts = [grid.get((r, c), " ") for c in range(table.column_count)]
        lines.append("\t".join(row_parts))

    if omitted_count > 0:
        lines.append("")
        lines.append(f"[{omitted_count} additional rows omitted. Full structured table: {ap}]")

    return "\n".join(lines)


def render_table_json_reference(
    table: TableData,
    table_id: str,
    artifact_path: "str | None",
) -> str:
    """Compact reference only. No inline data.

    Format: [Table <table_id>: <body_row_count> rows x <column_count> columns. Full data: <artifact_path or 'unavailable'>]
    """
    header_count = len(table.header_rows) if table.header_rows else 0
    body_row_count = max(0, table.row_count - header_count)
    ap = artifact_path or "unavailable"
    return f"[Table {table_id}: {body_row_count} rows x {table.column_count} columns. Full data: {ap}]"
