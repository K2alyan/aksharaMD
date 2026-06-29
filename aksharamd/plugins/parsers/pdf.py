from __future__ import annotations
import hashlib
import logging
import re
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.asset import Asset
from ...models.document import Document


_PAGE_NUM_RE = re.compile(
    r"^\d+$"
    r"|^\d+[-–]\d+$"
    r"|^-\s*\d+\s*-$"
    r"|^page\s+\d+(\s+of\s+\d+)?$",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r"^(figure|fig\.?|table|exhibit|appendix)\s+\d",
    re.IGNORECASE,
)

_HEADER_ZONE = 0.12
_FOOTER_ZONE = 0.88
_FOOTNOTE_ZONE_START = 0.72
_FOOTNOTE_SIZE_RATIO = 0.72
_COL_GENERIC_RE = re.compile(r"^Col\d+$")


def _is_bold(flags: int) -> bool:
    return bool(flags & 2**4)


def _has_ruled_table(page: fitz.Page) -> bool:
    """
    Geometry pre-screen — returns True only if the page has enough horizontal
    and vertical line segments to plausibly contain a ruled table.
    Skipping find_tables() on text-only pages is the primary speed win.
    """
    h = v = 0
    for path in page.get_drawings():
        for item in path.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                dx = abs(p2.x - p1.x)
                dy = abs(p2.y - p1.y)
                if dx > 20 and dy < 3:
                    h += 1
                elif dy > 10 and dx < 3:
                    v += 1
            elif item[0] == "re":
                r = item[1]
                if r.width > 30 and r.height > 5:
                    h += 2
                    v += 1
        if h >= 3 and v >= 2:
            return True
    return False


def _is_quality_table(markdown: str) -> bool:
    """Reject tables that are clearly noise: need ≥2 columns and ≥1 real data row."""
    lines = [l for l in markdown.strip().splitlines() if l.strip()]
    if len(lines) < 3:
        return False
    cols = [c for c in lines[0].split("|") if c.strip()]
    if len(cols) < 2:
        return False
    data_rows = [l for l in lines[2:] if "|" in l and not l.startswith("|---")]
    return bool(data_rows)


def _clean_table_markdown(markdown: str) -> str:
    """Replace generic ColN header names with blank cells for readability."""
    lines = markdown.strip().splitlines()
    if not lines:
        return markdown
    cells = lines[0].split("|")
    lines[0] = "|".join(
        "" if _COL_GENERIC_RE.match(c.strip()) else c
        for c in cells
    )
    return "\n".join(lines)


_OCR_TEXT_THRESHOLD = 50   # chars below which a page is treated as image-only
_OCR_DPI = 150             # rasterization DPI for Tesseract; 150 balances speed and accuracy


class RawPage(NamedTuple):
    page_num: int
    spans: list[dict]
    tables: list[dict]
    images: list[dict]
    height: float
    width: float
    ocr_pixmap: bytes | None = None  # PNG bytes when page has < _OCR_TEXT_THRESHOLD chars


def _extract_raw_page(pdf: fitz.Document, page_num: int) -> RawPage:
    """Sequential I/O pass — must run in the main thread (PyMuPDF not thread-safe)."""
    page = pdf[page_num - 1]

    spans = []
    for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        if block["type"] != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if text:
                    spans.append({
                        "text": text,
                        "size": span["size"],
                        "bold": _is_bold(span.get("flags", 0)),
                        "y": span["origin"][1],
                        "x": span["origin"][0],
                        "bbox": span["bbox"],
                    })

    tables = []
    if _has_ruled_table(page):
        try:
            for tab in page.find_tables():
                md = tab.to_markdown()
                if md and _is_quality_table(md):
                    tables.append({"markdown": _clean_table_markdown(md), "bbox": tuple(tab.bbox)})
        except Exception:
            logger.debug("find_tables() failed on page %d", page_num, exc_info=True)

    images = [
        {"xref": img[0], "img_index": i}
        for i, img in enumerate(page.get_images(full=True))
    ]

    total_chars = sum(len(s["text"]) for s in spans)
    ocr_pixmap: bytes | None = None
    if total_chars < _OCR_TEXT_THRESHOLD:
        try:
            pix = page.get_pixmap(dpi=_OCR_DPI)
            ocr_pixmap = pix.tobytes("png")
        except Exception:
            logger.debug("Rasterization failed on page %d", page_num, exc_info=True)

    return RawPage(
        page_num=page_num,
        spans=spans,
        tables=tables,
        images=images,
        height=page.rect.height,
        width=page.rect.width,
        ocr_pixmap=ocr_pixmap,
    )


