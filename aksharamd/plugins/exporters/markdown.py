from __future__ import annotations

import re
from pathlib import Path

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ExporterPlugin
from ..registry import register_plugin


def _block_to_md(block: Block) -> str:
    if block.type == BlockType.HEADING:
        return f"{'#' * (block.level or 1)} {block.content}"
    elif block.type == BlockType.CODE_BLOCK:
        lang = block.language or ""
        longest = max((len(run) for run in re.findall(r"`+", block.content)), default=0)
        fence = "`" * max(3, longest + 1)
        newline = "" if block.content.endswith("\n") else "\n"
        return f"{fence}{lang}\n{block.content}{newline}{fence}"
    elif block.type == BlockType.TABLE:
        return block.content
    elif block.type == BlockType.LIST:
        return block.content
    elif block.type == BlockType.BLOCKQUOTE:
        lines = block.content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return "\n".join(f"> {line}" for line in lines)
    elif block.type == BlockType.ADMONITION:
        kind = block.metadata.get("admonition_type", "note").upper()
        lines = block.content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        # Keep the label separate so a leading fence/list remains a block.
        return "\n".join([f"> **{kind}**:", ">", *[f"> {line}" for line in lines]])
    elif block.type == BlockType.IMAGE:
        label = block.content or block.metadata.get("src", "Image")
        return f"![{label}]"
    elif block.type == BlockType.PAGE_BREAK:
        return "---"
    else:
        return block.content


def render_markdown(document: Document) -> str:
    """Render the exact Markdown shared by string delivery and file export."""
    lines = [_block_to_md(block) for block in document.blocks]
    return "\n\n".join(line for line in lines if line)


class MarkdownExporter(ExporterPlugin):
    name = "markdown_exporter"
    priority = 90

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        out = Path(ctx.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        content = render_markdown(ctx.document)
        (out / "document.md").write_bytes(content.encode("utf-8"))
        return ctx


register_plugin(MarkdownExporter)
