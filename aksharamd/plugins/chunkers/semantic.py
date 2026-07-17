from __future__ import annotations

import logging

from ...context import CompilationContext
from ...models.block import Block, BlockType, ExtractionConfidence
from ...models.chunk import Chunk
from ...renderers.table_markdown import render_row_range
from ...utils import count_tokens
from ..base import ChunkerPlugin
from ..registry import register_plugin
from .table_splitter import make_table_chunk_meta, split_table_into_ranges

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 512
_DEFAULT_MIN_TOKENS = 50


def conf_summary_for_block(block: Block) -> dict:
    summary: dict = {
        ExtractionConfidence.EXTRACTED: {"count": 0, "block_ids": []},
        ExtractionConfidence.INFERRED:  {"count": 0, "block_ids": []},
        ExtractionConfidence.AMBIGUOUS: {"count": 0, "block_ids": []},
    }
    entry = summary.get(block.confidence)
    if entry is not None:
        entry["count"] += 1
        entry["block_ids"].append(block.id)
    return summary


def _block_to_markdown(block: Block) -> str:
    if block.type == BlockType.HEADING:
        prefix = "#" * (block.level or 1)
        return f"{prefix} {block.content}"
    elif block.type == BlockType.CODE_BLOCK:
        lang = block.language or ""
        return f"```{lang}\n{block.content}\n```"
    elif block.type == BlockType.TABLE:
        return block.content
    elif block.type == BlockType.KEY_VALUE_GROUP:
        return block.content  # content already rendered by _compute_derived
    elif block.type == BlockType.LIST:
        return block.content
    elif block.type == BlockType.BLOCKQUOTE:
        return f"> {block.content}"
    elif block.type == BlockType.ADMONITION:
        kind = block.metadata.get("admonition_type", "note").upper()
        return f"> **{kind}**: {block.content}"
    elif block.type == BlockType.IMAGE:
        return f"![{block.content}]"
    else:
        return block.content


