from __future__ import annotations

import re
from pathlib import Path

import chardet
from markdown_it import MarkdownIt

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser


def _read_file(path: Path) -> str:
    raw = path.read_bytes()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def _render_list_tokens(tokens: list, start: int, ordered: bool, depth: int = 0) -> tuple[list[str], int]:
    """Recursively render list tokens to indented lines. Returns (lines, index_after_close)."""
    lines: list[str] = []
    j = start
    item_num = 1
    while j < len(tokens):
        ttype = tokens[j].type
        if ttype in ("bullet_list_close", "ordered_list_close"):
            return lines, j + 1
        if ttype in ("bullet_list_open", "ordered_list_open"):
            nested_lines, j = _render_list_tokens(tokens, j + 1, ttype == "ordered_list_open", depth + 1)
            lines.extend(nested_lines)
            continue
        if ttype == "inline" and j > 0 and tokens[j - 1].type == "paragraph_open":
            indent = "  " * depth
            prefix = f"{item_num}." if ordered else "-"
            lines.append(f"{indent}{prefix} {tokens[j].content}")
            item_num += 1
        j += 1
    return lines, j


class MarkdownParser(ParserPlugin):
    name = "markdown_parser"
    supported_types = ["md", "markdown"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        text = _read_file(path)

        md = MarkdownIt().enable("table")
        tokens = md.parse(text)

        blocks: list[Block] = []
        block_index = 0
        title: str | None = None
        i = 0

        while i < len(tokens):
            token = tokens[i]

            if token.type == "heading_open":
                level = int(token.tag[1])
                inline = tokens[i + 1] if i + 1 < len(tokens) else None
                content = inline.content if inline else ""
                if not title and level == 1:
                    title = content
                blocks.append(Block(
                    type=BlockType.HEADING,
                    content=content,
                    level=level,
                    index=block_index,
                ))
                block_index += 1
                i += 3  # heading_open, inline, heading_close
                continue

            elif token.type == "paragraph_open":
                inline = tokens[i + 1] if i + 1 < len(tokens) else None
                content = inline.content if inline else ""
                content = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", content)  # strip links
                if content.strip():
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content=content.strip(),
                        index=block_index,
                    ))
                    block_index += 1
                i += 3
                continue

            elif token.type == "fence":
                lang = token.info.strip() if token.info else None
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=token.content,
                    language=lang,
                    index=block_index,
                ))
                block_index += 1

            elif token.type == "table_open":
                # Collect the raw table markdown
                text.find("|")
                table_lines = []
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("|"):
                        table_lines.append(stripped)
                    elif table_lines:
                        break
                if table_lines:
                    blocks.append(Block(
                        type=BlockType.TABLE,
                        content="\n".join(table_lines),
                        index=block_index,
                    ))
                    block_index += 1

            elif token.type == "bullet_list_open" or token.type == "ordered_list_open":
                ordered = token.type == "ordered_list_open"
                list_lines, next_i = _render_list_tokens(tokens, i + 1, ordered)
                if list_lines:
                    blocks.append(Block(
                        type=BlockType.LIST,
                        content="\n".join(list_lines),
                        index=block_index,
                    ))
                    block_index += 1
                i = next_i
                continue

            elif token.type == "blockquote_open":
                j = i + 1
                parts = []
                while j < len(tokens) and tokens[j].type != "blockquote_close":
                    if tokens[j].type == "inline":
                        parts.append(tokens[j].content)
                    j += 1
                if parts:
                    blocks.append(Block(
                        type=BlockType.BLOCKQUOTE,
                        content="\n".join(parts),
                        index=block_index,
                    ))
                    block_index += 1

            i += 1

        doc = Document(
            source=str(path),
            file_type="md",
            title=title or path.stem,
            pages=1,
            blocks=blocks,
        )
        doc.compute_id()
        ctx.document = doc
        return ctx


register_parser("md", MarkdownParser)
register_parser("markdown", MarkdownParser)
