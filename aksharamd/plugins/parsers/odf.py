from __future__ import annotations
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

# ODF XML namespaces
_NS = {
    "text":  "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw":  "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "dc":    "http://purl.org/dc/elements/1.1/",
    "meta":  "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "office":"urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
}

_HEADING_STYLE_RE = re.compile(r"heading\s*(\d)", re.IGNORECASE)
_MAX_ROWS = 500
_MAX_COLS = 20


def _t(tag: str) -> str:
    """Expand a ns:local tag name."""
    ns, local = tag.split(":")
    return f"{{{_NS[ns]}}}{local}"


def _all_text(el: ET.Element) -> str:
    """Recursively collect all text from an element, respecting text:s (space) and text:tab."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        tag = child.tag
        if tag == _t("text:s"):
            count = int(child.get(_t("text:c") if _t("text:c") in (child.attrib or {}) else "1") or 1)
            parts.append(" " * count)
        elif tag == _t("text:tab"):
            parts.append("\t")
        elif tag == _t("text:line-break"):
            parts.append("\n")
        else:
            parts.append(_all_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _read_meta(zf: zipfile.ZipFile) -> dict[str, str]:
    meta: dict[str, str] = {}
    try:
        root = ET.fromstring(zf.read("meta.xml"))
        for tag, key in [
            (_t("dc:title"), "title"),
            (_t("dc:creator"), "creator"),
            (_t("dc:description"), "description"),
        ]:
            el = root.find(f".//{tag}")
            if el is not None and el.text:
                meta[key] = el.text.strip()
    except Exception:
        logger.debug("Failed to read ODF meta.xml", exc_info=True)
    return meta


# ── ODT (text document) ────────────────────────────────────────────────────────

def _odt_heading(el: ET.Element, idx: int) -> Block | None:
    level = int(el.get(_t("text:outline-level") or "") or 1)
    level = max(1, min(6, level))
    text = _all_text(el).strip()
    if text:
        return Block(type=BlockType.HEADING, content=text, level=level, index=idx)
    return None


def _odt_paragraph(el: ET.Element, idx: int) -> Block | None:
    text = _all_text(el).strip()
    if not text:
        return None
    style = el.get(_t("text:style-name") or "") or ""
    m = _HEADING_STYLE_RE.search(style)
    if m:
        level = max(1, min(6, int(m.group(1))))
        return Block(type=BlockType.HEADING, content=text, level=level, index=idx)
    return Block(type=BlockType.PARAGRAPH, content=text, index=idx)


def _odt_table(el: ET.Element, idx: int) -> Block | None:
    rows_md: list[str] = []
    for row_el in el.findall(_t("table:table-row"))[:_MAX_ROWS]:
        cells = []
        for cell in row_el.findall(_t("table:table-cell"))[:_MAX_COLS]:
            cell_text = " ".join(_all_text(p).strip() for p in cell.findall(_t("text:p")))
            cells.append(cell_text[:80])
        if any(cells):
            rows_md.append("| " + " | ".join(cells) + " |")
    if not rows_md:
        return None
    sep = "| " + " | ".join(["---"] * rows_md[0].count("|")) + " |"
    rows_md.insert(1, sep)
    return Block(type=BlockType.TABLE, content="\n".join(rows_md), index=idx)


def _walk_odt(el: ET.Element, blocks: list[Block], idx: list[int]) -> None:
    """Recursive ODT walker — dispatches by tag, prevents double-processing."""
    for child in el:
        tag = child.tag
        if tag == _t("text:h"):
            b = _odt_heading(child, idx[0])
            if b:
                blocks.append(b)
                idx[0] += 1
        elif tag == _t("text:p"):
            b = _odt_paragraph(child, idx[0])
            if b:
                blocks.append(b)
                idx[0] += 1
        elif tag == _t("table:table"):
            b = _odt_table(child, idx[0])
            if b:
                blocks.append(b)
                idx[0] += 1
            # Do NOT recurse into table children — already handled in _odt_table
        elif tag in (_t("text:section"), _t("text:body"), _t("office:text")):
            _walk_odt(child, blocks, idx)
        else:
            # Unknown container — recurse transparently
            _walk_odt(child, blocks, idx)


def _parse_odt_body(body: ET.Element) -> list[Block]:
    blocks: list[Block] = []
    idx = [0]
    _walk_odt(body, blocks, idx)
    return blocks


# ── ODS (spreadsheet) ──────────────────────────────────────────────────────────

def _parse_ods_body(body: ET.Element) -> list[Block]:
    blocks: list[Block] = []
    idx = 0
    for sheet in body.iter(_t("table:table")):
        name = sheet.get(_t("table:name") or "") or "Sheet"
        blocks.append(Block(type=BlockType.HEADING, content=name, level=2, index=idx))
        idx += 1

        rows_md: list[str] = []
        for row_el in sheet.findall(_t("table:table-row"))[:_MAX_ROWS]:
            cells = []
            for cell in row_el.findall(_t("table:table-cell"))[:_MAX_COLS]:
                # Check repeat attribute to skip empty trailing cells
                repeat = int(cell.get(_t("table:number-columns-repeated") or "1") or 1)
                val = cell.get(_t("office:value") or "") or ""
                texts = [_all_text(p).strip() for p in cell.findall(_t("text:p"))]
                cell_text = " ".join(texts) or val
                cells.append(cell_text[:80])
                if repeat > 1 and not cell_text:
                    break  # trailing empty repeated cells
            cells = [c for c in cells if c]  # drop trailing empties
            if cells:
                rows_md.append("| " + " | ".join(cells) + " |")
        if len(rows_md) >= 1:
            sep = "| " + " | ".join(["---"] * rows_md[0].count("|")) + " |"
            rows_md.insert(1, sep)
            blocks.append(Block(type=BlockType.TABLE, content="\n".join(rows_md), index=idx))
            idx += 1

    return blocks


# ── ODP (presentation) ─────────────────────────────────────────────────────────

def _parse_odp_body(body: ET.Element) -> list[Block]:
    blocks: list[Block] = []
    idx = 0
    slide_num = 0

    for page in body.iter(_t("draw:page")):
        slide_num += 1
        page_name = page.get(_t("draw:name") or "") or f"Slide {slide_num}"

        blocks.append(Block(type=BlockType.HEADING, content=page_name, level=2, index=idx))
        idx += 1

        for frame in page.iter(_t("draw:frame")):
            for text_box in frame.iter(_t("draw:text-box")):
                for para in text_box.findall(_t("text:p")):
                    text = _all_text(para).strip()
                    if not text:
                        continue
                    style = para.get(_t("text:style-name") or "") or ""
                    if "title" in style.lower() or "Title" in style:
                        blocks.append(Block(type=BlockType.HEADING, content=text, level=3, index=idx))
                    else:
                        blocks.append(Block(type=BlockType.PARAGRAPH, content=text, index=idx))
                    idx += 1

        # Speaker notes
        for notes in page.findall(_t("presentation:notes")):
            for para in notes.iter(_t("text:p")):
                text = _all_text(para).strip()
                if text:
                    blocks.append(Block(type=BlockType.BLOCKQUOTE, content=f"[Note] {text}", index=idx))
                    idx += 1

    return blocks


# ── Shared parser ──────────────────────────────────────────────────────────────

def _parse_odf(path: Path, file_type: str) -> tuple[list[Block], dict]:
    with zipfile.ZipFile(str(path), "r") as zf:
        meta = _read_meta(zf)
        content_xml = zf.read("content.xml")

    root = ET.fromstring(content_xml)
    body_content = root.find(f".//{_t('office:body')}")
    if body_content is None:
        return [], meta

    if file_type == "odt":
        body = body_content.find(_t("office:text"))
        blocks = _parse_odt_body(body) if body is not None else []
    elif file_type == "ods":
        body = body_content.find(_t("office:spreadsheet"))
        blocks = _parse_ods_body(body) if body is not None else []
    elif file_type == "odp":
        body = body_content.find(_t("office:presentation"))
        blocks = _parse_odp_body(body) if body is not None else []
    else:
        blocks = []

    return blocks, meta


class OdfParser(ParserPlugin):
    name = "odf_parser"
    supported_types = ["odt", "ods", "odp"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        file_type = path.suffix.lower().lstrip(".")

        try:
            blocks, meta = _parse_odf(path, file_type)
        except Exception as e:
            ctx.error("ODF_PARSE_ERROR", str(e))
            return ctx

        if not blocks:
            ctx.error("ODF_EMPTY", "No content extracted")
            return ctx

        ctx.document = Document(
            source=str(path),
            file_type=file_type,
            title=meta.get("title") or path.stem,
            author=meta.get("creator") or None,
            pages=1,
            blocks=blocks,
            metadata=meta,
        ).compute_id()
        return ctx


for _ext in OdfParser.supported_types:
    register_parser(_ext, OdfParser)
