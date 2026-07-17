"""Table-expectation validator.

Detects pages where a table strategy found candidates but the quality filter
rejected them, then cross-references with text signals (captions, numeric
alignment) to determine whether a table was expected on that page.

Emits W_TABLE_EXPECTED_NOT_EXTRACTED for pages where expected="true" and no
table block was extracted.

All findings are maturity="experimental" and carry no readiness-score penalty.
"""
from __future__ import annotations

from ..base import ValidatorPlugin
from ..registry import register_plugin
from ...context import CompilationContext
from ...models.block import BlockType
from ...scoring.table_expectation import compute_table_expectation


class TableExpectationValidator(ValidatorPlugin):
    name = "table_expectation_validator"
    priority = 30   # after table_quality (28), before header_footer_table (36)
    maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document

        # Retrieve rejected candidates accumulated during parsing (keyed by int page)
        rejected_by_page: dict = doc.metadata.get("table_rejected_candidates_by_page", {})
        doc_type: str | None = doc.metadata.get("pdf_classification")

        # Group blocks by page and collect pages that already have a table
        blocks_by_page: dict[int, list] = {}
        pages_with_tables: set[int] = set()

        for block in doc.blocks:
            page = block.page
            if page is None:
                continue
            blocks_by_page.setdefault(page, []).append(block)
            if block.type == BlockType.TABLE:
                pages_with_tables.add(page)

        reports: list[dict] = []

        for page_num, page_blocks in sorted(blocks_by_page.items()):
            # Support both int and str keys in rejected_by_page
            rejected = rejected_by_page.get(page_num) or rejected_by_page.get(str(page_num), [])

            report = compute_table_expectation(
                page=page_num,
                blocks=page_blocks,
                rejected_candidates=rejected,
                doc_type=doc_type,
            )

            # Attach extracted table block IDs for this page
            table_block_ids = [b.id for b in page_blocks if b.type == BlockType.TABLE]
            report = report.model_copy(update={"extracted_table_block_ids": table_block_ids})

            reports.append(report.model_dump())

            # Emit warning for pages where a table was expected but not extracted
            if report.expected == "true" and page_num not in pages_with_tables:
                ctx.warn(
                    "W_TABLE_EXPECTED_NOT_EXTRACTED",
                    (
                        f"Page {page_num}: table expected but not extracted "
                        f"({len(report.rejected_candidates)} rejected candidate"
                        f"{'s' if len(report.rejected_candidates) != 1 else ''})"
                    ),
                    block_id=None,
                )

        if reports:
            doc.metadata["table_expectation_reports"] = reports

        return ctx


register_plugin(TableExpectationValidator)
