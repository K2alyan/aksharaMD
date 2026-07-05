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
from ...models.block import Block, BlockType, ExtractionConfidence
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_PAGE_NUM_RE = re.compile(
    r"^\d+$"
    r"|^\d+[-–]\d+$"
    r"|^-\s*\d+\s*-$"
    r"|^page\s+\d+(\s+of\s+\d+)?$"
    # Print timestamps from PDF authoring tools: "5/31/07 10:22 AM Page i"
    r"|^\d+/\d+/\d{2,4}\s+\d+:\d+\s*(AM|PM)\s+Page\s+\S+$",
    re.IGNORECASE,
)
_CID_RE = re.compile(r"\(cid:\d+\)")
# Narrower than _PAGE_NUM_RE: only strip print timestamps from table cells (not bare numbers)
_CELL_FURNITURE_RE = re.compile(
    r"^\d+/\d+/\d{2,4}\s+\d+:\d+\s*(AM|PM)\s+Page\s+\S+$"  # print timestamps
    r"|^page\s+\d+(\s+of\s+\d+)?$"                          # "Page 3 of 8"
    r"|^\d{4}\s+©",                                          # "2020 © Acme Inc."
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r"^(figure|fig\.?|table|exhibit|appendix)\s+\d",
    re.IGNORECASE,
)
# Bold body-font heading guard: spans starting with "Figure N" or "Table N"
# are figure/table captions, never section headings.
_BOLD_HDR_CAPTION_RE = re.compile(r"^(Figure|Table|Fig\.|Tab\.)\s", re.IGNORECASE)
# LaTeX \lineno detection: "1 S" header means line-number 1 bled into first char 'S'
_LINE_NUM_BLEED_RE = re.compile(r"^\d{1,3}\s+[A-Z]")
# Extract leading integer from a cell that may contain line-number + bleed text
_LINE_NUM_COL_RE = re.compile(r"^(\d{1,3})(\s.*)?$")

_HEADER_ZONE = 0.12
_FOOTER_ZONE = 0.88
_FOOTNOTE_ZONE_START = 0.72
_FOOTNOTE_SIZE_RATIO = 0.72
def _is_bold(flags: int) -> bool:
    return bool(flags & 2**4)


def _has_ruled_table(page: fitz.Page) -> bool:
    """
    Geometry pre-screen using interior line intersection analysis.

    The key distinction between a ruled table and a decorative page border:
    - A table has column-divider lines that cross row-divider lines at INTERIOR
      points (in the middle of the horizontal line, not at its endpoints).
    - A page border (rectangle) only produces corner intersections — the
      vertical sides hit the horizontal sides exactly at their endpoints.

    We collect all h-lines (y, x0, x1) and v-lines (x, y0, y1) from drawings,
    then count intersections where the v-line x is strictly inside the h-line
    span (hx0 + tol < vx < hx1 - tol). Three or more such interior crossings
    confirms a genuine grid rather than a border or decorative frame.
    """
    h_lines: list[tuple[float, float, float]] = []  # (y, x0, x1)
    v_lines: list[tuple[float, float, float]] = []  # (x, y0, y1)

    for path in page.get_drawings():
        for item in path.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                if dx > 20 and dy < 3:
                    h_lines.append(((p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x)))
                elif dy > 10 and dx < 3:
                    v_lines.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif item[0] == "re":
                r = item[1]
                if r.width > 30 and r.height > 5:
                    h_lines.append((r.y0, r.x0, r.x1))
                    h_lines.append((r.y1, r.x0, r.x1))
                    v_lines.append((r.x0, r.y0, r.y1))
                    v_lines.append((r.x1, r.y0, r.y1))

    if not h_lines or not v_lines:
        return False

    return _has_interior_intersections(h_lines, v_lines)


