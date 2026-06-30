from __future__ import annotations

import re
from pathlib import Path

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_DRAW_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_LEVEL_SUFFIX_RE = re.compile(r'\s+(\d+)$')


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


def _get_list_props(para, qn) -> tuple[str | None, int, bool | None]:
    """
    Return (group_key, ilvl, is_ordered) if this paragraph is a list item, else (None, 0, False).

    group_key  — identifies which list this belongs to (for grouping consecutive items)
    ilvl       — indent level 0=top
    is_ordered — True=numbered, False=bullet, None=must look up from numbering XML
    """
    # Primary: w:numPr in paragraph XML (authoritative for real Word documents)
    try:
        pPr = para._element.find(qn("w:pPr"))
        if pPr is not None:
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                numId_el = numPr.find(qn("w:numId"))
                ilvl_el = numPr.find(qn("w:ilvl"))
                if numId_el is not None:
                    numId = numId_el.get(qn("w:val"), "0")
                    if numId != "0":
                        ilvl = int(ilvl_el.get(qn("w:val"), "0")) if ilvl_el is not None else 0
                        return f"num:{numId}", ilvl, None  # is_ordered resolved from numbering XML
    except Exception:
        pass

    # Fallback: style name (python-docx-created files and simple Word docs)
    style_name = (para.style.name or "") if para.style else ""
    sl = style_name.lower()
    if "list bullet" in sl:
        m = _LEVEL_SUFFIX_RE.search(style_name)
        ilvl = int(m.group(1)) - 1 if m else 0
        return "style:bullet", ilvl, False
    if "list number" in sl:
        m = _LEVEL_SUFFIX_RE.search(style_name)
        ilvl = int(m.group(1)) - 1 if m else 0
        return "style:number", ilvl, True

    return None, 0, False


def _build_ordered_numids(doc) -> set[str]:
    """Return the set of numIds whose level-0 format is ordered (not bullet/none)."""
    ordered: set[str] = set()
    try:
        from docx.oxml.ns import qn
        root = doc.part.numbering_part._element
        # abstractNumId -> level-0 numFmt value
        abstract_fmt0: dict[str, str] = {}
        for a in root.findall(qn("w:abstractNum")):
            aid = a.get(qn("w:abstractNumId"))
            for lvl in a.findall(qn("w:lvl")):
                if lvl.get(qn("w:ilvl")) == "0":
                    nf = lvl.find(qn("w:numFmt"))
                    fmt = nf.get(qn("w:val"), "bullet") if nf is not None else "bullet"
                    abstract_fmt0[aid] = fmt
                    break
        for n in root.findall(qn("w:num")):
            nid = n.get(qn("w:numId"))
            ref = n.find(qn("w:abstractNumId"))
            if ref is None:
                continue
            fmt = abstract_fmt0.get(ref.get(qn("w:val")), "bullet")
            if fmt not in ("bullet", "none"):
                ordered.add(nid)
    except Exception:
        pass
    return ordered


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

        props = doc.core_properties
        if props.title:
            title = props.title
        if props.author:
            author = props.author

        ordered_numIds = _build_ordered_numids(doc)

        body = doc.element.body
        para_map = {p._element: p for p in doc.paragraphs}
        table_map = {t._element: t for t in doc.tables}

        # List accumulator — groups consecutive list paragraphs into one LIST block
        list_items: list[tuple[int, bool, str]] = []  # (ilvl, is_ordered, text)
        current_list_key: str | None = None

        def flush_list() -> None:
            nonlocal idx, current_list_key
            if not list_items:
                return
            counters: dict[int, int] = {}
            prev_ilvl = -1
            lines: list[str] = []
            for ilvl, is_ordered, text in list_items:
                # Reset deeper-level counters when ascending
                if ilvl < prev_ilvl:
                    for k in [k for k in counters if k > ilvl]:
                        del counters[k]
                indent = "  " * ilvl
                if is_ordered:
                    counters[ilvl] = counters.get(ilvl, 0) + 1
                    prefix = f"{counters[ilvl]}."
                else:
                    prefix = "-"
                lines.append(f"{indent}{prefix} {text}")
                prev_ilvl = ilvl
            blocks.append(Block(type=BlockType.LIST, content="\n".join(lines), index=idx))
            idx += 1
            list_items.clear()
            current_list_key = None

        for child in body:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p" and child in para_map:
                para = para_map[child]

                # Inline images flush the current list (images break list continuity)
                img_list = _extract_drawing_bytes(child, doc.part)
                if img_list:
                    flush_list()
                    for img_bytes in img_list:
                        asset_id = f"img_{idx}"
                        assets.append(Asset(id=asset_id, type="image", image_bytes=img_bytes))
                        blocks.append(Block(type=BlockType.IMAGE, content="", index=idx,
                                            metadata={"asset_id": asset_id}))
                        idx += 1

                math_text = _extract_omml_text(child)
                text = para.text.strip()

                if not text and math_text:
                    flush_list()
                    blocks.append(Block(type=BlockType.PARAGRAPH,
                                        content=f"$${math_text}$$", index=idx))
                    idx += 1
                    continue

                if not text:
                    continue

                # List item?
                key, ilvl, is_ordered = _get_list_props(para, qn)
                if key is not None:
                    if is_ordered is None:
                        is_ordered = key[len("num:"):] in ordered_numIds
                    if key != current_list_key and current_list_key is not None:
                        flush_list()
                    current_list_key = key
                    list_items.append((ilvl, is_ordered, text))
                    continue

                # Regular paragraph
                flush_list()
                style_name = (para.style.name or "").lower() if para.style else ""
                level = _HEADING_STYLES.get(style_name)
                if level:
                    if not title and level == 1:
                        title = text
                    blocks.append(Block(type=BlockType.HEADING, content=text,
                                        level=level, index=idx))
                elif "code" in style_name or "mono" in style_name:
                    blocks.append(Block(type=BlockType.CODE_BLOCK, content=text, index=idx))
                else:
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=text, index=idx))
                idx += 1

            elif tag == "oMathPara":
                flush_list()
                math_text = _extract_omml_text(child)
                if math_text:
                    blocks.append(Block(type=BlockType.PARAGRAPH,
                                        content=f"$${math_text}$$", index=idx))
                    idx += 1

            elif tag == "tbl" and child in table_map:
                flush_list()
                table = table_map[child]
                md = _table_to_markdown(table)
                if md:
                    blocks.append(Block(type=BlockType.TABLE, content=md, index=idx))
                    idx += 1

        flush_list()

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
