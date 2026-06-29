from __future__ import annotations
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

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
                style_name = (para.style.name or "").lower() if para.style else ""
                text = para.text.strip()
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

            elif tag == "tbl" and child in table_map:
                table = table_map[child]
                md = _table_to_markdown(table)
                if md:
                    blocks.append(Block(type=BlockType.TABLE, content=md, index=idx))
                    idx += 1

        images = sum(1 for s in doc.inline_shapes)

        ctx.document = Document(
            source=str(path),
            file_type="docx",
            title=title,
            author=author,
            pages=len(doc.sections),
            blocks=blocks,
            metadata={"images": images, "sections": len(doc.sections)},
        ).compute_id()
        return ctx


register_parser("docx", DocxParser)