def _has_interior_intersections(
    h_lines: list[tuple[float, float, float]],
    v_lines: list[tuple[float, float, float]],
    tol: float = 5.0,
    threshold: int = 3,
) -> bool:
    """Return True when h_lines/v_lines contain ≥ threshold interior crossings.

    An interior crossing is one where the vertical line's x falls strictly inside
    the horizontal line's x-span (not at the endpoints). This separates real table
    grids (which have column-dividers crossing multiple row-dividers internally)
    from decorative page borders (whose corner intersections are at endpoints).

    Exposed for testing without requiring a live fitz.Page object.
    """
    count = 0
    for hy, hx0, hx1 in h_lines:
        for vx, vy0, vy1 in v_lines:
            if vy0 - tol <= hy <= vy1 + tol and hx0 + tol < vx < hx1 - tol:
                count += 1
                if count >= threshold:
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

    # Very wide tables are almost always text blocks mis-detected as tables.
    # Legitimate document tables rarely exceed 8 columns.
    if len(cols) > 8:
        return False

    data_rows = [ln for ln in lines[2:] if "|" in ln and not ln.startswith("|---")]
    if not data_rows:
        return False

    # Reject TOC dot-leader rows: most rows contain "....." sequences
    dot_rows = sum(1 for r in data_rows if _TOC_DOT_RE.search(r))
    if dot_rows > len(data_rows) * 0.4:
        return False

    # Reject word-fragmentation from layout over-segmentation: cells that are
    # purely lowercase alphabetic and ≤4 chars are almost certainly word tails
    # split mid-word (e.g. "ore" from "Signore", "sy" from "Fantasy").
    all_cells = [c.strip() for row in data_rows for c in row.split("|") if c.strip()]
    if all_cells:
        short_alpha = sum(
            1 for c in all_cells
            if len(c) <= 4 and c.isalpha() and c.islower() and c not in _FRAG_WHITELIST
        )
        if short_alpha / len(all_cells) > 0.25:
            return False

    # Reject tables where >50% of data cells are empty — paragraph text forced
    # into layout columns leaves most cells blank (e.g. a 3-column layout where
    # prose sits in the first column and the others are empty spacers).
    total_data_cells = 0
    empty_data_cells = 0
    for row in data_rows:
        if "|" in row:
            inner = row.split("|")[1:-1]
            total_data_cells += len(inner)
            empty_data_cells += sum(1 for c in inner if not c.strip())
    if total_data_cells > 0 and empty_data_cells / total_data_cells > 0.5:
        return False

    # Reject tables where rows are clearly word-split across columns.
    # Pattern A: first non-empty cell in a row is a single letter AND the next
    # non-empty cell starts with lowercase (e.g. "Q" | "uotation #:").
    # Pattern B: >30% of adjacent cell pairs have (left ends alpha, right starts
    # lowercase), indicating wrapped paragraph text chopped into columns.
    if len(data_rows) >= 2:
        single_letter_split = 0
        adj_split = 0
        adj_total = 0
        for row in data_rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if len(cells) >= 2:
                if len(cells[0]) == 1 and cells[0].isalpha() and cells[1][0].islower():
                    single_letter_split += 1
                for i in range(len(cells) - 1):
                    adj_total += 1
                    if cells[i] and cells[i][-1].isalpha() and cells[i + 1][0].islower():
                        adj_split += 1
        # Include the header row in the adj_split count.  Cover-page bordered
        # layouts can produce word-split cells in the header itself — e.g.
        # "Company Nam L" | "e, Inc." — which pushes the combined ratio over
        # 30% even when the data-row ratio alone is just below the threshold.
        for i in range(len(cols) - 1):
            adj_total += 1
            left = cols[i].strip()
            right = cols[i + 1].strip()
            if left and left[-1].isalpha() and right and right[0].islower():
                adj_split += 1
        if single_letter_split / len(data_rows) > 0.2:
            return False
        if adj_total and adj_split / adj_total > 0.3:
            return False

    # Reject LaTeX \lineno line-number tables.  Two patterns:
    # A) Header first cell is "N LETTER" — line-number bled into first char of text
    #    (e.g. "1 S" = line 1 starting with 'S' of "Supplementary").
    # B) ≤3-column table whose header first cell is a small bare integer (≤20).
    #    pdfplumber whitespace-strategy detects line numbers as a left column.
    hdr_first = cols[0].strip()
    if _LINE_NUM_BLEED_RE.match(hdr_first):
        return False
    if len(cols) <= 3 and hdr_first.isdigit() and 1 <= int(hdr_first) <= 20:
        return False

    return True


def _cells_to_markdown(cells: list[list]) -> str:
    """Convert a 2-D cell grid (from tab.extract()) to a Markdown table string.

    Merged-cell ghost values (where a cell repeats the value from the row above)
    are blanked in header rows so financial multi-row headers render cleanly.
    """
    if not cells:
        return ""

    def norm(v) -> str:
        text = re.sub(r"\s+", " ", _CID_RE.sub("", (v or "")).replace("|", "\\|")).strip()
        return "" if _CELL_FURNITURE_RE.match(text) else text

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
    "min_words_vertical": 3,    # at least 3 aligned words to declare a column (was 2)
    "min_words_horizontal": 3,  # at least 3 words per row to form a table (was 1)
}
_TOC_DOT_RE = re.compile(r"\.{5,}")  # 5+ consecutive dots = dot-leader; excludes ellipsis (…)
# Common legitimate short lowercase table values — excluded from word-fragment detection
_FRAG_WHITELIST = frozenset({
    "yes", "no", "na", "n/a", "tbd", "low", "mid", "high", "all", "and",
    "or", "per", "vs", "avg", "max", "min", "sum", "net", "the", "for",
})