class SemanticChunker(ChunkerPlugin):
    name = "semantic_chunker"
    priority = 40
    max_tokens: int = _DEFAULT_MAX_TOKENS
    min_tokens: int = _DEFAULT_MIN_TOKENS
    overlap_tokens: int = 0

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        min_tokens: int = _DEFAULT_MIN_TOKENS,
        overlap_tokens: int = 0,
    ) -> None:
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self.overlap_tokens = overlap_tokens

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        blocks = ctx.document.blocks
        chunks: list[Chunk] = []
        chunk_index = 0

        current_blocks: list[Block] = []
        current_heading: str | None = None
        current_tokens = 0

        doc_id = ctx.document.document_id if ctx.document else ""

        def flush(
            blocks_to_flush: list[Block], heading: str | None
        ) -> tuple[list[Block], int]:
            nonlocal chunk_index
            if not blocks_to_flush:
                return [], 0
            content = "\n\n".join(_block_to_markdown(b) for b in blocks_to_flush)
            token_count = count_tokens(content)
            pages = [b.page for b in blocks_to_flush if b.page is not None]
            conf_summary: dict[str, dict] = {
                ExtractionConfidence.EXTRACTED: {"count": 0, "block_ids": []},
                ExtractionConfidence.INFERRED:  {"count": 0, "block_ids": []},
                ExtractionConfidence.AMBIGUOUS: {"count": 0, "block_ids": []},
            }
            for b in blocks_to_flush:
                entry = conf_summary.get(b.confidence)
                if entry is not None:
                    entry["count"] += 1
                    entry["block_ids"].append(b.id)
            chunk = Chunk(
                document_id=doc_id,
                index=chunk_index,
                heading=heading,
                content=content,
                token_count=token_count,
                block_ids=[b.id for b in blocks_to_flush],
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                confidence_summary=conf_summary,
            )
            chunk.compute_id()
            chunks.append(chunk)
            chunk_index += 1

            # Carry the tail of this chunk into the next as overlap context.
            # Overlap is block-granular: walk backwards collecting whole blocks
            # until overlap_tokens budget is reached.
            if self.overlap_tokens > 0:
                tail: list[Block] = []
                tail_tokens = 0
                for block in reversed(blocks_to_flush):
                    bt = count_tokens(_block_to_markdown(block))
                    if tail_tokens + bt > self.overlap_tokens:
                        break
                    tail.insert(0, block)
                    tail_tokens += bt
                return tail, tail_tokens
            return [], 0

        for block in blocks:
            if block.type == BlockType.HEADING:
                # Heading marks a section boundary — flush without overlap so the
                # new section starts clean.
                if current_blocks:
                    flush(current_blocks, current_heading)
                    current_blocks = []
                    current_tokens = 0
                current_heading = block.content
                current_blocks.append(block)
                current_tokens += count_tokens(_block_to_markdown(block))
            elif block.type == BlockType.TABLE and block.table_data is not None:
                # Structured table — flush preceding mixed-content blocks, then
                # emit one or more row-range chunks (never interleaved with other blocks).
                if current_blocks:
                    flush(current_blocks, current_heading)
                    current_blocks = []
                    current_tokens = 0
                ranges = split_table_into_ranges(block.table_data, self.max_tokens)
                for plan in ranges:
                    content = render_row_range(block.table_data, plan.row_start, plan.row_end)
                    token_count = count_tokens(content)
                    pages = [block.page] if block.page is not None else []
                    conf_entry = conf_summary_for_block(block)
                    chunk = Chunk(
                        document_id=doc_id,
                        index=chunk_index,
                        heading=current_heading,
                        content=content,
                        token_count=token_count,
                        block_ids=[block.id],
                        page_start=min(pages) if pages else None,
                        page_end=max(pages) if pages else None,
                        confidence_summary=conf_entry,
                        metadata=make_table_chunk_meta(
                            block, plan.row_start, plan.row_end,
                            plan=plan,
                            chunk_budget_tokens=self.max_tokens,
                        ),
                    )
                    chunk.compute_id()
                    chunks.append(chunk)
                    chunk_index += 1
            elif block.type == BlockType.KEY_VALUE_GROUP and block.key_value_group is not None:
                # Flush preceding mixed-content blocks first
                if current_blocks:
                    flush(current_blocks, current_heading)
                    current_blocks = []
                    current_tokens = 0

                # Render as compact text
                content = _block_to_markdown(block)
                token_count = count_tokens(content)
                pages = [block.page] if block.page is not None else []
                conf_entry = conf_summary_for_block(block)

                kv_group = block.key_value_group
                entry_count = len(kv_group.entries)

                # Count records (repeated keys indicate multiple records)
                seen_k: set[str] = set()
                rec_count = 1
                for e in kv_group.entries:
                    if e.key in seen_k:
                        rec_count += 1
                        seen_k = {e.key}
                    else:
                        seen_k.add(e.key)

                kv_meta = {
                    "content_type": "key_value_group",
                    "key_value_group_id": block.id,
                    "record_start": 0,
                    "record_end": rec_count - 1,
                    "entry_count": entry_count,
                    "group_type": str(kv_group.group_type),
                    "source_block_ids": list(kv_group.source_block_ids),
                }

                chunk = Chunk(
                    document_id=doc_id,
                    index=chunk_index,
                    heading=current_heading,
                    content=content,
                    token_count=token_count,
                    block_ids=[block.id],
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    confidence_summary=conf_entry,
                    metadata=kv_meta,
                )
                chunk.compute_id()
                chunks.append(chunk)
                chunk_index += 1
            else:
                block_tokens = count_tokens(_block_to_markdown(block))
                if current_tokens + block_tokens > self.max_tokens and current_tokens >= self.min_tokens:
                    # Token budget exceeded mid-section — carry overlap into next chunk.
                    overlap_blocks, overlap_token_count = flush(current_blocks, current_heading)
                    current_blocks = list(overlap_blocks) + [block]
                    current_tokens = overlap_token_count + block_tokens
                else:
                    current_blocks.append(block)
                    current_tokens += block_tokens

        flush(current_blocks, current_heading)
        ctx.chunks = chunks
        return ctx


register_plugin(SemanticChunker)
