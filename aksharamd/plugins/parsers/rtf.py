from __future__ import annotations
import re
from pathlib import Path

import chardet

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")


def _extract_text(path: Path) -> str:
    from striprtf.striprtf import rtf_to_text
    raw = path.read_bytes()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    rtf_str = raw.decode(enc, errors="replace")
    return rtf_to_text(rtf_str)


class RtfParser(ParserPlugin):
    name = "rtf_parser"
    supported_types = ["rtf"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        try:
            text = _extract_text(path)
        except Exception as e:
            ctx.error("RTF_PARSE_ERROR", str(e))
            return ctx

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = re.split(r"\n{2,}", text.strip())

        blocks: list[Block] = []
        idx = 0
        title: str | None = None

        for para in paragraphs:
            para = para.strip()
            if not para or len(para) < 2:
                continue

            m = _HEADING_RE.match(para)
            if m:
                level = len(m.group(1))
                content = m.group(2).strip()
                if not title and level == 1:
                    title = content
                blocks.append(Block(type=BlockType.HEADING, content=content, level=level, index=idx))
            else:
                if not title and idx == 0 and len(para) < 120:
                    title = para
                blocks.append(Block(type=BlockType.PARAGRAPH, content=para, index=idx))
            idx += 1

        ctx.document = Document(
            source=str(path),
            file_type="rtf",
            title=title or path.stem,
            pages=1,
            blocks=blocks,
        ).compute_id()
        return ctx


register_parser("rtf", RtfParser)