def _filter_latex_line_numbers(spans: list[dict], page_width: float) -> list[dict]:
    """Remove LaTeX \\lineno package margin line numbers from the span list.

    Line numbers appear as small integers at x < 8 % of page width in a
    monotonically increasing sequence (the lineno package numbers every line or
    every N-th line).  They are metadata, not document content, and should not
    appear as paragraph text or trigger false table detection.
    """
    if page_width == 0 or not spans:
        return spans
    left_int_spans = [
        s for s in spans
        if s["x"] / page_width < 0.08
        and s["text"].isdigit()
        and 1 <= int(s["text"]) <= 999
    ]
    if len(left_int_spans) < 6:
        return spans
    vals = sorted(int(s["text"]) for s in left_int_spans)
    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    # Accept only sequences where the average step is ≤ 3 (handles "every 5th
    # line" numbering style).  A larger average means the integers are data, not
    # line numbers.
    if not diffs or sum(diffs) / len(diffs) > 3.0:
        return spans
    to_remove = {id(s) for s in left_int_spans}
    return [s for s in spans if id(s) not in to_remove]


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
# Override with AKSHARAMD_OCR_DPI env var (e.g. 300 for high-quality archival PDFs).
_OCR_DPI = int(os.getenv("AKSHARAMD_OCR_DPI", "200"))
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
                text = _CID_RE.sub("", span["text"]).strip()
                if text:
                    spans.append({
                        "text": text,
                        "size": span["size"],
                        "bold": _is_bold(span.get("flags", 0)),
                        "y": span["origin"][1],
                        "x": span["origin"][0],
                        "bbox": span["bbox"],
                    })

    # Strip LaTeX \lineno margin line-numbers before further processing so they
    # don't pollute table cells or paragraph text.
    spans = _filter_latex_line_numbers(spans, page.rect.width)

    tables = []
    if _has_ruled_table(page):
        try:
            for tab in page.find_tables():
                cells = tab.extract()
                md = _cells_to_markdown(cells)
                if md and _is_quality_table(md):
                    tables.append({"markdown": md, "bbox": tuple(tab.bbox), "source": "ruled"})
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
        for tbl in _try_pdfplumber_tables(pdf_pl, page_num, total_chars, page.rect.height):
            tables.append({**tbl, "source": "whitespace"})

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
            rel_y = span["y"] / raw.height if raw.height > 0 else 0.5
            # Bare digits (^\d+$) are only treated as page numbers when they
            # appear in the header or footer zone.  Bare "1" mid-page is a
            # quantity, footnote number, or list item — not a page number.
            # Other _PAGE_NUM_RE patterns (ranges, "Page N of M", timestamps)
            # are structural noise regardless of position and are always removed.
            if _PAGE_NUM_RE.match(text):
                is_bare_digit = bool(re.match(r"^\d+$", text))
                in_zone = rel_y < _HEADER_ZONE or rel_y > _FOOTER_ZONE
                if not is_bare_digit or in_zone:
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

    # Minimum of 3 prevents false removal on 1–2 page documents (e.g. email PDFs
    # where the same amount appears in both the summary page and the receipt page).
    zone_threshold = max(3, int(page_count * 0.3))
    global_threshold = max(3, int(page_count * 0.4))

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
    """Drop spans that fall inside a detected table bounding box.

    Uses center-point check with a 6pt margin so spans that bleed slightly
    outside the detected table boundary are still suppressed, preventing the
    same text appearing as both a table row and a prose paragraph.
    """
    if not table_bboxes:
        return spans

    _MARGIN = 6.0

    def in_table(span: dict) -> bool:
        sx0, sy0, sx1, sy1 = span["bbox"]
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        return any(
            tx0 - _MARGIN <= cx <= tx1 + _MARGIN and ty0 - _MARGIN <= cy <= ty1 + _MARGIN
            for tx0, ty0, tx1, ty1 in table_bboxes
        )

    return [s for s in spans if not in_table(s)]