def _median_font_size(all_pages: list[RawPage]) -> float:
    sizes = [s["size"] for p in all_pages for s in p.spans]
    return statistics.median(sizes) if sizes else 12.0


def _detect_removable_spans(all_pages: list[RawPage]) -> set[str]:
    page_count = len(all_pages)
    to_remove: set[str] = set()
    header_counter: Counter[str] = Counter()
    footer_counter: Counter[str] = Counter()
    global_counter: Counter[str] = Counter()

    for raw in all_pages:
        seen_page: set[str] = set()
        seen_header: set[str] = set()
        seen_footer: set[str] = set()

        for span in raw.spans:
            text = span["text"].strip()
            if not text:
                continue
            if _PAGE_NUM_RE.match(text):
                to_remove.add(text)
                continue
            if text not in seen_page:
                global_counter[text] += 1
                seen_page.add(text)
            if raw.height > 0:
                rel_y = span["y"] / raw.height
                if rel_y < _HEADER_ZONE and text not in seen_header:
                    header_counter[text] += 1
                    seen_header.add(text)
                elif rel_y > _FOOTER_ZONE and text not in seen_footer:
                    footer_counter[text] += 1
                    seen_footer.add(text)

    zone_threshold = max(2, int(page_count * 0.3))
    global_threshold = max(2, int(page_count * 0.4))

    for text, count in header_counter.items():
        if count >= zone_threshold:
            to_remove.add(text)
    for text, count in footer_counter.items():
        if count >= zone_threshold:
            to_remove.add(text)
    for text, count in global_counter.items():
        if count >= global_threshold:
            to_remove.add(text)

    return to_remove


def _filter_table_spans(spans: list[dict], table_bboxes: list[tuple]) -> list[dict]:
    """Drop spans whose center falls inside a detected table bounding box."""
    if not table_bboxes:
        return spans

    def in_table(span: dict) -> bool:
        sx0, sy0, sx1, sy1 = span["bbox"]
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        return any(
            tx0 <= cx <= tx1 and ty0 <= cy <= ty1
            for tx0, ty0, tx1, ty1 in table_bboxes
        )

    return [s for s in spans if not in_table(s)]


def _detect_column_boundaries(spans: list[dict], page_width: float) -> list[float]:
    """
    Return normalized column boundary X positions.
    A boundary exists where there's a gap > 10% of page width
    in the horizontal distribution of span starts, within the middle 40% of the page.
    """
    if page_width == 0 or not spans:
        return []

    xs = sorted({round(s["x"] / page_width, 2) for s in spans if 0.02 < s["x"] / page_width < 0.98})
    boundaries = []
    for i in range(1, len(xs)):
        gap = xs[i] - xs[i - 1]
        if gap > 0.10 and 0.30 < xs[i] < 0.70:
            boundaries.append((xs[i - 1] + xs[i]) / 2)
    return boundaries[:2]  # cap at 3 columns


def _column_of(x: float, page_width: float, boundaries: list[float]) -> int:
    if not boundaries or page_width == 0:
        return 0
    rel = x / page_width
    for i, b in enumerate(boundaries):
        if rel < b:
            return i
    return len(boundaries)


def _heading_level(size: float, bold: bool, median: float, text: str, centered: bool) -> int | None:
    ratio = size / median if median else 1.0
    is_caps = text.isupper() and len(text) > 3

    if ratio >= 2.0:
        return 1
    if ratio >= 1.6:
        return 2
    if ratio >= 1.3:
        return 3
    if ratio >= 1.15 and (bold or is_caps):
        return 4
    if ratio >= 1.05 and bold:
        return 5
    if is_caps and centered and len(text) < 80:
        return 4
    return None


def _apply_page_ocr(png_bytes: bytes, page_num: int, blocks: list[Block]) -> None:
    """Run Tesseract on a rasterized page and append paragraph blocks."""
    try:
        import io
        import re
        from PIL import Image
        from .image import _try_ocr
        pil_img = Image.open(io.BytesIO(png_bytes))
        ocr_text = _try_ocr(pil_img)
        if not ocr_text:
            return
        for chunk in re.split(r"\n{2,}", ocr_text):
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            para = " ".join(lines)
            if para and len(para) >= 10:
                blocks.append(Block(
                    type=BlockType.PARAGRAPH,
                    content=para,
                    page=page_num,
                    index=0,
                ))
    except Exception:
        logger.debug("OCR failed on page %d", page_num, exc_info=True)


