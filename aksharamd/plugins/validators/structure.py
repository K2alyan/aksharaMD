from __future__ import annotations

from ...context import CompilationContext
from ...models.block import BlockType
from ..base import ValidatorPlugin
from ..registry import register_plugin


class StructureValidator(ValidatorPlugin):
    name = "structure_validator"
    priority = 30

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            ctx.error("NO_DOCUMENT", "No document was produced by the parser")
            return ctx

        doc = ctx.document
        blocks = doc.blocks

        if not blocks:
            ctx.warn("EMPTY_DOCUMENT", "Document contains no blocks")
            return ctx

        # Check heading hierarchy
        headings = [b for b in blocks if b.type == BlockType.HEADING]
        if headings:
            levels = [h.level for h in headings if h.level is not None]
            if levels and levels[0] > 2:
                ctx.warn("HEADING_HIERARCHY", f"Document starts at heading level {levels[0]}, expected 1 or 2")

            prev_level = 0
            for h in headings:
                if h.level and h.level > prev_level + 1 and prev_level > 0:
                    ctx.warn(
                        "HEADING_SKIP",
                        f"Heading level jumped from {prev_level} to {h.level}: '{h.content[:60]}'",
                        page=h.page,
                        block_id=h.id,
                    )
                if h.level:
                    prev_level = h.level

        # Check for very long blocks (likely a merge/parse failure)
        for block in blocks:
            if len(block.content) > 10_000:
                ctx.warn(
                    "LARGE_BLOCK",
                    f"Block {block.id} is unusually large ({len(block.content)} chars)",
                    page=block.page,
                    block_id=block.id,
                )

        # Check for empty content blocks
        for block in blocks:
            if not block.content.strip():
                ctx.warn("EMPTY_BLOCK", f"Block {block.id} has empty content", block_id=block.id)

        # If PDF: check for missing page ranges
        if doc.file_type == "pdf" and doc.pages > 0:
            pages_with_content = {b.page for b in blocks if b.page is not None}
            for p in range(1, doc.pages + 1):
                if p not in pages_with_content:
                    ctx.warn("MISSING_PAGE", f"Page {p} has no extracted content", page=p)

        return ctx


register_plugin(StructureValidator)
