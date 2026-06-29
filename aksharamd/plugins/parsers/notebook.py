from __future__ import annotations
import json as _json
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_MAX_OUTPUT_CHARS = 2000
_HEADING_RE = None


def _md_heading_level(line: str) -> int | None:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= 6 and (len(stripped) > level and stripped[level] == " "):
            return level
    return None


class NotebookParser(ParserPlugin):
    name = "notebook_parser"
    supported_types = ["ipynb"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        try:
            nb = _json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            ctx.error("IPYNB_PARSE_ERROR", str(e))
            return ctx

        lang = (
            nb.get("metadata", {})
              .get("kernelspec", {})
              .get("language", "python")
        )
        cells = nb.get("cells", [])

        blocks: list[Block] = [
            Block(type=BlockType.METADATA,
                  content=f"Notebook | Language: {lang} | Cells: {len(cells)}",
                  index=0),
        ]
        idx = 1
        title: str | None = None

        for cell in cells:
            cell_type = cell.get("cell_type", "")
            source = "".join(cell.get("source", []))

            if cell_type == "markdown":
                lines = source.splitlines()
                para_lines: list[str] = []

                def flush_para():
                    nonlocal idx
                    text = " ".join(para_lines).strip()
                    if text:
                        blocks.append(Block(type=BlockType.PARAGRAPH, content=text, index=idx))
                        idx += 1
                    para_lines.clear()

                for line in lines:
                    level = _md_heading_level(line)
                    if level is not None:
                        flush_para()
                        text = line.lstrip("#").strip()
                        if not title and level == 1:
                            title = text
                        blocks.append(Block(type=BlockType.HEADING, content=text, level=level, index=idx))
                        idx += 1
                    else:
                        para_lines.append(line)
                flush_para()

            elif cell_type == "code":
                if source.strip():
                    blocks.append(Block(
                        type=BlockType.CODE_BLOCK,
                        content=source,
                        language=lang,
                        index=idx,
                    ))
                    idx += 1

                # Outputs: only include text/plain and stderr, skip images
                for output in cell.get("outputs", []):
                    out_type = output.get("output_type", "")
                    text_lines = output.get("text", []) or output.get("data", {}).get("text/plain", [])
                    if text_lines:
                        out_text = "".join(text_lines)[:_MAX_OUTPUT_CHARS].strip()
                        if out_text:
                            prefix = "[stderr] " if out_type == "stream" and output.get("name") == "stderr" else "[output] "
                            blocks.append(Block(
                                type=BlockType.BLOCKQUOTE,
                                content=prefix + out_text,
                                index=idx,
                            ))
                            idx += 1

            elif cell_type == "raw":
                if source.strip():
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=source.strip(), index=idx))
                    idx += 1

        ctx.document = Document(
            source=str(path),
            file_type="ipynb",
            title=title or path.stem,
            pages=1,
            blocks=blocks,
            metadata={"cells": len(cells), "language": lang},
        ).compute_id()
        return ctx


register_parser("ipynb", NotebookParser)
