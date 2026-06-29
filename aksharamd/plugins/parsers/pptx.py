from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document


def _shape_text(shape) -> str:
    try:
        if shape.has_text_frame:
            return "\n".join(
                p.text.strip()
                for p in shape.text_frame.paragraphs
                if p.text.strip()
            )
    except Exception:
        logger.debug("Could not extract text from PPTX shape", exc_info=True)
    return ""


def _table_to_markdown(table) -> str:
    rows = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(rows)


class PptxParser(ParserPlugin):
    name = "pptx_parser"
    supported_types = ["pptx"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        from pptx import Presentation
        from pptx.util import Pt
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        path = Path(ctx.source)
        try:
            prs = Presentation(str(path))
        except Exception as e:
            ctx.error("PPTX_PARSE_ERROR", str(e))
            return ctx

        blocks: list[Block] = []
        idx = 0
        title: str | None = None
        image_count = 0

        for slide_num, slide in enumerate(prs.slides, 1):
            # Slide title
            slide_title = None
            if slide.shapes.title:
                slide_title = _shape_text(slide.shapes.title).strip()
                if slide_title:
                    if not title:
                        title = slide_title
                    blocks.append(Block(
                        type=BlockType.HEADING, content=slide_title,
                        level=2, page=slide_num, index=idx,
                    ))
                    idx += 1

            for shape in slide.shapes:
                # Skip the title shape we already handled
                if shape == slide.shapes.title:
                    continue

                if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
                    md = _table_to_markdown(shape.table)
                    if md:
                        blocks.append(Block(type=BlockType.TABLE, content=md, page=slide_num, index=idx))
                        idx += 1
                    continue

                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    image_count += 1
                    continue

                text = _shape_text(shape)
                if not text:
                    continue

                # Heuristic: short all-caps text is a sub-heading
                lines = [l for l in text.splitlines() if l.strip()]
                for line in lines:
                    s = line.strip()
                    if not s:
                        continue
                    if len(s) < 80 and s == s.upper() and s.isalpha():
                        blocks.append(Block(type=BlockType.HEADING, content=s, level=3, page=slide_num, index=idx))
                    else:
                        blocks.append(Block(type=BlockType.PARAGRAPH, content=s, page=slide_num, index=idx))
                    idx += 1

            # Speaker notes
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    blocks.append(Block(
                        type=BlockType.BLOCKQUOTE,
                        content=f"[Speaker notes] {notes}",
                        page=slide_num, index=idx,
                    ))
                    idx += 1

        ctx.document = Document(
            source=str(path),
            file_type="pptx",
            title=title,
            pages=len(prs.slides),
            blocks=blocks,
            metadata={"slides": len(prs.slides), "images": image_count},
        ).compute_id()
        return ctx


register_parser("pptx", PptxParser)
