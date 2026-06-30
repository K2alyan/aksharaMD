from __future__ import annotations
import json
from pathlib import Path

from ..base import ExporterPlugin
from ..registry import register_plugin
from ...context import CompilationContext
from ...models.block import Block, BlockType


def _block_to_md(block: Block) -> str:
    if block.type == BlockType.HEADING:
        return f"{'#' * (block.level or 1)} {block.content}"
    elif block.type == BlockType.CODE_BLOCK:
        lang = block.language or ""
        return f"```{lang}\n{block.content}\n```"
    elif block.type == BlockType.TABLE:
        return block.content
    elif block.type == BlockType.LIST:
        return block.content
    elif block.type == BlockType.BLOCKQUOTE:
        lines = block.content.splitlines()
        return "\n".join(f"> {line}" for line in lines)
    elif block.type == BlockType.IMAGE:
        label = block.content or block.metadata.get("src", "Image")
        return f"![{label}]"
    elif block.type == BlockType.PAGE_BREAK:
        return "---"
    else:
        return block.content


class MarkdownExporter(ExporterPlugin):
    name = "markdown_exporter"
    priority = 90

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        out = Path(ctx.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        lines = []
        for block in ctx.document.blocks:
            md = _block_to_md(block)
            if md:
                lines.append(md)

        content = "\n\n".join(lines)
        (out / "document.md").write_text(content, encoding="utf-8")
        return ctx


register_plugin(MarkdownExporter)
