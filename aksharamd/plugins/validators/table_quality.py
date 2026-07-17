"""Table-quality diagnostics validator.

Computes structured TableQualityReport for every TABLE block that carries
table_data, stores the report in block.metadata["table_quality"] and
accumulates all reports in ctx.document.metadata["table_quality_reports"].

This validator emits NO warnings and changes NO readiness scores during
Milestone 5. All findings are experimental and diagnostic only.

Warning reconciliation (informational):
  - COL_GENERIC_TABLES (readiness.py): backed by the generic_header_count
    signal. The existing deduction uses Markdown text parsing; the new signal
    uses structured TableData. They may diverge on edge cases. Deferred.
  - W_HEADER_FOOTER_TABLE_GARBLED (header_footer_table.py): backed by the
    table_near_top_margin / table_near_bottom_margin + short_cell_fraction
    signals. The existing validator requires page_height from pdf_column_info;
    the new geometry signals use the same source. Deferred.
"""
from __future__ import annotations

from ...context import CompilationContext
from ...models.block import BlockType
from ...scoring.table_quality import compute_table_quality
from ..base import ValidatorPlugin
from ..registry import register_plugin


class TableQualityValidator(ValidatorPlugin):
    name = "table_quality_validator"
    priority = 28   # before header_footer_table (36) and multicolumn (varies)
    maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document
        col_info_raw: dict = doc.metadata.get("pdf_column_info", {})
        all_reports: list[dict] = []

        for block in doc.blocks:
            if block.type != BlockType.TABLE or block.table_data is None:
                continue

            pg = block.page or 0
            col_info = col_info_raw.get(pg) or col_info_raw.get(str(pg), {})
            page_height: float = float(col_info.get("page_height", 0.0))
            page_width:  float = float(col_info.get("page_width", 0.0))

            report = compute_table_quality(
                block,
                page_height=page_height,
                page_width=page_width,
            )

            # Compact summary in block metadata (chunker reads this)
            block.metadata["table_quality"] = {
                "overall_status": report.overall_status,
                "maturity": report.maturity,
                "signals": [s.model_dump() for s in report.signals],
            }

            all_reports.append(report.model_dump())

        if all_reports:
            doc.metadata["table_quality_reports"] = all_reports

        return ctx


register_plugin(TableQualityValidator)
