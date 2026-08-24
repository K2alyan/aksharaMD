"""Table-expectation validator.

Detects pages where a table strategy found candidates but the quality filter
rejected them, then cross-references with text signals (captions, numeric
alignment) to determine whether a table was expected on that page.

Emits W_TABLE_EXPECTED_NOT_EXTRACTED for pages where expected="true" and no
table block was extracted.

All findings are maturity="experimental" and carry no readiness-score penalty.
"""
from __future__ import annotations

from ...context import CompilationContext
from ...models.block import BlockType
from ...scoring.source_profile import SourceProfile
from ...scoring.table_expectation import RejectedTableCandidate, compute_table_expectation
from ..base import ValidatorPlugin
from ..registry import register_plugin


class TableExpectationValidator(ValidatorPlugin):
    name = "table_expectation_validator"
    priority = 30   # after table_quality (28), before header_footer_table (36)
    maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document

        # Read via the neutral scoring contract when the PdfBlockTreeAdapter
        # populated it (BLOCK_TREE_CONTRACT_DESIGN.md §3.1 / §3.2), else fall
        # back to the raw pdf.py metadata keys with identical defaults.
        # Backward-compat shim per §5.3 — retire the else branches once every
        # parser adapter is in place.
        #
        # Option (b) migration: the neutral contract stores typed
        # RejectedTableCandidate instances; we materialize back to raw dicts
        # at the boundary so compute_table_expectation's existing list[dict]
        # signature and .get("row_count") / .get("col_count") substantiality
        # guard remain BYTE-IDENTICAL. Signature migration to typed input
        # (design option a) is a separate follow-up PR.
        neutral_rejected = doc.metadata.get("rejected_table_candidates_by_page")
        if isinstance(neutral_rejected, dict):
            rejected_by_page_dicts: dict = {
                pg: [
                    (c.model_dump() if isinstance(c, RejectedTableCandidate) else c)
                    for c in (candidates or [])
                ]
                for pg, candidates in neutral_rejected.items()
            }
        else:
            # Legacy path — parser did not populate the neutral contract.
            rejected_by_page_dicts = doc.metadata.get(
                "table_rejected_candidates_by_page", {}
            )

        sp = doc.metadata.get("source_profile")
        if isinstance(sp, SourceProfile):
            doc_type: str | None = sp.document_type_hint
        else:
            doc_type = doc.metadata.get("pdf_classification")

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
        pages_expected_not_extracted: list[int] = []

        for page_num, page_blocks in sorted(blocks_by_page.items()):
            # Support both int and str keys in rejected_by_page_dicts
            rejected = (
                rejected_by_page_dicts.get(page_num)
                or rejected_by_page_dicts.get(str(page_num), [])
            )

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
                pages_expected_not_extracted.append(page_num)
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

        # Summary diagnostics dict for cap consumers (readiness.py). Uses
        # the same shape as other validator diagnostics dicts — carrying a
        # top-level warning_maturity field so the cap wiring can gradate its
        # cap value per the maturity-aware pattern.
        doc.metadata["table_expectation_diagnostics"] = {
            "pages_expected_not_extracted": pages_expected_not_extracted,
            "warned": bool(pages_expected_not_extracted),
            "warning_maturity": self.maturity,
        }

        return ctx


register_plugin(TableExpectationValidator)