def _detect_column_boundaries(spans: list[dict], page_width: float) -> list[float]:
    """
    Return normalized column boundary X positions.

    Uses line-start x-positions (leftmost span per text line) rather than all
    span x-positions. Individual spans are spread uniformly across page width
    (one per word/run), while line starts cluster sharply at column left-margins,
    making two-column layouts like arXiv papers detectable.

    Lines are grouped by y-coordinate with 3 pt tolerance to handle sub/superscripts.
    """
    if page_width == 0 or not spans:
        return []

    # Collect the leftmost x per line (line start = column left-margin proxy)
    sorted_spans = sorted(spans, key=lambda s: s["y"])
    line_starts: list[float] = []
    line_y: float | None = None
    line_min_x: float = float("inf")
    for s in sorted_spans:
        y = s["y"]
        if line_y is None or abs(y - line_y) > 3:
            if line_y is not None and line_min_x < float("inf"):
                line_starts.append(line_min_x / page_width)
            line_y = y
            line_min_x = s["x"]
        else:
            if s["x"] < line_min_x:
                line_min_x = s["x"]
    if line_y is not None and line_min_x < float("inf"):
        line_starts.append(line_min_x / page_width)

    xs = sorted({round(x, 2) for x in line_starts if 0.02 < x < 0.98})
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
    # isupper() returns True if ALL cased characters are uppercase.  Exclude
    # geographic abbreviations like "CA, USA." by requiring no mixed punctuation.
    is_caps = text.isupper() and len(text) > 3 and not ("," in text and "." in text)

    # Prose signals that indicate this span is body text, not a heading:
    #   - starts with lowercase or punctuation → mid-sentence fragment
    #   - ends in comma/semicolon → sentence continues on the next line
    #   - contains a URL → metadata annotation, never a heading
    _prose = bool(
        (text and (text[0].islower() or text[0] in ".,;:("))
        or text.endswith(",")
        or text.endswith(";")
        or "http" in text
    )

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
        # In 2-column journals, footnote/reference text pulls the document median
        # down so body-text spans appear at ratio >= 1.3.  Only accept as H3 if
        # there is clear heading evidence (bold/caps) and no prose signals.
        # At higher ratios (>= 1.5), short unlabelled headings (e.g. arXiv-style)
        # are also accepted.
        if not _prose:
            if bold or is_caps:
                return 3
            if ratio >= 1.5 and len(text.split()) <= 5:
                return 3
    if ratio >= 1.15 and not _prose and (bold or is_caps):
        return 4
    if ratio >= 1.05 and bold and not _prose:
        return 5
    if is_caps and centered and not _prose and len(text) < 80:
        return 4
    # Bold body-font heading: same size as body text but bold and short.
    # Catches unlabelled section headings like "Introduction", "Phase I",
    # "Problem Statement" that sit at ratio ≈ 1.0 and would otherwise be
    # absorbed into paragraph text.  Only fires when no TOC is present
    # (has_toc path returns early above).
    # Guards:
    #   - not ending ":" → keeps "Note:", "Warning:" as labels, not headings
    #   - not ending "." → keeps unit labels ("2000 CFM.") and caption
    #     continuations from being promoted
    #   - len(text) >= 3 → excludes bare single/double letter section markers
    #   - not starting "Figure"/"Table" → figure/table captions are not headings
    _words = text.split()
    if (
        bold
        and not _prose
        and not text.endswith(":")
        and not text.endswith(".")
        and len(text) >= 3
        and not _BOLD_HDR_CAPTION_RE.match(text)
        and 1 <= len(_words) <= 4
        # Single all-caps tokens are abbreviations/units ("CFM", "SMACNA"),
        # not section headings.  Multi-word all-caps ("APPENDIX B") are fine.
        and not (len(_words) == 1 and text.isupper())
    ):
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
                confidence=ExtractionConfidence.AMBIGUOUS,
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

    # Tables come first — ruled tables are EXTRACTED; whitespace-inferred are INFERRED
    table_bboxes = []
    for t in raw.tables:
        tbl_confidence = (
            ExtractionConfidence.EXTRACTED if t.get("source") != "whitespace"
            else ExtractionConfidence.INFERRED
        )
        blocks.append(Block(
            type=BlockType.TABLE,
            content=t["markdown"],
            page=raw.page_num,
            index=0,
            confidence=tbl_confidence,
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

    prev_text_span: dict | None = None  # last span that was appended to current_parts

    for span in spans:
        text = span["text"]
        if text in removable:
            continue

        # Paragraph-break detection: a vertical gap larger than 1.8 × the previous
        # span's font size within the same column signals a new paragraph.  This
        # separates distinct paragraphs that share no heading or caption to act as
        # a natural separator — common in 2-column academic papers.
        if prev_text_span is not None and current_parts:
            curr_col = _column_of(span["x"], raw.width, boundaries)
            prev_col = _column_of(prev_text_span["x"], raw.width, boundaries)
            if curr_col == prev_col:
                gap = span["y"] - prev_text_span["y"]
                if gap > prev_text_span["size"] * 1.8:
                    flush()

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
            prev_text_span = None
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
            prev_text_span = None
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
                confidence=ExtractionConfidence.INFERRED,  # inferred from font size/bold, not a markup heading
            ))
            prev_text_span = None
        else:
            current_parts.append(text)
            prev_text_span = span

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
