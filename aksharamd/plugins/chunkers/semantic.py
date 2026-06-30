from __future__ import annotations

import logging

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.chunk import Chunk
from ...utils import count_tokens
from ..base import ChunkerPlugin
from ..registry import register_plugin

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 512
_DEFAULT_MIN_TOKENS = 50


def _block_to_markdown(block: Block) -> str:
    if block.type == BlockType.HEADING:
        prefix = "#" * (block.level or 1)
        return f"{prefix} {block.content}"
    elif block.type == BlockType.CODE_BLOCK:
        lang = block.language or ""
        return f"```{lang}\n{block.content}\n```"
    elif block.type == BlockType.TABLE:
        return block.content
    elif block.type == BlockType.LIST:
        return block.content
    elif block.type == BlockType.BLOCKQUOTE:
        return f"> {block.content}"
    elif block.type == BlockType.IMAGE:
        return f"![{block.content}]"
    else:
        return block.content


class SemanticChunker(ChunkerPlugin):
    name = "semantic_chunker"
    priority = 40
    max_tokens: int = _DEFAULT_MAX_TOKENS
    min_tokens: int = _DEFAULT_MIN_TOKENS

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        blocks = ctx.document.blocks
        chunks: list[Chunk] = []
        chunk_index = 0

        current_blocks: list[Block] = []
        current_heading: str | None = None
        current_tokens = 0

        def flush(blocks_to_flush: list[Block], heading: str | None) -> None:
            nonlocal chunk_index
            if not blocks_to_flush:
                return
            content = "\n\n".join(_block_to_markdown(b) for b in blocks_to_flush)
            token_count = count_tokens(content)
            pages = [b.page for b in blocks_to_flush if b.page is not None]
            chunk = Chunk(
                index=chunk_index,
                heading=heading,
                content=content,
                token_count=token_count,
                block_ids=[b.id for b in blocks_to_flush],
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
            )
            chunk.compute_id()
            chunks.append(chunk)
            chunk_index += 1

        for block in blocks:
            if block.type == BlockType.HEADING:
                # Start a new chunk on heading
                if current_blocks:
                    flush(current_blocks, current_heading)
                    current_blocks = []
                    current_tokens = 0
                current_heading = block.content
                current_blocks.append(block)
                current_tokens += count_tokens(_block_to_markdown(block))
            else:
                block_tokens = count_tokens(_block_to_markdown(block))
                if current_tokens + block_tokens > self.max_tokens and current_tokens >= self.min_tokens:
                    flush(current_blocks, current_heading)
                    current_blocks = [block]
                    current_tokens = block_tokens
                else:
                    current_blocks.append(block)
                    current_tokens += block_tokens

        flush(current_blocks, current_heading)
        ctx.chunks = chunks
        return ctx


register_plugin(SemanticChunker)
