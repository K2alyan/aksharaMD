from __future__ import annotations

import hashlib
import logging
import os
import re
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

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
    lines = [ln for ln in markdown.strip().splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    cols = [c for c in lines[0].split("|") if c.strip()]
    if len(cols) < 2:
        return False
    data_rows = [ln for ln in lines[2:] if "|" in ln and not ln.startswith("|---")]
    return bool(data_rows)


def _cells_to_markdown(cells: list[list]) -> str:
    """Convert a 2-D cell grid (from tab.extract()) to a Markdown table string.

    Merged-cell ghost values (where a cell repeats the value from the row above)
    are blanked in header rows so financial multi-row headers render cleanly.
    """
    if not cells:
        return ""

    def norm(v) -> str:
        return re.sub(r"\s+", " ", (v or "").replace("|", "\\|")).strip()

    rows = [[norm(c) for c in row] for row in cells]
    ncols = max((len(r) for r in rows), default=0)
    if ncols == 0:
        return ""
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    # Blank ghost cells in the header zone (before the first row with numeric data).
    # A cell that repeats the value directly above it is a merged-cell artefact.
    first_data = next(
        (i for i, r in enumerate(rows) if any(re.search(r"\d", c) for c in r)),
        len(rows),
    )
    for i in range(1, first_data):
        for j in range(ncols):
            if rows[i][j] and rows[i][j] == rows[i - 1][j]:
                rows[i][j] = ""

    sep = ["---"] * ncols
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


_PDFPLUMBER_TEXT_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_x_tolerance": 3,
    "snap_y_tolerance": 3,
    "min_words_vertical": 2,    # at least 2 aligned words needed to declare a column
    "min_words_horizontal": 1,
}


def _try_pdfplumber_tables(
    pdf_pl,
    page_num: int,
    total_chars: int,
    page_height: float,
) -> list[dict]:
    """Use pdfplumber to detect borderless (whitespace-aligned) tables.

    Called only when PyMuPDF's ruled-line detector found nothing.
    Uses the text strategy — clusters column boundaries by x-coordinate rather
    than looking for ruling lines. Catches financial statements, government forms,
    and any table laid out with tab/space alignment.

    Bboxes are converted from pdfplumber's top-left origin to PyMuPDF's
    bottom-left origin so _filter_table_spans removes the right text spans.
    """
    if total_chars > _PDFPLUMBER_CHAR_LIMIT or total_chars < _OCR_TEXT_THRESHOLD:
        return []
    try:
        pl_page = pdf_pl.pages[page_num - 1]
        results = []
        for tbl in pl_page.find_tables(table_settings=_PDFPLUMBER_TEXT_SETTINGS):
            # Filter entirely-empty rows that pdfplumber inserts for inter-row gaps
            cells = [row for row in tbl.extract() if any(c for c in row)]
            md = _cells_to_markdown(cells)
            if not md or not _is_quality_table(md):
                continue
            x0, top, x1, bottom = tbl.bbox
            # pdfplumber: y measured from top-left; PyMuPDF: y measured from bottom-left
            pymupdf_bbox = (x0, page_height - bottom, x1, page_height - top)
            results.append({"markdown": md, "bbox": pymupdf_bbox})
        return results
    except Exception:
        logger.debug("pdfplumber table extraction failed on page %d", page_num, exc_info=True)
        return []


_OCR_TEXT_THRESHOLD = 50    # chars below which a full-page rasterisation is done
_EMBEDDED_OCR_THRESHOLD = 300  # chars below which embedded images are individually OCR'd
# 200 DPI meaningfully improves Tesseract accuracy over 150 on typical A4/Letter scans.
# Override with OMNIMARK_OCR_DPI env var (e.g. 300 for high-quality archival PDFs).
_OCR_DPI = int(os.getenv("OMNIMARK_OCR_DPI", "200"))
_EMBED_MIN_PX = 100         # ignore embedded images smaller than 100×100 px (decorative)
_MAX_CONTENT_IMAGE_BYTES = 2 * 1024 * 1024  # skip images > 2 MB (very high-res raw scans)
_MAX_IMAGES_PER_PAGE = 3
_MAX_TOTAL_IMAGES = 20
# pdfplumber fallback: skip pages denser than this (unlikely to have a missed table)
_PDFPLUMBER_CHAR_LIMIT = 3000


