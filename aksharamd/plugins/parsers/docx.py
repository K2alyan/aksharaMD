from __future__ import annotations
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.asset import Asset
from ...models.document import Document

_DRAW_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _extract_drawing_bytes(para_el, doc_part) -> list[bytes]:
    """Extract image bytes from all w:drawing elements within a paragraph element."""
    images = []
    for drawing in para_el.iter(f"{{{_W_NS}}}drawing"):
        for blip in drawing.iter(f"{{{_DRAW_NS}}}blip"):
            r_id = blip.get(f"{{{_REL_NS}}}embed")
            if r_id:
                try:
                    images.append(doc_part.related_parts[r_id].blob)
                except Exception:
                    pass
    return images

_OMML_URI = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _extract_omml_text(el) -> str:
    """Collect text from OMML <m:t> nodes within *el*."""
    parts = []
    for node in el.iter():
        if node.tag == f"{{{_OMML_URI}}}t" and node.text and node.text.strip():
            parts.append(node.text.strip())
    return " ".join(parts)


_HEADING_STYLES = {
    "heading 1": 1, "heading 2": 2, "heading 3": 3,
    "heading 4": 4, "heading 5": 5, "heading 6": 6,
    "title": 1, "subtitle": 2,
}


def _table_to_markdown(table) -> str:
    rows = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(rows)


class DocxParser(ParserPlugin):
    name = "docx_parser"
    supported_types = ["docx"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        from docx import Document as DocxDocument
        from docx.oxml.ns import qn

        path = Path(ctx.source)
        try:
            doc = DocxDocument(str(path))
        except Exception as e:
            ctx.error("DOCX_PARSE_ERROR", str(e))
            return ctx

        blocks: list[Block] = []
        assets: list[Asset] = []
        idx = 0
        title: str | None = None
        author: str | None = None

        # Core properties
        props = doc.core_properties
        if props.title:
            title = props.title
        if props.author:
            author = props.author

        # Body: interleave paragraphs and tables in document order
        # python-docx exposes doc.paragraphs and doc.tables separately;
        # to preserve order we walk the body XML directly.
        body = doc.element.body
        para_iter = iter(doc.paragraphs)
        table_iter = iter(doc.tables)

        para_map = {p._element: p for p in doc.paragraphs}
        table_map = {t._element: t for t in doc.tables}

        for child in body:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p" and child in para_map:
                para = para_map[child]

                # Extract any inline images in this paragraph (before processing text)
                for img_bytes in _extract_drawing_bytes(child, doc.part):
                    asset_id = f"img_{idx}"
                    assets.append(Asset(id=asset_id, type="image", image_bytes=img_bytes))
                    blocks.append(Block(type=BlockType.IMAGE, content="", index=idx,
                                        metadata={"asset_id": asset_id}))
                    idx += 1

                style_name = (para.style.name or "").lower() if para.style else ""
                text = para.text.strip()

                # Detect inline OMML equations (paragraphs that are partially/fully equations)
                math_text = _extract_omml_text(child)
                if not text and math_text:
                    blocks.append(Block(type=BlockType.PARAGRAPH,
                                        content=f"$${math_text}$$", index=idx))
                    idx += 1
                    continue

                if not text:
                    continue

                level = _HEADING_STYLES.get(style_name)
                if level:
                    if not title and level == 1:
                        title = text
                    blocks.append(Block(type=BlockType.HEADING, content=text, level=level, index=idx))
                elif "code" in style_name or "mono" in style_name:
                    blocks.append(Block(type=BlockType.CODE_BLOCK, content=text, index=idx))
                else:
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=text, index=idx))
                idx += 1

            elif tag == "oMathPara":
                math_text = _extract_omml_text(child)
                if math_text:
                    blocks.append(Block(type=BlockType.PARAGRAPH,
                                        content=f"$${math_text}$$", index=idx))
                    idx += 1

            elif tag == "tbl" and child in table_map:
                table = table_map[child]
                md = _table_to_markdown(table)
                if md:
                    blocks.append(Block(type=BlockType.TABLE, content=md, index=idx))
                    idx += 1


        image_count = len([b for b in blocks if b.type == BlockType.IMAGE])
        ctx.document = Document(
            source=str(path),
            file_type="docx",
            title=title,
            author=author,
            pages=len(doc.sections),
            blocks=blocks,
            assets=assets,
            metadata={"images": image_count, "sections": len(doc.sections)},
        ).compute_id()
        return ctx


register_parser("docx", DocxParser)
