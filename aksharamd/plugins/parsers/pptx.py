from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document


_PT_TO_PX = 4 / 3  # 1 pt = 1.333 px (irrelevant here — we just compare pt values)


def _para_font_size_pt(para) -> float | None:
    """Return the dominant font size of a paragraph in points, or None."""
    try:
        sizes = []
        for run in para.runs:
            if run.font.size:
                sizes.append(run.font.size.pt)
        if sizes:
            return max(sizes)
        # Fall back to paragraph-level font
        if para.font.size:
            return para.font.size.pt
    except Exception:
        pass
    return None


def _para_has_explicit_bullet(para) -> bool:
    """Return True if the paragraph has explicit bullet formatting in its own XML."""
    try:
        from pptx.oxml.ns import qn
        pPr = para._p.find(qn("a:pPr"))
        if pPr is None:
            return False
        if pPr.find(qn("a:buNone")) is not None:
            return False
        if (pPr.find(qn("a:buChar")) is not None or
                pPr.find(qn("a:buAutoNum")) is not None or
                pPr.find(qn("a:buFont")) is not None):
            return True
    except Exception:
        pass
    return False


def _text_frame_is_list(paras: list) -> bool:
    """
    Return True if this text frame looks like a bullet list.

    Many PPTX files inherit bullet styling from the slide layout rather than
    encoding it in paragraph XML, so explicit XML checks miss most real lists.
    Heuristic: 2+ paragraphs where all are short (≤ 12 words) and none ends
    with a period followed by another sentence — this profile matches list items,
    not prose.
    """
    if len(paras) < 2:
        return False
    for p in paras:
        text = p.text.strip()
        words = text.split()
        if len(words) > 15:
            return False
        # If it ends with ". " continuation or is multi-sentence, it's prose
        if text.count(". ") >= 2:
            return False
    return True


def _table_to_markdown(table) -> str:
    rows = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(rows)


def _shape_to_blocks(shape, slide_num: int, idx: int,
                     title_shape) -> tuple[list[Block], int]:
    """
    Convert a single PPTX shape into one or more Blocks.

    Strategy:
    - Tables → TABLE block
    - Text frames → analyse each paragraph:
        * Large font (>= 20 pt) and few words → HEADING level 3
        * Explicit bullet markup → collect into a LIST block
        * Otherwise → PARAGRAPH block per logical group
    """
    blocks: list[Block] = []

    try:
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
            md = _table_to_markdown(shape.table)
            if md:
                blocks.append(Block(type=BlockType.TABLE, content=md,
                                    page=slide_num, index=idx))
            return blocks, idx + len(blocks)

        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            return blocks, idx  # no text to extract

        if not shape.has_text_frame:
            return blocks, idx

    except Exception:
        return blocks, idx

    # --- text frame ---
    tf = shape.text_frame
    paras = [p for p in tf.paragraphs if p.text.strip()]
    if not paras:
        return blocks, idx

    # Decide upfront if the whole text frame is a list
    frame_is_list = _text_frame_is_list(paras)

    if frame_is_list:
        # Check if any paragraph has an explicit bullet; if the frame heuristic
        # says list AND explicit bullets exist, trust the explicit markup.
        # Either way, emit as a single LIST block.
        items = [p.text.strip() for p in paras if p.text.strip()]
        content = "\n".join(f"- {item}" for item in items)
        blocks.append(Block(type=BlockType.LIST, content=content,
                            page=slide_num, index=idx))
        idx += 1
    else:
        # Mixed content — process paragraph by paragraph
        pending_flush: list[str] = []

        def flush_pending():
            nonlocal idx
            if pending_flush:
                text = "\n".join(pending_flush)
                if text:
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=text,
                                        page=slide_num, index=idx))
                    idx += 1
                pending_flush.clear()

        for para in paras:
            text = para.text.strip()
            if not text:
                continue

            size = _para_font_size_pt(para)
            is_big = size is not None and size >= 20
            is_short = len(text.split()) <= 12
            has_explicit_bullet = _para_has_explicit_bullet(para)

            if has_explicit_bullet:
                flush_pending()
                blocks.append(Block(type=BlockType.LIST, content=f"- {text}",
                                    page=slide_num, index=idx))
                idx += 1
            elif is_big and is_short:
                flush_pending()
                blocks.append(Block(type=BlockType.HEADING, content=text,
                                    level=3, page=slide_num, index=idx))
                idx += 1
            else:
                pending_flush.append(text)

        flush_pending()

    return blocks, idx


class PptxParser(ParserPlugin):
    name = "pptx_parser"
    supported_types = ["pptx"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        from pptx import Presentation

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
            slide_title_text = None
            title_shape = slide.shapes.title

            # Slide heading
            if title_shape:
                slide_title_text = title_shape.text_frame.text.strip() if title_shape.has_text_frame else ""
                if slide_title_text:
                    if not title:
                        title = slide_title_text
                    blocks.append(Block(
                        type=BlockType.HEADING, content=slide_title_text,
                        level=2, page=slide_num, index=idx,
                    ))
                    idx += 1

            # Count images, process all other shapes
            for shape in slide.shapes:
                if shape == title_shape:
                    continue
                try:
                    from pptx.enum.shapes import MSO_SHAPE_TYPE
                    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        image_count += 1
                        continue
                except Exception:
                    pass

                new_blocks, idx = _shape_to_blocks(shape, slide_num, idx, title_shape)
                blocks.extend(new_blocks)

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
