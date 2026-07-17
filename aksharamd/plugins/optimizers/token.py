from __future__ import annotations

import re

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...utils import count_tokens
from ..base import OptimizerPlugin
from ..registry import register_plugin

# Matches numbered section headings: "1", "1.5", "1.5.1", "Section 1.5.1", "A.2", "IV."
_NUMBERED_SECTION_RE = re.compile(
    r'^(?:(?:Section|Appendix|Chapter|Article)\s+)?(?:\d+(?:\.\d+)*\.?\s|[IVXivx]+\.?\s|[A-Z]\.\d)'
)

_MIN_MERGE_LEN = 60   # paragraphs shorter than this are candidates for merging
_MAX_MERGE_LEN = 300  # don't merge if combined result exceeds this


def _remove_duplicates(blocks: list[Block]) -> tuple[list[Block], int]:
    seen: set[str] = set()
    result = []
    removed = 0
    for block in blocks:
        if block.type == BlockType.IMAGE:
            result.append(block)  # images are never duplicates — different charts, same empty content
        elif block.type == BlockType.KEY_VALUE_GROUP:
            result.append(block)  # KEY_VALUE_GROUP blocks are structured — never deduplicate
        elif block.checksum in seen:
            removed += 1
        else:
            seen.add(block.checksum)
            result.append(block)
    return result, removed


def _detect_repeated_headers_footers(blocks: list[Block], total_pages: int) -> tuple[set[str], set[str]]:
    """Return (header_checksums, footer_checksums) for blocks repeated across pages."""
    if total_pages < 3:
        return set(), set()

    threshold = max(2, int(total_pages * 0.4))

    # Group by page
    by_page: dict[int, list[Block]] = {}
    for b in blocks:
        if b.page is not None:
            by_page.setdefault(b.page, []).append(b)

    checksum_pages: dict[str, set[int]] = {}
    for page, pblocks in by_page.items():
        for b in pblocks:
            checksum_pages.setdefault(b.checksum, set()).add(page)

    repeated = {cs for cs, pages in checksum_pages.items() if len(pages) >= threshold}

    # Classify header vs footer by position (first vs last block on page)
    headers: set[str] = set()
    footers: set[str] = set()
    for page, pblocks in by_page.items():
        if not pblocks:
            continue
        first_cs = pblocks[0].checksum
        last_cs = pblocks[-1].checksum
        if first_cs in repeated:
            headers.add(first_cs)
        if last_cs in repeated:
            footers.add(last_cs)

    return headers, footers


def _merge_fragmented_headings(blocks: list[Block]) -> list[Block]:
    """
    Merge chains of same-level headings on the same page that are clearly
    split title words (e.g. PDF cover page with each word on a separate line).
    Only merges when every part is short and the combined result is under 150 chars.
    """
    import hashlib
    result = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.type == BlockType.HEADING:
            chain = [block]
            j = i + 1
            while (
                j < len(blocks)
                and blocks[j].type == BlockType.HEADING
                and blocks[j].level == block.level
                and blocks[j].page == block.page
            ):
                chain.append(blocks[j])
                j += 1

            if len(chain) > 1:
                combined = " ".join(b.content for b in chain)
                if all(len(b.content) < 60 for b in chain) and len(combined) < 150:
                    cs = hashlib.sha256(combined.encode()).hexdigest()[:16]
                    result.append(block.model_copy(update={"content": combined, "checksum": cs, "id": cs}))
                    i = j
                    continue

            result.append(block)
            i += 1
        else:
            result.append(block)
            i += 1
    return result


def _merge_fragments(blocks: list[Block]) -> list[Block]:
    """Merge consecutive short paragraphs on the same page into one.

    Only applies to blocks with explicit page numbers (PDFs). Blocks without
    page numbers (email, HTML, etc.) already have intentional paragraph breaks.
    """
    result: list[Block] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if (
            block.type == BlockType.PARAGRAPH
            and block.page is not None
            and len(block.content) < _MIN_MERGE_LEN
            and i + 1 < len(blocks)
            and blocks[i + 1].type == BlockType.PARAGRAPH
            and blocks[i + 1].page == block.page
        ):
            combined = block.content + " " + blocks[i + 1].content
            if len(combined) <= _MAX_MERGE_LEN:
                merged = block.model_copy(update={"content": combined, "checksum": ""})
                # recompute checksum
                import hashlib
                merged = merged.model_copy(update={
                    "checksum": hashlib.sha256(combined.encode()).hexdigest()[:16]
                })
                result.append(merged)
                i += 2
                continue
        result.append(block)
        i += 1
    return result


class TokenOptimizer(OptimizerPlugin):
    name = "token_optimizer"
    priority = 20

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        blocks = ctx.document.blocks
        total_text = " ".join(b.content for b in blocks)
        ctx.original_tokens = count_tokens(total_text)

        # Remove exact duplicates
        blocks, dups_removed = _remove_duplicates(blocks)
        ctx.duplicate_blocks_removed += dups_removed

        # Remove repeated headers/footers — but never remove numbered section headings
        headers, footers = _detect_repeated_headers_footers(blocks, ctx.document.pages)
        filtered = []
        headers_removed = 0
        footers_removed = 0
        for b in blocks:
            # KEY_VALUE_GROUP blocks are structured — never remove as page furniture
            if b.type == BlockType.KEY_VALUE_GROUP:
                filtered.append(b)
            elif b.checksum in headers or b.checksum in footers:
                if (
                    b.type == BlockType.HEADING
                    and bool(_NUMBERED_SECTION_RE.match(b.content))
                ):
                    # Conservative: preserve numbered section headings regardless of repetition
                    filtered.append(b)
                elif b.checksum in headers:
                    headers_removed += 1
                else:
                    footers_removed += 1
            else:
                filtered.append(b)
        ctx.headers_removed += headers_removed
        ctx.footers_removed += footers_removed
        blocks = filtered

        # Merge fragmented headings (e.g. PDF cover title split across lines)
        blocks = _merge_fragmented_headings(blocks)

        # Merge short fragments
        blocks = _merge_fragments(blocks)

        # Re-index
        for i, b in enumerate(blocks):
            blocks[i] = b.model_copy(update={"index": i})

        ctx.document = ctx.document.model_copy(update={"blocks": blocks})
        return ctx


register_plugin(TokenOptimizer)
