"""Header/footer table garbling validator.

Emits W_HEADER_FOOTER_TABLE_GARBLED when a table block is detected near the
top or bottom page margin AND shows high short-cell density. The combination
suggests pdfplumber mistakenly detected page-furniture (running headers,
page numbers, column titles) as a table.

Root cause: pdfplumber's whitespace-table detection fires on the regular
horizontal spacing of multi-column running headers, producing a "table" whose
cells are word fragments (e.g., "Tokio M" | "arine" | "Holdings, Inc").

Detection approach:
  1. For each TABLE block, read its bounding box from block.metadata["table_bbox"].
  2. Check whether the table's top or bottom edge falls within the page margin
     region (top 10% or bottom 10% of page height).
  3. Parse the markdown table cells and compute the fraction with ≤6 non-whitespace
     characters. Running-header fragments are characteristically very short.
  4. Warn when BOTH signals agree: in_margin AND short_frac ≥ 0.45.

Conservative design: a single signal is never enough. A real summary table at the
top of a page may be shallow or in the margin; a real financial table may have many
short numeric cells. The combination is required.

Does NOT discard or modify the table output — reports questionable extraction
while preserving the original block and its content.

Calibration: text_multicolumns__pwc (2026-07-13)
  - table_bbox y_top = -36 (above page), in_top_margin = True
  - short_frac = 0.58 (58% of cells ≤6 chars), high_short_frac = True
  - Both signals agree → WARN ✓
  - Observational results: see benchmarks/HEADER_FOOTER_TABLE_REPORT.md
"""
from __future__ import annotations

from ..base import ValidatorPlugin
from ..registry import register_plugin
from ...context import CompilationContext
from ...models.block import BlockType

# Fraction of page height treated as top/bottom margin.
_MARGIN_FRAC: float = 0.10

# Cell character threshold for "very short" fragments.
_SHORT_CELL_CHARS: int = 6

# Fraction of cells that must be short to fire the short-density signal.
_SHORT_FRAC_THRESHOLD: float = 0.45


def _parse_table_cells(content: str) -> list[str]:
    """Return non-separator cell strings from a markdown table."""
    cells: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or all(c in "|-: " for c in stripped):
            continue
        row_cells = [c.strip() for c in stripped.split("|") if c.strip()]
        cells.extend(row_cells)
    return cells


def _analyse_table(
    block,
    page_height: float,
) -> dict:
    """
    Compute margin-proximity and short-cell-density signals for one TABLE block.

    Returns a dict with individual signal values and a 'warn' boolean.
    The table content is never modified.
    """
    result: dict = {
        "page": block.page,
        "in_top_margin": False,
        "in_bottom_margin": False,
        "in_margin": False,
        "y_top": None,
        "y_bottom": None,
        "total_cells": 0,
        "short_cells": 0,
        "short_frac": 0.0,
        "high_short_frac": False,
        "signals": [],
        "warn": False,
    }

    bbox = block.metadata.get("table_bbox")
    if bbox is None or page_height <= 0:
        return result

    _, y_top, _, y_bottom = bbox
    result["y_top"] = round(y_top, 1)
    result["y_bottom"] = round(y_bottom, 1)

    margin_pts = page_height * _MARGIN_FRAC
    in_top = y_top < margin_pts
    in_bottom = y_bottom > page_height - margin_pts
    result["in_top_margin"] = in_top
    result["in_bottom_margin"] = in_bottom
    result["in_margin"] = in_top or in_bottom

    cells = _parse_table_cells(block.content or "")
    result["total_cells"] = len(cells)
    if cells:
        short = sum(1 for c in cells if len(c.replace(" ", "")) <= _SHORT_CELL_CHARS)
        short_frac = short / len(cells)
        result["short_cells"] = short
        result["short_frac"] = round(short_frac, 2)
        result["high_short_frac"] = short_frac >= _SHORT_FRAC_THRESHOLD

    signals: list[str] = []
    if in_top:
        signals.append(f"top_margin_y_top={y_top:.0f}")
    if in_bottom:
        signals.append(f"bottom_margin_y_bottom={y_bottom:.0f}")
    if result["high_short_frac"]:
        signals.append(f"short_frac={result['short_frac']:.2f}")
    result["signals"] = signals

    # Both margin proximity AND high short-cell density must agree.
    result["warn"] = result["in_margin"] and result["high_short_frac"]
    return result


class HeaderFooterTableValidator(ValidatorPlugin):
    name = "header_footer_table_validator"
    priority = 36
    # Maturity: EXPERIMENTAL — calibrated on 1 known positive (text_multicolumns__pwc).
    # Thresholds (margin_frac=0.10, short_cell_chars=6, short_frac=0.45) were chosen to
    # fire on that single document. Generalizability to other pdfplumber over-detection
    # cases is untested. Does not affect readiness score.
    # Phase 1 re-score (2026-07-13): precision 100% (1/1), recall 100% (1/1 known positive).
    warning_maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document
        column_info_raw = doc.metadata.get("pdf_column_info", {})

        table_analyses: list[dict] = []
        problem_tables: list[dict] = []

        for block in doc.blocks:
            if block.type != BlockType.TABLE:
                continue
            if block.metadata.get("table_bbox") is None:
                continue

            pg = block.page or 0
            col_info = column_info_raw.get(pg) or column_info_raw.get(str(pg), {})
            page_height: float = col_info.get("page_height", 0.0)
            if page_height <= 0:
                continue

            analysis = _analyse_table(block, page_height)
            table_analyses.append(analysis)
            if analysis["warn"]:
                problem_tables.append(analysis)

        doc.metadata["header_footer_table_diagnostics"] = {
            "table_analyses": table_analyses,
            "problem_tables": problem_tables,
            "warned": bool(problem_tables),
            "warning_maturity": self.warning_maturity,
        }

        if not problem_tables:
            return ctx

        # Summarise for the warning message
        locations = []
        for a in problem_tables:
            loc = "top" if a["in_top_margin"] else "bottom"
            locations.append(f"p{a['page']} ({loc}, short_frac={a['short_frac']:.0%})")
        location_str = "; ".join(locations)

        ctx.warn(
            "W_HEADER_FOOTER_TABLE_GARBLED",
            (
                f"A table detected near the page header or footer appears fragmented "
                f"({location_str}). "
                "Short cell fragments may represent page furniture (running headers, "
                "page numbers, column titles) rather than a meaningful table. "
                "Table output is preserved but may not represent genuine tabular data."
            ),
        )

        return ctx


register_plugin(HeaderFooterTableValidator)