def _process_raw_page(
    raw: RawPage,
    removable: set[str],
    median: float,
) -> tuple[int, list[Block], list[Asset]]:
    """
    Pure Python processing of one page's extracted data.
    No PyMuPDF calls — safe to run in a thread pool.
    """
    blocks: list[Block] = []

    # Tables come first — extracted cleanly by PyMuPDF's find_tables()
    table_bboxes = []
    for t in raw.tables:
        blocks.append(Block(
            type=BlockType.TABLE,
            content=t["markdown"],
            page=raw.page_num,
            index=0,
        ))
        table_bboxes.append(t["bbox"])

    # Remove spans that overlap with already-extracted tables
    spans = _filter_table_spans(raw.spans, table_bboxes)

    # Detect multi-column layout and sort spans into reading order
    boundaries = _detect_column_boundaries(spans, raw.width)
    spans = sorted(spans, key=lambda s: (_column_of(s["x"], raw.width, boundaries), s["y"]))

    current_parts: list[str] = []

    def flush() -> None:
        text = " ".join(current_parts).strip()
        if text:
            blocks.append(Block(
                type=BlockType.PARAGRAPH,
                content=text,
                page=raw.page_num,
                index=0,
            ))
        current_parts.clear()

    for span in spans:
        text = span["text"]
        if text in removable:
            continue

        rel_y = span["y"] / raw.height if raw.height > 0 else 0.5
        centered = raw.width > 0 and 0.2 < span["x"] / raw.width < 0.8

        # Footnote: small font in the lower portion of the page
        if (
            span["size"] < median * _FOOTNOTE_SIZE_RATIO
            and _FOOTNOTE_ZONE_START < rel_y < _FOOTER_ZONE
        ):
            flush()
            blocks.append(Block(
                type=BlockType.FOOTNOTE,
                content=text,
                page=raw.page_num,
                index=0,
            ))
            continue

        # Caption: "Figure N", "Table N", "Fig. N", etc.
        if _CAPTION_RE.match(text):
            flush()
            blocks.append(Block(
                type=BlockType.CAPTION,
                content=text,
                page=raw.page_num,
                index=0,
            ))
            continue

        level = _heading_level(span["size"], span["bold"], median, text, centered)
        if level is not None:
            flush()
            blocks.append(Block(
                type=BlockType.HEADING,
                content=text,
                level=level,
                page=raw.page_num,
                index=0,
            ))
        else:
            current_parts.append(text)

    flush()

    if raw.ocr_pixmap is not None:
        _apply_page_ocr(raw.ocr_pixmap, raw.page_num, blocks)

    assets = [
        Asset(
            id=hashlib.sha256(f"{raw.page_num}:{img['xref']}".encode()).hexdigest()[:12],
            type="image",
            page=raw.page_num,
            metadata={"xref": img["xref"]},
        )
        for img in raw.images
    ]

    return raw.page_num, blocks, assets


class PDFParser(ParserPlugin):
    name = "pdf_parser"
    supported_types = ["pdf"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        pdf = fitz.open(str(path))
        page_count = pdf.page_count

        # Phase 1: Sequential I/O — extract raw data from PyMuPDF (not thread-safe)
        raw_pages = [_extract_raw_page(pdf, i + 1) for i in range(page_count)]
        pdf_metadata = dict(pdf.metadata)
        pdf.close()

        # Phase 2: Global analysis
        median = _median_font_size(raw_pages)
        removable = _detect_removable_spans(raw_pages)

        # Phase 3: Parallel processing — pure Python, no shared state
        results: dict[int, tuple[list[Block], list[Asset]]] = {}
        workers = min(8, max(1, page_count))

        if workers > 1 and page_count > 4:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_process_raw_page, raw, removable, median): raw.page_num
                    for raw in raw_pages
                }
                for future in as_completed(futures):
                    page_num, blocks, assets = future.result()
                    results[page_num] = (blocks, assets)
        else:
            for raw in raw_pages:
                page_num, blocks, assets = _process_raw_page(raw, removable, median)
                results[page_num] = (blocks, assets)

        # Phase 4: Assemble in page order and assign final indices
        all_blocks: list[Block] = []
        all_assets: list[Asset] = []
        idx = 0
        for page_num in sorted(results):
            blocks, assets = results[page_num]
            for block in blocks:
                all_blocks.append(block.model_copy(update={"index": idx}))
                idx += 1
            all_assets.extend(assets)

        doc = Document(
            source=str(path),
            file_type="pdf",
            title=pdf_metadata.get("title") or path.stem,
            author=pdf_metadata.get("author") or None,
            pages=page_count,
            blocks=all_blocks,
            assets=all_assets,
            metadata=pdf_metadata,
        )
        doc.compute_id()
        ctx.document = doc
        return ctx


register_parser("pdf", PDFParser)