class RawPage(NamedTuple):
    page_num: int
    spans: list[dict]
    tables: list[dict]
    images: list[dict]
    height: float
    width: float
    ocr_pixmap: bytes | None = None       # PNG bytes when page has < _OCR_TEXT_THRESHOLD chars
    embedded_image_bytes: list[bytes] = []  # per-image bytes for image-heavy pages (OCR use)
    content_images: list[tuple[str, bytes]] = []  # (asset_id, bytes) for multimodal output


def _extract_raw_page(pdf: fitz.Document, page_num: int, pdf_pl=None) -> RawPage:
    """Sequential I/O pass — must run in the main thread (PyMuPDF not thread-safe).

    pdf_pl: optional open pdfplumber.PDF for borderless-table fallback.
    """
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
                cells = tab.extract()
                md = _cells_to_markdown(cells)
                if md and _is_quality_table(md):
                    tables.append({"markdown": md, "bbox": tuple(tab.bbox)})
        except Exception:
            logger.debug("find_tables() failed on page %d", page_num, exc_info=True)

    images = [
        {"xref": img[0], "img_index": i}
        for i, img in enumerate(page.get_images(full=True))
    ]

    total_chars = sum(len(s["text"]) for s in spans)

    # pdfplumber fallback: detect borderless (whitespace-aligned) tables when
    # PyMuPDF found nothing and the page has enough text to plausibly contain one.
    if not tables and pdf_pl is not None:
        tables = _try_pdfplumber_tables(pdf_pl, page_num, total_chars, page.rect.height)

    ocr_pixmap: bytes | None = None
    if total_chars < _OCR_TEXT_THRESHOLD:
        try:
            pix = page.get_pixmap(dpi=_OCR_DPI)
            ocr_pixmap = pix.tobytes("png")
        except Exception:
            logger.debug("Rasterization failed on page %d", page_num, exc_info=True)

    # Extract bytes for embedded images on image-heavy pages.
    # Only when _OCR_TEXT_THRESHOLD <= chars < _EMBEDDED_OCR_THRESHOLD (if chars < threshold
    # we already OCR the full rasterised page, so per-image OCR would be redundant).
    _PIL_SUPPORTED_EXTS = {"png", "jpeg", "jpg", "bmp", "tiff", "tif", "webp"}
    embedded_image_bytes: list[bytes] = []
    if _OCR_TEXT_THRESHOLD <= total_chars < _EMBEDDED_OCR_THRESHOLD and images:
        for img_info in page.get_images(full=True):
            if len(embedded_image_bytes) >= _MAX_IMAGES_PER_PAGE:
                break
            try:
                xref = img_info[0]
                img_dict = pdf.extract_image(xref)
                w = img_dict.get("width", 0)
                h = img_dict.get("height", 0)
                if w < _EMBED_MIN_PX or h < _EMBED_MIN_PX:
                    continue
                ext = img_dict.get("ext", "").lower()
                if ext in _PIL_SUPPORTED_EXTS:
                    raw_bytes = img_dict.get("image")
                else:
                    # Unsupported codec (JBIG2, CCITT, etc.) — decode via Pixmap to PNG
                    pix = fitz.Pixmap(pdf, xref)
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    raw_bytes = pix.tobytes("png")
                if raw_bytes:
                    embedded_image_bytes.append(raw_bytes)
            except Exception:
                logger.debug("Image extraction failed on page %d xref %s",
                             page_num, img_info[0], exc_info=True)

    # Extract content images for multimodal output
    content_images: list[tuple[str, bytes]] = []
    if total_chars < _OCR_TEXT_THRESHOLD and ocr_pixmap is not None:
        # Scanned page — the full raster IS the content image
        asset_id = hashlib.sha256(f"{page_num}:raster".encode()).hexdigest()[:12]
        content_images.append((asset_id, ocr_pixmap))
    elif total_chars >= _OCR_TEXT_THRESHOLD and images:
        # Text page — extract individual significant embedded images
        img_count = 0
        for img_info in page.get_images(full=True):
            if img_count >= _MAX_IMAGES_PER_PAGE:
                break
            try:
                xref = img_info[0]
                img_dict = pdf.extract_image(xref)
                w = img_dict.get("width", 0)
                h = img_dict.get("height", 0)
                raw_bytes = img_dict.get("image", b"")
                if (w >= _EMBED_MIN_PX and h >= _EMBED_MIN_PX
                        and raw_bytes and len(raw_bytes) <= _MAX_CONTENT_IMAGE_BYTES):
                    asset_id = hashlib.sha256(f"{page_num}:{xref}".encode()).hexdigest()[:12]
                    content_images.append((asset_id, raw_bytes))
                    img_count += 1
            except Exception:
                logger.debug("Content image extraction failed on page %d", page_num, exc_info=True)

    return RawPage(
        page_num=page_num,
        spans=spans,
        tables=tables,
        images=images,
        height=page.rect.height,
        width=page.rect.width,
        ocr_pixmap=ocr_pixmap,
        embedded_image_bytes=embedded_image_bytes,
        content_images=content_images,
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


def _heading_level(size: float, bold: bool, median: float, text: str, centered: bool, has_toc: bool = False) -> int | None:
    ratio = size / median if median else 1.0
    is_caps = text.isupper() and len(text) > 3

    if has_toc:
        # Real TOC exists → only trust strongly dominant font sizes; suppress noisy small headings
        if ratio >= 2.0:
            return 1
        if ratio >= 1.6:
            return 2
        return None

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
    """Run Tesseract on a rasterized page and append heading/paragraph blocks."""
    try:
        import io

        from PIL import Image

        from .image import _try_ocr_structured
        pil_img = Image.open(io.BytesIO(png_bytes))
        for block_type, content, level in _try_ocr_structured(pil_img):
            blocks.append(Block(
                type=block_type,
                content=content,
                level=level,
                page=page_num,
                index=0,
            ))
    except Exception:
        logger.debug("OCR failed on page %d", page_num, exc_info=True)


def _process_raw_page(
    raw: RawPage,
    removable: set[str],
    median: float,
    has_toc: bool = False,
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

        level = _heading_level(span["size"], span["bold"], median, text, centered, has_toc=has_toc)
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

    for img_bytes in raw.embedded_image_bytes:
        _apply_page_ocr(img_bytes, raw.page_num, blocks)

    # Add IMAGE blocks for content images (after text, for multimodal output)
    for asset_id, _img_bytes in raw.content_images:
        blocks.append(Block(
            type=BlockType.IMAGE, content="",
            page=raw.page_num, index=0,
            metadata={"asset_id": asset_id},
        ))

    assets = [
        Asset(
            id=asset_id,
            type="image",
            page=raw.page_num,
            image_bytes=img_bytes,
        )
        for asset_id, img_bytes in raw.content_images
    ]

    return raw.page_num, blocks, assets


class PDFParser(ParserPlugin):
    name = "pdf_parser"
    supported_types = ["pdf"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        try:
            pdf = fitz.open(str(path))
        except Exception as exc:
            ctx.error("PARSE_FAILED", f"Could not open PDF: {exc}")
            return ctx
        page_count = pdf.page_count

        # Open pdfplumber alongside fitz for borderless-table fallback.
        # Both must be used in Phase 1 (sequential) since neither is thread-safe.
        pdf_pl = None
        try:
            import pdfplumber
            pdf_pl = pdfplumber.open(str(path))
        except Exception:
            logger.debug("pdfplumber unavailable; borderless-table fallback disabled")

        # Phase 1: Sequential I/O — extract raw data from PyMuPDF (not thread-safe)
        try:
            raw_pages = [_extract_raw_page(pdf, i + 1, pdf_pl) for i in range(page_count)]
        finally:
            if pdf_pl is not None:
                pdf_pl.close()

        pdf_metadata = dict(pdf.metadata)
        toc = pdf.get_toc()  # [[level, title, page], ...]
        pdf.close()

        # Phase 2: Global analysis
        median = _median_font_size(raw_pages)
        removable = _detect_removable_spans(raw_pages)
        has_toc = len(toc) >= 3

        # Phase 3: Parallel processing — pure Python, no shared state
        results: dict[int, tuple[list[Block], list[Asset]]] = {}
        workers = min(8, max(1, page_count))

        if workers > 1 and page_count > 4:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_process_raw_page, raw, removable, median, has_toc): raw.page_num
                    for raw in raw_pages
                }
                for future in as_completed(futures):
                    page_num, blocks, assets = future.result()
                    results[page_num] = (blocks, assets)
        else:
            for raw in raw_pages:
                page_num, blocks, assets = _process_raw_page(raw, removable, median, has_toc)
                results[page_num] = (blocks, assets)

        # Phase 4: Assemble in page order and assign final indices
        all_blocks: list[Block] = []
        all_assets: list[Asset] = []
        idx = 0

        # Prepend bookmark-derived TOC when the PDF has real navigation entries
        if has_toc:
            toc_lines = ["**Contents**"]
            for level, title, page in toc:
                indent = "  " * (level - 1)
                toc_lines.append(f"{indent}- {title} (p. {page})")
            all_blocks.append(Block(
                type=BlockType.PARAGRAPH,
                content="\n".join(toc_lines),
                index=idx,
            ))
            idx += 1

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
