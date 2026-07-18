from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import os
import re
import statistics
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import NamedTuple

# pymupdf 1.28+ emits an informational one-time notice on stdout
# ("Consider using the pymupdf_layout package...") during document
# parse, which pollutes `aksharamd compile --json` output. Suppress it
# via the documented opt-out environment variable BEFORE the import so
# the module-level check inside pymupdf's _warn_layout_once picks it up.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

import fitz  # PyMuPDF

# Belt-and-braces: also route any other pymupdf messages to stderr so
# the JSON contract holds even if a future pymupdf release adds a new
# stdout-writing notice. Older pymupdf releases (< 1.26) lack
# set_messages — tolerate that with try/except.
try:
    import sys as _sys
    fitz.set_messages(stream=_sys.stderr)
except AttributeError:
    pass

logger = logging.getLogger(__name__)


def _tesseract_available() -> bool:
    """Return True if pytesseract and its Tesseract binary are accessible.

    Delegates to image._configure_tesseract() so the Windows install path
    (C:\\Program Files\\Tesseract-OCR\\tesseract.exe) is auto-detected even when
    the binary is not on PATH.
    """
    from .image import _configure_tesseract
    return _configure_tesseract()


_TESSERACT_AVAILABLE: bool | None = None  # lazily cached


def _ocr_available() -> bool:
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is None:
        _TESSERACT_AVAILABLE = _tesseract_available()
    return _TESSERACT_AVAILABLE


# ── Marker (vision) availability ──────────────────────────────────────────────
# SECURITY: The marker integration below MUST NOT accept user-controlled model
# identifiers, `from_pretrained(...)` targets, or `trust_remote_code` toggles.
# `create_model_dict()` is always called with no arguments so the resolved
# checkpoints stay pinned to marker/surya's hardcoded set. Adding any dynamic
# model-selection surface here would expose transformers CVEs that are
# currently unreachable (see SECURITY.md "Deferred Dependency Alerts").
_MARKER_AVAILABLE: bool | None = None
_MARKER_LOAD_ATTEMPTED: bool = False
_MARKER_MODELS: dict | None = None


def _marker_available() -> bool:
    """Return True if marker-pdf is installed (does not import or load models)."""
    global _MARKER_AVAILABLE
    if _MARKER_AVAILABLE is None:
        _MARKER_AVAILABLE = importlib.util.find_spec("marker") is not None
    return _MARKER_AVAILABLE


def _get_marker_models() -> dict | None:
    """Load and cache Marker models.  Returns None if unavailable or load fails.  On failure,
    sets a flag so subsequent calls within the same process return immediately without retrying.

    Models are downloaded from HuggingFace on first run (~3 GB).  For
    air-gapped environments, pre-cache by running once on a connected machine:
        python -c "from marker.models import create_model_dict; create_model_dict()"
    then copy ~/.cache/huggingface/hub/ to the target machine and set
    HF_HUB_OFFLINE=1 before running aksharamd.
    """
    global _MARKER_MODELS, _MARKER_LOAD_ATTEMPTED, _MARKER_AVAILABLE
    if _MARKER_LOAD_ATTEMPTED:
        return _MARKER_MODELS  # None on prior failure, dict on success
    if _MARKER_MODELS is not None:
        return _MARKER_MODELS
    if not _marker_available():
        _MARKER_LOAD_ATTEMPTED = True
        return None
    _MARKER_LOAD_ATTEMPTED = True
    try:
        from marker.models import create_model_dict
        logger.debug("Loading Marker vision models (first-time load, may take a moment)...")
        _MARKER_MODELS = create_model_dict()
        return _MARKER_MODELS
    except Exception as exc:
        logger.warning(
            "Marker models failed to load: %s. "
            "For air-gapped use, pre-cache with: "
            "python -c \"from marker.models import create_model_dict; create_model_dict()\" "
            "then set HF_HUB_OFFLINE=1.",
            exc,
        )
        _MARKER_AVAILABLE = False
        return None


_OCR_UNAVAILABLE_MSG = (
    "[Image not extracted — OCR unavailable. "
    "Install pytesseract and Tesseract to extract text from images: "
    "pip install aksharamd[ocr]"
)

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.block import Block, BlockType, ExtractionConfidence
from ...models.document import Document
from ...renderers.table_markdown import render_table_markdown as _render_table_markdown
from ..base import ParserPlugin
from ..registry import register_parser
from .pdf_tables.normalization import cell_bbox_from_spans, cells_to_tabledata
from .pdf_tables.stitching import stitch_page_break_tables as _stitch_page_break_tables

_PAGE_NUM_RE = re.compile(
    r"^\d+$"
    r"|^\d+[-–]\d+$"
    r"|^-\s*\d+\s*-$"
    r"|^page\s+\d+(\s+of\s+\d+)?$"
    # Web-print "X/N" pagination ("1/13", "12/13") — zone-restricted like bare digits
    r"|^\d+/\d+$"
    # Print timestamps from PDF authoring tools: "5/31/07 10:22 AM Page i"
    r"|^\d+/\d+/\d{2,4}\s+\d+:\d+\s*(AM|PM)\s+Page\s+\S+$"
    # Roman numeral page numbers (i–xxxix) in header/footer zones
    r"|^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$",
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
def _is_bold(flags: int, font_name: str = "") -> bool:
    if flags & 2**4:
        return True
    # Fallback: some PDFs embed bold as a named font variant without setting the flag.
    fname = font_name.lower()
    return any(tok in fname for tok in ("bold", "heavy", "black", "-bd", "+b", ",b"))


def _is_italic(flags: int, font_name: str = "") -> bool:
    if flags & 2**1:
        return True
    # Fallback: oblique/italic named variants that omit the standard flag.
    fname = font_name.lower()
    return any(tok in fname for tok in ("italic", "oblique", "slant", "-ital", "-it,"))


def _is_superscript(flags: int, font_name: str = "") -> bool:
    if flags & 2**0:  # PyMuPDF TEXT_FONT_SUPERSCRIPT
        return True
    fname = font_name.lower()
    return any(tok in fname for tok in ("super", "sup"))


def _is_subscript(font_name: str = "") -> bool:
    # No dedicated PyMuPDF flag for subscript; use font-name detection.
    # Geometric baseline-offset detection is a future improvement.
    fname = font_name.lower()
    return any(tok in fname for tok in ("subscript", "sub"))


def _is_monospace(flags: int, font_name: str = "") -> bool:
    if flags & 2**3:  # PyMuPDF TEXT_FONT_MONOSPACED
        return True
    fname = font_name.lower()
    return any(tok in fname for tok in (
        "mono", "courier", "consolas", "code", "typewriter",
        "inconsolata", "menlo", "monaco", "lucidaconsole", "fixedwidth",
    ))


def _apply_inline_fmt(
    text: str,
    bold: bool,
    italic: bool,
    strikethrough: bool,
    underline: bool,
    sup: bool = False,
    sub: bool = False,
) -> str:
    """Wrap text in markdown/HTML inline decoration markers."""
    if strikethrough:
        text = f"~~{text}~~"
    if underline:
        text = f"<u>{text}</u>"
    if bold and italic:
        text = f"***{text}***"
    elif bold:
        text = f"**{text}**"
    elif italic:
        text = f"*{text}*"
    if sup:
        text = f"<sup>{text}</sup>"
    elif sub:
        text = f"<sub>{text}</sub>"
    return text


def _tag_text_decorations(page: fitz.Page, spans: list[dict]) -> None:
    """Tag spans with underline/strikethrough detected from page drawing paths.

    PDF underline and strikethrough are typically drawn as separate thin
    horizontal paths rather than encoded as font flags. We detect them
    geometrically: a thin horizontal stroke at the text midpoint is
    strikethrough; one just below the text bbox bottom is underline.
    This runs only when drawings exist and is wrapped in a broad except so
    it can never break text extraction.
    """
    if not spans:
        return
    try:
        drawings = page.get_drawings()
    except Exception:
        return

    page_w = page.rect.width
    thin_lines: list[tuple[float, float, float, float]] = []
    for path in drawings:
        r = path.get("rect")
        if r is None:
            continue
        r_h = r[3] - r[1]
        r_w = r[2] - r[0]
        # Keep only thin horizontal strokes that are not full-page-width rules
        if r_h > 3.0 or r_w < 4.0 or r_w > page_w * 0.95:
            continue
        thin_lines.append((r[0], r[1], r[2], r[3]))

    if not thin_lines:
        return

    for span in spans:
        b = span["bbox"]            # (x0, y0, x1, y1)
        sp_h = b[3] - b[1]
        if sp_h <= 0:
            continue
        sp_cy = (b[1] + b[3]) / 2
        sp_w = b[2] - b[0]
        for lx0, _ly0, lx1, ly1 in thin_lines:
            # Horizontal overlap: line must cover at least half the span width
            overlap = min(lx1, b[2]) - max(lx0, b[0])
            if overlap < (b[2] - b[0]) * 0.4:
                continue
            # Table rulings span multiple columns and extend well past a single span.
            # Skip lines that reach more than one span-width beyond either edge —
            # a real text underline stays close to its word; a table rule does not.
            if sp_w > 0 and ((b[0] - lx0) > sp_w or (lx1 - b[2]) > sp_w):
                continue
            line_cy = (_ly0 + ly1) / 2
            # Underline: line sits at or just below text bottom
            if -sp_h * 0.3 <= (line_cy - b[3]) <= sp_h * 0.5:
                span["underline"] = True
            # Strikethrough: line crosses the vertical midpoint
            elif abs(line_cy - sp_cy) <= sp_h * 0.5:
                span["strikethrough"] = True


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
                if r.width > 30 and r.height > 1:
                    h_lines.append((r.y0, r.x0, r.x1))
                    h_lines.append((r.y1, r.x0, r.x1))
                    v_lines.append((r.x0, r.y0, r.y1))
                    v_lines.append((r.x1, r.y0, r.y1))

    if not h_lines:
        return False

    if v_lines and _has_interior_intersections(h_lines, v_lines):
        return True

    # Fallback for tables with only horizontal row-dividers and no vertical column
    # lines (columns separated by whitespace).  Three or more h-lines whose widths
    # are within 15% of the median width indicate parallel row-dividers in the same
    # table — not decorative rules at varying spans.  A single page-border rectangle
    # contributes only 2 h-lines (top + bottom), which is below the threshold of 3,
    # so it cannot trigger this path on its own.
    if len(h_lines) >= 3:
        widths = sorted(x1 - x0 for _, x0, x1 in h_lines)
        median_w = widths[len(widths) // 2]
        if median_w >= 50:  # ignore very short decorative rules
            similar = sum(1 for w in widths if abs(w - median_w) / median_w <= 0.15)
            if similar >= 3:
                return True

    return False


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


def _is_quality_table(markdown: str, _rejection_log: list[str] | None = None) -> bool:
    """Reject tables that are clearly noise: need ≥2 columns and ≥1 real data row.

    Optional _rejection_log: if provided, a reason code string is appended before
    each ``return False`` so callers can record why a candidate was rejected.
    """
    def _reject(reason: str) -> bool:
        if _rejection_log is not None:
            _rejection_log.append(reason)
        return False

    lines = [ln for ln in markdown.strip().splitlines() if ln.strip()]
    if len(lines) < 3:
        return _reject("too_short")
    cols = [c for c in lines[0].split("|") if c.strip()]
    if len(cols) < 2:
        return _reject("too_few_cols")

    # Very wide tables are almost always text blocks mis-detected as tables.
    # Financial statements and comparison grids can legitimately reach 12 columns.
    if len(cols) > 12:
        return _reject("too_many_cols")

    data_rows = [ln for ln in lines[2:] if "|" in ln and not ln.startswith("|---")]
    if not data_rows:
        return _reject("too_short")

    # Reject TOC dot-leader rows: most rows contain "....." sequences
    dot_rows = sum(1 for r in data_rows if _TOC_DOT_RE.search(r))
    if dot_rows > len(data_rows) * 0.4:
        return _reject("dot_leader")

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
            return _reject("word_fragment")

    # Reject tables where >65% of data cells are empty — paragraph text forced
    # into layout columns leaves most cells blank (e.g. a 3-column layout where
    # prose sits in the first column and the others are empty spacers).
    # 65% rather than 50% to permit legitimate sparse tables (N/A quarterly data,
    # optional fields) while still blocking multi-column prose layouts.
    total_data_cells = 0
    empty_data_cells = 0
    for row in data_rows:
        if "|" in row:
            inner = row.split("|")[1:-1]
            total_data_cells += len(inner)
            empty_data_cells += sum(1 for c in inner if not c.strip())
    if total_data_cells > 0 and empty_data_cells / total_data_cells > 0.65:
        return _reject("too_sparse")

    # Reject tables where data cells contain prose-length text — these are 2-column
    # page layouts (narrative chapters, cover text) that pdfplumber's text-strategy
    # detected as multi-column tables.  Real data table cells are short labels or
    # values; cells averaging > 12 words indicate sentence fragments across columns.
    # 12 rather than 8 allows product/spec tables with longer descriptive cells.
    if all_cells:
        word_counts = [len(c.split()) for c in all_cells]
        if sum(word_counts) / len(word_counts) > 12:
            return _reject("prose_cells")

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
            return _reject("single_letter_split")
        if adj_total and adj_split / adj_total > 0.3:
            return _reject("word_split")

    # Reject LaTeX \lineno line-number tables.  Two patterns:
    # A) Header first cell is "N LETTER" — line-number bled into first char of text
    #    (e.g. "1 S" = line 1 starting with 'S' of "Supplementary").
    # B) ≤3-column table whose header first cell is a small bare integer (≤20).
    #    pdfplumber whitespace-strategy detects line numbers as a left column.
    hdr_first = cols[0].strip()
    if _LINE_NUM_BLEED_RE.match(hdr_first):
        return _reject("line_number_bleed")
    if len(cols) <= 3 and hdr_first.isdigit() and 1 <= int(hdr_first) <= 20:
        return _reject("line_number_col")

    return True


def _is_repetitive_text(text: str, threshold: float = 0.15) -> bool:
    """Return True when text is OCR hallucination detected via 4-gram repetition.

    Marker produces endless "the state of the state of..." loops on unfamiliar
    scripts.  We measure what fraction of the text's 4-grams are duplicates: if
    more than `threshold` of all 4-grams are repeats of an earlier 4-gram, the
    block is hallucination.  Threshold=0.15 catches loops while leaving normal
    prose (which naturally repeats common function words) unaffected.
    """
    words = text.split()
    if len(words) < 16:
        return False
    grams = [tuple(words[i:i + 4]) for i in range(len(words) - 3)]
    seen: set[tuple] = set()
    duplicates = 0
    for g in grams:
        if g in seen:
            duplicates += 1
        else:
            seen.add(g)
    return duplicates / len(grams) > threshold


def _cells_to_markdown(cells: list[list]) -> str:
    """Convert a 2-D cell grid (from tab.extract()) to a Markdown table string.

    Merged-cell ghost values (where a cell repeats the value from the row above)
    are blanked in header rows so financial multi-row headers render cleanly.
    """
    if not cells:
        return ""

    def norm(v) -> str:
        text = (v or "")
        # Strip CID placeholders and replacement characters
        text = _CID_RE.sub("", text).replace("�", "")
        # Strip Unicode zero-width and formatting characters that corrupt cell matching
        text = re.sub(r"[­​‌‍﻿]", "", text)
        # Strip trailing footnote superscripts (e.g. "1,234¹" → "1,234", "Value²³" → "Value")
        text = re.sub(r"[\xb2\xb3\xb9⁰-⁹]+$", "", text)
        text = re.sub(r"\s+", " ", text.replace("|", "\\|")).strip()
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
    "intersection_tolerance": 3,  # tighter snapping reduces cell-text bleed across columns
    "min_words_vertical": 4,    # at least 4 aligned words to declare a column (was 5)
    "min_words_horizontal": 2,  # at least 2 words per row to form a table (was 3)
}
_TOC_DOT_RE = re.compile(r"\.{5,}")  # 5+ consecutive dots = dot-leader; excludes ellipsis (…)
# Common legitimate short lowercase table values — excluded from word-fragment detection
_FRAG_WHITELIST = frozenset({
    "yes", "no", "na", "n/a", "tbd", "low", "mid", "high", "all", "and",
    "or", "per", "vs", "avg", "max", "min", "sum", "net", "the", "for",
    # Common unit and measurement abbreviations — legitimate short table cell values
    "pct", "avg", "est", "lbs", "mph", "rpm", "hrs", "sec", "deg",
    "psi", "cfm", "gpm", "kpa", "bar", "kwh", "mbps", "qty", "ref",
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
    rejected_out: list[dict] | None = None,
) -> list[dict]:
    """Use pdfplumber to detect borderless (whitespace-aligned) tables.

    Called only when PyMuPDF's ruled-line detector found nothing.
    Uses the text strategy — clusters column boundaries by x-coordinate rather
    than looking for ruling lines. Catches financial statements, government forms,
    and any table laid out with tab/space alignment.

    Bboxes are converted from pdfplumber's top-left origin to PyMuPDF's
    bottom-left origin so _filter_table_spans removes the right text spans.

    rejected_out: optional list to accumulate rejected-candidate dicts.
    """
    if total_chars > _PDFPLUMBER_CHAR_LIMIT or total_chars < _OCR_TEXT_THRESHOLD:
        return []
    try:
        pl_page = pdf_pl.pages[page_num - 1]
        results = []
        for tbl in pl_page.find_tables(table_settings=_PDFPLUMBER_TEXT_SETTINGS):
            # Filter entirely-empty rows that pdfplumber inserts for inter-row gaps
            cells = [row for row in tbl.extract() if any(c for c in row)]
            if not cells:
                continue
            x0, top, x1, bottom = tbl.bbox
            # pdfplumber: y measured from top-left; PyMuPDF: y measured from bottom-left
            pymupdf_bbox = (x0, page_height - bottom, x1, page_height - top)
            td = cells_to_tabledata(
                cells,
                bbox=pymupdf_bbox,
                source="whitespace",
                page=page_num,
            )
            if td.row_count == 0:
                continue
            md = _render_table_markdown(td)
            rejection_log: list[str] = []
            if not md or not _is_quality_table(md, rejection_log):
                if rejected_out is not None:
                    # Compute quality metrics for the rejected candidate
                    dot_rows = 0
                    total_cells = 0
                    empty_cells = 0
                    if md:
                        md_lines = [ln for ln in md.strip().splitlines() if ln.strip()]
                        md_data = [ln for ln in md_lines[2:] if "|" in ln and not ln.startswith("|---")]
                        dot_rows = sum(1 for r in md_data if _TOC_DOT_RE.search(r))
                        for row in md_data:
                            inner = row.split("|")[1:-1]
                            total_cells += len(inner)
                            empty_cells += sum(1 for c in inner if not c.strip())
                    rejected_out.append({
                        "strategy": "whitespace",
                        "page": page_num,
                        "bbox": list(pymupdf_bbox),
                        "row_count": td.row_count,
                        "col_count": td.column_count,
                        "rejection_reasons": rejection_log,
                        "quality_metrics": {
                            "dot_leader_fraction": dot_rows / max(len([ln for ln in (md or "").strip().splitlines() if "|" in ln and not ln.startswith("|---")]), 1),
                            "empty_cell_fraction": empty_cells / max(total_cells, 1),
                            "col_count": td.column_count,
                        },
                    })
                continue
            results.append({"table_data": td, "bbox": pymupdf_bbox})
        return results
    except Exception:
        logger.debug("pdfplumber table extraction failed on page %d", page_num, exc_info=True)
        return []


_TBL_CAPTION_RE = re.compile(
    r'^\s*(Table|Figure|Fig\.\s*|Chart|Exhibit)\s+\S+', re.IGNORECASE
)


def _is_caption_row(cells: list[str]) -> bool:
    """Return True if this row is a table/figure caption label rather than a data row."""
    non_empty = [c.strip() for c in cells if c.strip()]
    if not non_empty:
        return False
    return bool(_TBL_CAPTION_RE.match(non_empty[0]))


def _try_hrule_table(
    page: fitz.Page,
    spans: list[dict],
    page_num: int = 0,
    rejected_out: list[dict] | None = None,
) -> list[dict]:
    """Detect booktabs-style tables that use only horizontal rules (no vertical lines).

    Collects h-rules from get_drawings(), groups them by x-extent into candidate
    tables, then uses x-position gaps in the span distribution to determine column
    boundaries.  Handles caption rows (e.g. "Table 1-1 | A Good Week…") by
    excluding them from the markdown so the table's first row is the column header,
    enabling cross-page stitching when the header repeats on the next page.

    Returns dicts with {"markdown": str, "bbox": tuple} in the same format as
    _try_pdfplumber_tables.

    rejected_out: optional list to accumulate rejected-candidate dicts.
    """
    page_width = page.rect.width
    min_rule_w = page_width * 0.20

    h_rules: list[tuple[float, float, float]] = []  # (y_mid, x0, x1)
    for path in page.get_drawings():
        for item in path.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                if dx >= min_rule_w and dy < 3:
                    h_rules.append(
                        ((p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x))
                    )
            elif item[0] == "re":
                r = item[1]
                if r.width >= min_rule_w and r.height <= 3:
                    h_rules.append((r.y0 + r.height / 2, r.x0, r.x1))

    if len(h_rules) < 2:
        return []

    # Group rules with similar x-extent into candidate tables
    x_tol = 20.0
    groups: list[list[tuple[float, float, float]]] = []
    for rule in sorted(h_rules, key=lambda r: r[0]):
        placed = False
        for grp in groups:
            rep = grp[0]
            if abs(rule[1] - rep[1]) <= x_tol and abs(rule[2] - rep[2]) <= x_tol:
                grp.append(rule)
                placed = True
                break
        if not placed:
            groups.append([rule])

    results = []
    for grp in groups:
        if len(grp) < 2:
            continue
        grp.sort(key=lambda r: r[0])
        ys = [r[0] for r in grp]
        tbl_x0 = min(r[1] for r in grp) - 5.0
        tbl_x1 = max(r[2] for r in grp) + 5.0

        # Small vertical margins: avoid capturing prose lines above the top rule
        hdr_above = 5.0
        last_below = 5.0
        row_ys = [ys[0] - hdr_above] + ys + [ys[-1] + last_below]
        n_rows = len(row_ys) - 1

        # Collect spans within the table x/y extent
        tbl_spans = [
            s for s in spans
            if tbl_x0 <= (s["bbox"][0] + s["bbox"][2]) / 2 <= tbl_x1
            and row_ys[0] <= (s["bbox"][1] + s["bbox"][3]) / 2 <= row_ys[-1]
        ]
        if len(tbl_spans) < 4:
            continue

        # Column detection: use left-edges of spans below the first rule
        body_for_cols = [
            s for s in tbl_spans
            if (s["bbox"][1] + s["bbox"][3]) / 2 > ys[0]
        ] or tbl_spans

        left_xs = sorted(s["bbox"][0] for s in body_for_cols)
        # Adaptive threshold: 4% of table width, floored at 8 pt.
        # Narrow financial grids (300 pt → 12 pt threshold) were silently
        # under-segmented by the previous hardcoded 20 pt value.
        tbl_width = tbl_x1 - tbl_x0
        min_col_gap = max(8.0, tbl_width * 0.04)
        col_boundaries: list[float] = []
        for i in range(1, len(left_xs)):
            gap = left_xs[i] - left_xs[i - 1]
            if gap >= min_col_gap:
                mid = (left_xs[i - 1] + left_xs[i]) / 2
                if not col_boundaries or mid - col_boundaries[-1] >= 10.0:
                    col_boundaries.append(mid)

        n_cols = len(col_boundaries) + 1
        if n_cols < 2 or n_cols > 12:
            continue

        def col_of(x: float) -> int:
            c = 0
            for b in col_boundaries:
                if x > b:
                    c += 1
            return c

        def row_of(cy: float) -> int:
            for i in range(n_rows):
                if cy <= row_ys[i + 1]:
                    return i
            return n_rows - 1

        bucket: list[list[list[dict]]] = [
            [[] for _ in range(n_cols)] for _ in range(n_rows)
        ]
        for s in tbl_spans:
            cy = (s["bbox"][1] + s["bbox"][3]) / 2
            ri = row_of(cy)
            ci = col_of(s["bbox"][0])
            if 0 <= ri < n_rows and 0 <= ci < n_cols:
                bucket[ri][ci].append(s)

        # Build cell grid; track each non-empty row's top y for bbox calculation
        row_data: list[tuple[list[str], float]] = []
        hrule_cell_bboxes: list[list] = []  # parallel to row_data
        for i, row_bucket in enumerate(bucket):
            grid_row = []
            row_bboxes_for_row = []
            for cell_spans in row_bucket:
                # Sort by y-bucket (5 pt rounding) then x so that adjacent glyphs
                # on the same baseline (e.g. "2" "×" "3 = 6") stay in x-order even
                # when their y-coordinates differ by a sub-pixel amount.
                cell_spans.sort(key=lambda s: (round(s["y"] / 5), s["x"]))
                grid_row.append(" ".join(s["text"] for s in cell_spans).strip())
                row_bboxes_for_row.append(cell_bbox_from_spans(cell_spans))
            if any(c for c in grid_row):
                row_data.append((grid_row, row_ys[i]))
                hrule_cell_bboxes.append(row_bboxes_for_row)

        if not row_data:
            continue

        cell_grid = [r for r, _ in row_data]
        row_tops = [t for _, t in row_data]

        # Skip an opening caption row (e.g. "Table 1-1 | A Good Week for Your Team")
        # and set bbox_y_top to that rule so the caption spans remain in the prose
        # stream rather than being suppressed by _filter_table_spans.
        start_row = 1 if len(cell_grid) > 1 and _is_caption_row(cell_grid[0]) else 0
        md_cells = cell_grid[start_row:]
        if not md_cells:
            continue

        bbox_y_top = row_tops[start_row] if start_row < len(row_tops) else ys[0]
        bbox_y_bottom = ys[-1] + last_below
        tbl_bbox = (tbl_x0, bbox_y_top, tbl_x1, bbox_y_bottom)

        # Cell bboxes computed in parallel with row_data above; slice off caption row
        md_cell_bboxes = hrule_cell_bboxes[start_row:]

        td = cells_to_tabledata(
            md_cells,
            bbox=tbl_bbox,
            source="hrule",
            page=page_num,
            cell_bboxes=md_cell_bboxes,
        )
        if td.row_count == 0:
            continue
        md = _render_table_markdown(td)
        rejection_log: list[str] = []
        if not md or not _is_quality_table(md, rejection_log):
            if rejected_out is not None:
                dot_rows = 0
                total_cells = 0
                empty_cells = 0
                if md:
                    md_lines = [ln for ln in md.strip().splitlines() if ln.strip()]
                    md_data = [ln for ln in md_lines[2:] if "|" in ln and not ln.startswith("|---")]
                    dot_rows = sum(1 for r in md_data if _TOC_DOT_RE.search(r))
                    for row in md_data:
                        inner = row.split("|")[1:-1]
                        total_cells += len(inner)
                        empty_cells += sum(1 for c in inner if not c.strip())
                rejected_out.append({
                    "strategy": "hrule",
                    "page": page_num,
                    "bbox": list(tbl_bbox),
                    "row_count": td.row_count,
                    "col_count": td.column_count,
                    "rejection_reasons": rejection_log,
                    "quality_metrics": {
                        "dot_leader_fraction": dot_rows / max(len([ln for ln in (md or "").strip().splitlines() if "|" in ln and not ln.startswith("|---")]), 1),
                        "empty_cell_fraction": empty_cells / max(total_cells, 1),
                        "col_count": td.column_count,
                    },
                })
            continue

        results.append({"table_data": td, "bbox": tbl_bbox})

    return results


_OCR_TEXT_THRESHOLD = 50    # chars below which a full-page rasterisation is done
_EMBEDDED_OCR_THRESHOLD = 300  # chars below which embedded images are individually OCR'd
# 300 DPI is the standard recommendation for Tesseract on body text.
# Override with AKSHARAMD_OCR_DPI env var for speed/quality trade-off.
_OCR_DPI = int(os.getenv("AKSHARAMD_OCR_DPI", "300"))
_EMBED_MIN_PX = 100         # ignore embedded images smaller than 100×100 px (decorative)
_MAX_CONTENT_IMAGE_BYTES = 2 * 1024 * 1024  # skip images > 2 MB (very high-res raw scans)
_MAX_IMAGES_PER_PAGE = 3
_MAX_TOTAL_IMAGES = 20
# pdfplumber fallback: skip pages denser than this (unlikely to have a missed table)
_PDFPLUMBER_CHAR_LIMIT = 3000
# Parallel Phase-1 I/O: use concurrent readers above this page threshold.
_PARALLEL_IO_THRESHOLD = 20
_PARALLEL_IO_WORKERS = 4
# Hard cap on pages processed per document.  Very large PDFs (textbooks, law
# compilations) can consume unbounded memory during parallel extraction.
# Override with AKSHARAMD_MAX_PDF_PAGES.
_MAX_PDF_PAGES = int(os.environ.get("AKSHARAMD_MAX_PDF_PAGES", "2000"))


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
    math_bboxes: list[tuple[float, float, float, float]] = []  # bboxes of undecodable font spans (math candidates)
    rejected_candidates: list[dict] = []  # table candidates found but rejected by _is_quality_table


def _chunk_pages(page_count: int, workers: int = _PARALLEL_IO_WORKERS) -> list[list[int]]:
    """Split 1-indexed page numbers into `workers` evenly-sized chunks."""
    actual = min(workers, page_count)
    size = max(1, (page_count + actual - 1) // actual)
    return [
        list(range(i + 1, min(i + size + 1, page_count + 1)))
        for i in range(0, page_count, size)
    ]


def _extract_page_chunk(path_str: str, page_nums: list[int]) -> list[RawPage]:
    """Open the PDF in the calling thread and extract the assigned page numbers.

    Each thread gets its own fitz.Document and pdfplumber handle — PyMuPDF
    supports concurrent access when each thread uses a distinct Document object.
    """
    pdf = fitz.open(path_str)
    pdf_pl = None
    try:
        import pdfplumber
        pdf_pl = pdfplumber.open(path_str)
    except Exception:
        pass
    try:
        return [_extract_raw_page(pdf, pn, pdf_pl) for pn in page_nums]
    finally:
        pdf.close()
        if pdf_pl is not None:
            pdf_pl.close()


def _extract_raw_page(pdf: fitz.Document, page_num: int, pdf_pl=None) -> RawPage:
    """Extract one page from an already-open fitz.Document.

    Callers must ensure each thread/process uses its own fitz.Document —
    sharing a single Document across threads is not safe.
    pdf_pl: optional open pdfplumber.PDF for borderless-table fallback.
    """
    page = pdf[page_num - 1]

    spans = []
    math_bboxes: list[tuple[float, float, float, float]] = []
    for block in page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        if block["type"] != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                chars_list = span.get("chars", [])
                # Exclude chars with PDF text rendering mode 3 (Tr=3: no fill, no stroke —
                # the text is invisible on screen but present in the byte stream). Lower 4 bits
                # of char["flags"] encode the rendering mode in PyMuPDF rawdict output.
                visible_chars = [
                    ch["c"] for ch in chars_list
                    if (ch.get("flags", 0) & 0xF) != 3
                ]
                _joined = "".join(visible_chars)
                text = _CID_RE.sub("", _joined).replace("�", "").strip()
                if text:
                    flags = span.get("flags", 0)
                    font_name = span.get("font", "")
                    spans.append({
                        "text": text,
                        "size": span["size"],
                        "bold": _is_bold(flags, font_name),
                        "italic": _is_italic(flags, font_name),
                        "sup": _is_superscript(flags, font_name),
                        "sub": _is_subscript(font_name),
                        "mono": _is_monospace(flags, font_name),
                        "y": span["origin"][1],
                        "x": span["origin"][0],
                        "bbox": span["bbox"],
                    })
                elif chars_list and not any((ch.get("flags", 0) & 0xF) == 3 for ch in chars_list):
                    # Span has visible chars but text is empty after stripping. Two cases:
                    # (a) chars decoded to U+FFFD — unmapped custom-font glyph (e.g. WileyCode
                    #     bullets). Discard silently; these are not math candidates.
                    # (b) chars decoded to "" — truly undecodable encoding (CM math fonts).
                    #     Record for potential math OCR in Phase 6.
                    if _joined and not _joined.replace("�", ""):
                        pass  # case (a): all U+FFFD — discard
                    else:
                        math_bboxes.append(tuple(span["bbox"]))  # case (b): math candidate

    # Strip LaTeX \lineno margin line-numbers before further processing so they
    # don't pollute table cells or paragraph text.
    spans = _filter_latex_line_numbers(spans, page.rect.width)

    # Tag spans with underline/strikethrough from drawing paths (additive, never
    # raises — detection failure silently leaves flags absent, treated as False).
    _tag_text_decorations(page, spans)

    tables = []
    rejected: list[dict] = []  # accumulate rejected candidates across all strategies
    if _has_ruled_table(page):
        try:
            for tab in page.find_tables():
                cells = tab.extract()
                td = cells_to_tabledata(
                    cells,
                    bbox=tuple(tab.bbox),
                    source="ruled",
                    page=page_num,
                )
                if td.row_count == 0:
                    continue
                md = _render_table_markdown(td)
                rejection_log: list[str] = []
                if md and _is_quality_table(md, rejection_log):
                    tables.append({"table_data": td, "bbox": tuple(tab.bbox), "source": "ruled"})
                elif md is not None or rejection_log:
                    # Candidate was found but rejected — record it
                    dot_rows = 0
                    total_cells = 0
                    empty_cells = 0
                    if md:
                        md_lines = [ln for ln in md.strip().splitlines() if ln.strip()]
                        md_data = [ln for ln in md_lines[2:] if "|" in ln and not ln.startswith("|---")]
                        dot_rows = sum(1 for r in md_data if _TOC_DOT_RE.search(r))
                        for row in md_data:
                            inner = row.split("|")[1:-1]
                            total_cells += len(inner)
                            empty_cells += sum(1 for c in inner if not c.strip())
                    rejected.append({
                        "strategy": "ruled",
                        "page": page_num,
                        "bbox": list(tuple(tab.bbox)),
                        "row_count": td.row_count,
                        "col_count": td.column_count,
                        "rejection_reasons": rejection_log,
                        "quality_metrics": {
                            "dot_leader_fraction": dot_rows / max(len([ln for ln in (md or "").strip().splitlines() if "|" in ln and not ln.startswith("|---")]), 1),
                            "empty_cell_fraction": empty_cells / max(total_cells, 1),
                            "col_count": td.column_count,
                        },
                    })
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
        for tbl in _try_pdfplumber_tables(pdf_pl, page_num, total_chars, page.rect.height, rejected_out=rejected):
            tables.append({**tbl, "source": "whitespace"})

    # Horizontal-rule fallback: detect booktabs-style tables with only h-rules and
    # no vertical lines (strategy='lines' finds nothing; pdfplumber text-strategy
    # also fails because multi-level cell text confuses its column detector).
    if not tables:
        for tbl in _try_hrule_table(page, spans, page_num, rejected_out=rejected):
            tables.append({**tbl, "source": "hrule"})

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
        math_bboxes=math_bboxes,
        rejected_candidates=rejected,
    )


def _median_font_size(all_pages: list[RawPage]) -> float:
    sizes = [s["size"] for p in all_pages for s in p.spans]
    return statistics.median(sizes) if sizes else 12.0


def _classify_pdf(raw_pages: list[RawPage]) -> tuple[str, dict]:
    """Classify PDF at document level based on per-page content characteristics.

    Returns (classification_label, stats_dict) where label is one of:
      native_text  — >80% pages have a substantial text layer
      scanned      — >60% pages are image-only (no text layer)
      hybrid       — 20–60% pages are image-only
      table_heavy  — >30% pages have extracted tables, majority are text pages
      layout_heavy — majority text, >30% pages appear to be multi-column
      low_confidence — doesn't fit a clear category
    """
    page_count = len(raw_pages)
    if page_count == 0:
        return "low_confidence", {}

    image_pages = 0
    text_pages = 0
    table_pages = 0
    multi_col_pages = 0

    for raw in raw_pages:
        page_chars = sum(len(s.get("text", "").strip()) for s in raw.spans)
        if page_chars < _OCR_TEXT_THRESHOLD:
            image_pages += 1
        else:
            text_pages += 1

        if raw.tables:
            table_pages += 1

        # Rough multi-column heuristic: span x-positions span >40% of page width
        # and have both left and right clusters (not just a wide single column).
        if raw.spans and page_chars >= _OCR_TEXT_THRESHOLD and raw.width > 0:
            xs = [s["x"] for s in raw.spans]
            if len(xs) >= 12:
                x_min, x_max = min(xs), max(xs)
                if (x_max - x_min) > raw.width * 0.40:
                    mid = (x_min + x_max) / 2
                    left = sum(1 for x in xs if x < mid)
                    right = sum(1 for x in xs if x >= mid)
                    if left >= 4 and right >= 4:
                        multi_col_pages += 1

    image_ratio = image_pages / page_count
    table_ratio = table_pages / page_count
    multi_col_ratio = multi_col_pages / page_count

    if image_ratio >= 0.80:
        label = "scanned"
    elif image_ratio >= 0.20:
        label = "hybrid"
    elif table_ratio >= 0.30:
        label = "table_heavy"
    elif multi_col_ratio >= 0.30:
        label = "layout_heavy"
    elif (text_pages / page_count) >= 0.80:
        label = "native_text"
    else:
        label = "low_confidence"

    stats: dict = {
        "page_count": page_count,
        "text_pages": text_pages,
        "image_pages": image_pages,
        "table_pages": table_pages,
        "multi_col_pages": multi_col_pages,
        "image_ratio": round(image_ratio, 2),
        "table_ratio": round(table_ratio, 2),
        "multi_col_ratio": round(multi_col_ratio, 2),
    }
    return label, stats


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
            # Bare digits (^\d+$) and X/N fractions (^\d+/\d+$) are only treated
            # as page numbers when they appear in the header or footer zone.
            # "1" mid-page is a quantity or footnote; "1/2" mid-page is a ratio.
            # Other _PAGE_NUM_RE patterns (ranges, "Page N of M", timestamps)
            # are structural noise regardless of position and are always removed.
            if _PAGE_NUM_RE.match(text):
                is_zone_restricted = bool(re.match(r"^\d+$|^\d+/\d+$", text))
                in_zone = rel_y < _HEADER_ZONE or rel_y > _FOOTER_ZONE
                if not is_zone_restricted or in_zone:
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
            # Require the span to appear in a header or footer zone on at least one page.
            # Mid-page body text can repeat across pages in short documents (filler text,
            # repeated prose sections) without being boilerplate — stripping it causes
            # near-complete content loss on those pages.
            if header_counter.get(text, 0) > 0 or footer_counter.get(text, 0) > 0:
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
    # A single character is never a heading — drop capitals, decorative initials, page markers.
    if len(text.strip()) == 1:
        return None
    # Very long spans are body prose even when large/bold — no real heading exceeds 15 words.
    if len(text.split()) > 15:
        return None
    ratio = size / median if median else 1.0
    # isupper() returns True if ALL *cased* characters are uppercase, so
    # "A + 2"" and "B - 2.5"" (dimension annotations) also pass.  Require that
    # at least half the characters are alphabetic to exclude these.  Also
    # exclude geographic abbreviations like "CA, USA." (mixed punctuation).
    _alpha = [c for c in text if c.isalpha()]
    is_caps = (
        bool(_alpha)
        and all(c.isupper() for c in _alpha)
        and len(_alpha) / max(len(text), 1) >= 0.5
        and len(text) > 3
        and not ("," in text and "." in text)
    )

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
        # Real TOC exists → only trust strongly dominant font sizes; suppress noisy small headings.
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
        # Single all-caps abbreviations ("ASTM", "ASHRAE") at ratio ≈ 1.15 are
        # institution names or acronyms in reference lists, not headings.
        if not (len(text.split()) == 1 and text.isupper()):
            return 4
    if ratio >= 1.10 and bold and not _prose:
        return 5
    if is_caps and centered and not _prose and ratio >= 0.95 and len(text) < 80:
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
        # Dimension annotations from technical drawings end with " (inch mark).
        and not text.endswith('"')
        and len(text) >= 3
        and not _BOLD_HDR_CAPTION_RE.match(text)
        and 1 <= len(_words) <= 3
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
) -> tuple[int, list[Block], list[Asset], dict]:
    """
    Pure Python processing of one page's extracted data.
    No PyMuPDF calls — safe to run in a thread pool.
    """
    blocks: list[Block] = []

    # Build TABLE blocks but defer insertion until the correct y-position in the
    # prose flow.  A table at y=450 should appear after paragraphs at y=83–405,
    # not before them — "tables come first" caused incorrect ordering when prose
    # precedes the table on the same page.
    table_bboxes = []
    pending_tables: list[tuple[float, Block]] = []  # (y_top, block)
    for t in raw.tables:
        tbl_confidence = (
            ExtractionConfidence.EXTRACTED if t.get("source") != "whitespace"
            else ExtractionConfidence.INFERRED
        )
        if "table_data" in t:
            block = Block.from_table(
                t["table_data"],
                page=raw.page_num,
                index=0,
                confidence=tbl_confidence,
                metadata={"table_bbox": t["bbox"]},
            )
        else:
            # Legacy fallback (Marker path creates blocks without table_data;
            # kept for safety in case other paths produce legacy-format dicts)
            block = Block(
                type=BlockType.TABLE,
                content=t.get("markdown", ""),
                page=raw.page_num,
                index=0,
                confidence=tbl_confidence,
                metadata={"table_bbox": t["bbox"]},
            )
        pending_tables.append((t["bbox"][1], block))
        table_bboxes.append(t["bbox"])

    # Remove spans that overlap with already-extracted tables
    spans = _filter_table_spans(raw.spans, table_bboxes)

    # Detect multi-column layout and sort spans into reading order
    boundaries = _detect_column_boundaries(spans, raw.width)
    spans = sorted(spans, key=lambda s: (_column_of(s["x"], raw.width, boundaries), s["y"]))

    current_spans: list[dict] = []

    def flush() -> None:
        if not current_spans:
            return
        # Merge consecutive spans that share the same inline formatting, then
        # apply bold/italic/strikethrough/underline markers to each run.
        result_parts: list[str] = []
        run_texts: list[str] = []
        run_bold   = current_spans[0].get("bold", False)
        run_italic = current_spans[0].get("italic", False)
        run_strike = current_spans[0].get("strikethrough", False)
        run_under  = current_spans[0].get("underline", False)
        run_sup    = current_spans[0].get("sup", False)
        run_sub    = current_spans[0].get("sub", False)

        def _flush_run() -> None:
            if run_texts:
                result_parts.append(
                    _apply_inline_fmt(
                        " ".join(run_texts),
                        run_bold, run_italic, run_strike, run_under,
                        run_sup, run_sub,
                    )
                )

        for sp in current_spans:
            b   = sp.get("bold", False)
            i   = sp.get("italic", False)
            s   = sp.get("strikethrough", False)
            u   = sp.get("underline", False)
            sup = sp.get("sup", False)
            sub = sp.get("sub", False)
            if (b, i, s, u, sup, sub) == (run_bold, run_italic, run_strike, run_under, run_sup, run_sub):
                run_texts.append(sp["text"])
            else:
                _flush_run()
                run_texts[:] = [sp["text"]]
                run_bold, run_italic, run_strike, run_under, run_sup, run_sub = b, i, s, u, sup, sub
        _flush_run()

        text = " ".join(result_parts).strip()
        if text:
            all_mono = all(sp.get("mono", False) for sp in current_spans)
            first = current_spans[0]
            blocks.append(Block(
                type=BlockType.CODE_BLOCK if all_mono else BlockType.PARAGRAPH,
                content=text,
                page=raw.page_num,
                index=0,
                metadata={"x0": first["x"], "y0": first["y"]},
            ))
        current_spans.clear()

    prev_text_span: dict | None = None  # last span appended to current_spans

    for span in spans:
        # Insert tables whose top y falls before this span's y (preserves reading order
        # on pages where prose precedes the table, e.g. a table at the bottom of a page).
        if pending_tables:
            ready = [(ty, tb) for ty, tb in pending_tables if ty <= span["y"]]
            if ready:
                pending_tables = [(ty, tb) for ty, tb in pending_tables if ty > span["y"]]
                flush()
                for _, tb in sorted(ready, key=lambda x: x[0]):
                    blocks.append(tb)

        text = span["text"]
        if text in removable:
            continue

        # Paragraph-break detection: a vertical gap larger than 1.8 × the previous
        # span's font size within the same column signals a new paragraph.  This
        # separates distinct paragraphs that share no heading or caption to act as
        # a natural separator — common in 2-column academic papers.
        if prev_text_span is not None and current_spans:
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
                metadata={"x0": span["x"], "y0": span["y"]},
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
                metadata={"x0": span["x"], "y0": span["y"]},
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
                metadata={"x0": span["x"], "y0": span["y"]},
            ))
            prev_text_span = None
        else:
            current_spans.append(span)
            prev_text_span = span

    flush()

    # Append any tables whose y-top exceeds all prose on this page (table at bottom).
    for _, tb in sorted(pending_tables, key=lambda x: x[0]):
        blocks.append(tb)

    if raw.ocr_pixmap is not None:
        if _ocr_available():
            _apply_page_ocr(raw.ocr_pixmap, raw.page_num, blocks)
        else:
            blocks.append(Block(
                type=BlockType.PARAGRAPH, content=_OCR_UNAVAILABLE_MSG,
                page=raw.page_num, index=0,
                confidence=ExtractionConfidence.AMBIGUOUS,
            ))

    if raw.embedded_image_bytes:
        if _ocr_available():
            for img_bytes in raw.embedded_image_bytes:
                _apply_page_ocr(img_bytes, raw.page_num, blocks)
        else:
            blocks.append(Block(
                type=BlockType.PARAGRAPH, content=_OCR_UNAVAILABLE_MSG,
                page=raw.page_num, index=0,
                confidence=ExtractionConfidence.AMBIGUOUS,
            ))

    # Add IMAGE blocks for content images (after text, for multimodal output).
    # Content uses standard markdown image syntax so the reference survives in .md output.
    for asset_id, _img_bytes in raw.content_images:
        blocks.append(Block(
            type=BlockType.IMAGE,
            content=f"![Image on page {raw.page_num}](asset://{asset_id})",
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

    col_info: dict = {
        "boundaries": boundaries,
        "num_columns": len(boundaries) + 1,
        "page_width": raw.width,
        "page_height": raw.height,
    }
    return raw.page_num, blocks, assets, col_info


_MARKER_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)")


def _parse_marker_markdown(
    markdown: str,
    page_num: int,
    images: dict | None = None,
) -> tuple[list[Block], list[Asset], bool]:
    """Convert a Marker markdown string (single page) into Block objects and Assets.

    images: Marker's rendered.images dict mapping key -> PIL Image (optional).
    Image blobs are stored as Assets; blocks carry an asset:// reference so the
    position is preserved in the markdown output without any AI extraction.

    Returns (blocks, assets, had_hallucination).  had_hallucination is True when
    at least one paragraph block was suppressed due to n-gram repetition.
    """
    blocks: list[Block] = []
    new_assets: list[Asset] = []
    idx = 0
    table_lines: list[str] = []
    in_table = False
    para_lines: list[str] = []
    had_hallucination = False

    def flush_para() -> None:
        nonlocal idx, had_hallucination
        text = " ".join(para_lines).strip()
        if text:
            if _is_repetitive_text(text):
                had_hallucination = True
            else:
                blocks.append(Block(
                    type=BlockType.PARAGRAPH,
                    content=text,
                    page=page_num,
                    index=idx,
                    confidence=ExtractionConfidence.EXTRACTED,
                ))
                idx += 1
        para_lines.clear()

    def flush_table() -> None:
        nonlocal idx
        if table_lines:
            md = "\n".join(table_lines)
            if _is_quality_table(md):
                blocks.append(Block(
                    type=BlockType.TABLE,
                    content=md,
                    page=page_num,
                    index=idx,
                    confidence=ExtractionConfidence.EXTRACTED,
                ))
                idx += 1
            table_lines.clear()

    for line in markdown.splitlines():
        stripped = line.strip()

        if stripped.startswith("|"):
            if not in_table:
                flush_para()
                in_table = True
            table_lines.append(line)
            continue

        if in_table:
            flush_table()
            in_table = False

        img_m = _MARKER_IMAGE_RE.match(stripped)
        if img_m:
            flush_para()
            alt_text = img_m.group(1).strip() or f"Figure on page {page_num}"
            img_key = img_m.group(2)
            asset_id = hashlib.sha256(
                f"marker:{page_num}:{img_key}".encode()
            ).hexdigest()[:12]

            img_bytes: bytes | None = None
            if images:
                pil_img = images.get(img_key)
                if pil_img is not None:
                    try:
                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG")
                        img_bytes = buf.getvalue()
                    except Exception:
                        pass

            new_assets.append(Asset(
                id=asset_id,
                type="image",
                page=page_num,
                alt_text=alt_text,
                image_bytes=img_bytes,
            ))
            blocks.append(Block(
                type=BlockType.IMAGE,
                content=f"![{alt_text}](asset://{asset_id})",
                page=page_num,
                index=idx,
                metadata={"asset_id": asset_id},
                confidence=ExtractionConfidence.EXTRACTED,
            ))
            idx += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            flush_para()
            blocks.append(Block(
                type=BlockType.HEADING,
                content=m.group(2).strip(),
                level=len(m.group(1)),
                page=page_num,
                index=idx,
                confidence=ExtractionConfidence.EXTRACTED,
            ))
            idx += 1
            continue

        if not stripped:
            flush_para()
        else:
            para_lines.append(stripped)

    flush_para()
    flush_table()
    return blocks, new_assets, had_hallucination


def _apply_marker_to_image_pages(
    path: Path,
    raw_pages: list[RawPage],
    all_blocks: list[Block],
) -> tuple[list[Block], list[Asset], int, bool]:
    """Re-extract image-only pages using Marker for layout-aware reconstruction.

    Returns (updated_blocks, new_assets, vision_page_count, had_hallucination).
    Each image page is processed as a single-page sub-PDF so blocks are assigned
    the correct page number. Figure/chart images are stored as Assets and
    referenced inline in the markdown as ![alt](asset://id) — no AI extraction,
    just blob + position.  had_hallucination is True when any page's OCR output
    was detected as repetitive garbage (e.g. Marker looping on an unknown script).
    """
    image_page_nums = [
        raw.page_num for raw in raw_pages
        if sum(len(s.get("text", "")) for s in raw.spans) < _OCR_TEXT_THRESHOLD
    ]
    if not image_page_nums:
        return all_blocks, [], 0, False

    models = _get_marker_models()
    if models is None:
        return all_blocks, [], 0, False

    try:
        from marker.converters.pdf import PdfConverter
    except ImportError:
        return all_blocks, [], 0, False

    marker_blocks: list[Block] = []
    marker_assets: list[Asset] = []
    vision_pages = 0
    ocr_hallucination = False
    pages_replaced: set[int] = set()  # only pages where Marker produced content
    original_pdf = fitz.open(str(path))

    for orig_pnum in image_page_nums:
        sub = fitz.open()
        sub.insert_pdf(original_pdf, from_page=orig_pnum - 1, to_page=orig_pnum - 1)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        try:
            os.close(tmp_fd)
            sub.save(tmp_path)
            sub.close()
            converter = PdfConverter(artifact_dict=models)
            rendered = converter(tmp_path)
            marker_images = getattr(rendered, "images", None) or {}
            page_blocks, page_assets, page_hallucination = _parse_marker_markdown(
                rendered.markdown, orig_pnum, marker_images
            )
            if page_hallucination:
                ocr_hallucination = True
                logger.debug("OCR hallucination detected on page %d of %s", orig_pnum, path.name)
            if page_blocks:
                marker_blocks.extend(page_blocks)
                marker_assets.extend(page_assets)
                vision_pages += 1
                pages_replaced.add(orig_pnum)
        except Exception:
            logger.debug("Marker failed on page %d of %s", orig_pnum, path.name, exc_info=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    original_pdf.close()

    # Only remove existing blocks (Tesseract OCR / IMAGE placeholders) for pages where
    # Marker actually produced content. Pages where Marker failed or returned nothing
    # keep their prior blocks, preventing spurious MISSING_PAGE validator warnings.
    filtered: list[Block] = [b for b in all_blocks if b.page not in pages_replaced]
    combined = filtered + marker_blocks
    combined.sort(key=lambda b: (b.page or 0, b.index))
    for i, block in enumerate(combined):
        combined[i] = block.model_copy(update={"index": i})

    return combined, marker_assets, vision_pages, ocr_hallucination


# ── Math OCR (Phase 6) ────────────────────────────────────────────────────────

_MATH_AVAILABLE: bool | None = None
_MATH_MODEL = None

_MATH_EMPTY_SPAN_THRESHOLD = 5    # minimum undecodable spans on a page to attempt OCR
_MATH_CLUSTER_LINE_GAP = 6.0      # bboxes within this many pts vertically → same line
_MATH_CLUSTER_HORIZ_GAP = 30.0    # bboxes within this many pts horizontally → same cluster
_MATH_MIN_CLUSTER_WIDTH = 8.0     # ignore clusters narrower than this (single dots, dashes)
_MATH_RASTER_SCALE = 3.0          # render scale for equation crops (≈216 DPI at 72dpi base)
_MATH_PADDING = 6.0               # padding added around each cluster before rasterising
_MATH_MAX_EQUATIONS = 300         # hard cap per document to bound processing time


def _math_available() -> bool:
    global _MATH_AVAILABLE
    if _MATH_AVAILABLE is None:
        _MATH_AVAILABLE = importlib.util.find_spec("pix2tex") is not None
    return _MATH_AVAILABLE


def _get_math_model():
    global _MATH_MODEL
    if _MATH_MODEL is not None:
        return _MATH_MODEL
    if not _math_available():
        return None
    try:
        from pix2tex.cli import LatexOCR
        logger.debug("Loading pix2tex math OCR model (first-time load, may take a moment)...")
        _MATH_MODEL = LatexOCR()
        return _MATH_MODEL
    except Exception as exc:
        logger.warning("pix2tex model failed to load: %s", exc)
        return None


def _cluster_math_bboxes(
    bboxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Merge individual char bboxes into expression-level regions.

    Strategy: sort by (y_center, x0), then greedily merge bboxes that are on
    the same line (y_center within _MATH_CLUSTER_LINE_GAP) and close
    horizontally (gap between consecutive bboxes < _MATH_CLUSTER_HORIZ_GAP).
    Returns merged union bboxes, one per expression cluster.
    """
    if not bboxes:
        return []

    sorted_bboxes = sorted(bboxes, key=lambda b: ((b[1] + b[3]) / 2, b[0]))

    clusters: list[list[tuple[float, float, float, float]]] = []
    current: list[tuple[float, float, float, float]] = [sorted_bboxes[0]]

    for bbox in sorted_bboxes[1:]:
        prev = current[-1]
        prev_y = (prev[1] + prev[3]) / 2
        cur_y = (bbox[1] + bbox[3]) / 2
        same_line = abs(cur_y - prev_y) <= _MATH_CLUSTER_LINE_GAP
        close_horiz = bbox[0] - prev[2] <= _MATH_CLUSTER_HORIZ_GAP

        if same_line and close_horiz:
            current.append(bbox)
        else:
            clusters.append(current)
            current = [bbox]
    clusters.append(current)

    merged = []
    for cluster in clusters:
        x0 = min(b[0] for b in cluster)
        y0 = min(b[1] for b in cluster)
        x1 = max(b[2] for b in cluster)
        y1 = max(b[3] for b in cluster)
        if (x1 - x0) >= _MATH_MIN_CLUSTER_WIDTH:
            merged.append((x0, y0, x1, y1))
    return merged


def _apply_math_ocr_to_blocks(
    path: Path,
    raw_pages: list[RawPage],
    all_blocks: list[Block],
) -> tuple[list[Block], int]:
    """Phase 6: rasterise undecodable font regions and recover math via pix2tex.

    Only runs on pages with >= _MATH_EMPTY_SPAN_THRESHOLD undecodable spans.
    Returns updated block list (with MATH blocks inserted) and equation count.
    If pix2tex is unavailable or no math is found, returns (all_blocks, 0) unchanged.
    """
    model = _get_math_model()
    if model is None:
        return all_blocks, 0

    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        logger.debug("numpy/Pillow not available; math OCR skipped")
        return all_blocks, 0

    math_pages = [
        raw for raw in raw_pages
        if len(raw.math_bboxes) >= _MATH_EMPTY_SPAN_THRESHOLD
    ]
    if not math_pages:
        return all_blocks, 0

    pdf = fitz.open(str(path))
    new_math_blocks: list[Block] = []
    total_equations = 0

    try:
        for raw in math_pages:
            if total_equations >= _MATH_MAX_EQUATIONS:
                break

            clusters = _cluster_math_bboxes(raw.math_bboxes)
            if not clusters:
                continue

            page = pdf[raw.page_num - 1]
            page_rect = page.rect

            for x0, y0, x1, y1 in clusters:
                if total_equations >= _MATH_MAX_EQUATIONS:
                    break
                # Add padding and clamp to page boundaries
                rx0 = max(0.0, x0 - _MATH_PADDING)
                ry0 = max(0.0, y0 - _MATH_PADDING)
                rx1 = min(page_rect.width, x1 + _MATH_PADDING)
                ry1 = min(page_rect.height, y1 + _MATH_PADDING)

                try:
                    clip = fitz.Rect(rx0, ry0, rx1, ry1)
                    mat = fitz.Matrix(_MATH_RASTER_SCALE, _MATH_RASTER_SCALE)
                    pix = page.get_pixmap(matrix=mat, clip=clip, colorspace=fitz.csRGB)
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                        pix.height, pix.width, 3
                    )
                    img = Image.fromarray(arr)
                except Exception:
                    logger.debug(
                        "Math rasterisation failed on p%d bbox %s",
                        raw.page_num, (x0, y0, x1, y1), exc_info=True,
                    )
                    continue

                try:
                    latex = model(img)
                except Exception:
                    logger.debug(
                        "pix2tex failed on p%d bbox %s",
                        raw.page_num, (x0, y0, x1, y1), exc_info=True,
                    )
                    continue

                if not latex or not latex.strip():
                    continue

                # Wrap in display-math delimiters; centre y of cluster determines position
                content = f"$${latex.strip()}$$"
                new_math_blocks.append(Block(
                    type=BlockType.MATH,
                    content=content,
                    page=raw.page_num,
                    confidence=ExtractionConfidence.AMBIGUOUS,
                    metadata={"math_bbox": (x0, y0, x1, y1), "y_center": (y0 + y1) / 2},
                ))
                total_equations += 1
    finally:
        pdf.close()

    if not new_math_blocks:
        return all_blocks, 0

    # Merge math blocks with existing blocks, preserving page + y-position order.
    # Build a sort key: (page, y_center) — existing blocks use their page and
    # the y coordinate stored in metadata (if available) or fall back to index.
    def _sort_key(b: Block) -> tuple[int, float]:
        page = b.page or 0
        y = b.metadata.get("y_center", float(b.index))
        return (page, y)

    # Annotate existing blocks with a y_center estimate from their content position.
    # We don't have stored y coords on existing blocks, so approximate by index order
    # within each page (preserves relative order, only used for merge tie-breaking).
    existing_on_page: dict[int, list[Block]] = {}
    for b in all_blocks:
        existing_on_page.setdefault(b.page or 0, []).append(b)

    # For pages that have math blocks, attach a y estimate to each existing block
    # on that page based on its position in the page's block list.
    math_page_nums = {b.page for b in new_math_blocks if b.page is not None}
    for pg in math_page_nums:
        page_blocks = existing_on_page.get(pg, [])
        for rank, blk in enumerate(page_blocks):
            if "y_center" not in blk.metadata:
                blk.metadata["y_center"] = float(rank) * 100.0

    combined = sorted(all_blocks + new_math_blocks, key=_sort_key)

    # Re-index
    for i, block in enumerate(combined):
        combined[i] = block.model_copy(update={"index": i})

    return combined, total_equations


def _pdfplumber_fallback(path: Path, ctx: CompilationContext) -> CompilationContext:
    """Fallback when PyMuPDF reports 0 pages due to corrupted xref/metadata.

    pdfminer (used by pdfplumber) is more tolerant of metadata corruption and can
    often read the content layer even when PyMuPDF's xref parser gives up.
    """
    try:
        import pdfplumber
    except ImportError:
        return ctx

    try:
        blocks: list[Block] = []
        page_count = 0
        logging.getLogger("pdfplumber").setLevel(logging.ERROR)
        with pdfplumber.open(str(path)) as pl:
            page_count = len(pl.pages)
            for i, page in enumerate(pl.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content=text.strip(),
                        page=i,
                        index=len(blocks),
                        confidence=ExtractionConfidence.AMBIGUOUS,
                    ))
    except Exception as exc:
        ctx.error("PARSE_FAILED", f"pdfplumber fallback also failed: {exc}")
        return ctx

    if not blocks:
        return ctx

    ctx.warn(
        "CORRUPTED_METADATA",
        "PDF has corrupted or unreadable metadata — PyMuPDF could not parse the page "
        "structure. Content was recovered via pdfplumber fallback. Confidence is AMBIGUOUS.",
    )

    doc = Document(
        source=str(path),
        file_type="pdf",
        title=path.stem,
        pages=page_count,
        blocks=blocks,
        metadata={"pdf_classification": "low_confidence", "pdf_ocr_available": False},
    )
    doc.compute_id()
    ctx.document = doc
    return ctx


class PDFParser(ParserPlugin):
    name = "pdf_parser"
    supported_types = ["pdf"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        # Suppress noisy low-level warnings that bypass Python's logging system
        try:
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception:
            pass
        logging.getLogger("pdfplumber.pdf").setLevel(logging.ERROR)

        try:
            pdf = fitz.open(str(path))
        except Exception as exc:
            ctx.error("PARSE_FAILED", f"Could not open PDF: {exc}")
            return ctx

        if pdf.is_encrypted and not pdf.authenticate(""):
            pdf.close()
            ctx.warn(
                "ENCRYPTED_PDF",
                "This PDF is password-protected and could not be read. "
                "No text could be extracted. To fix this, either open the PDF in a reader, "
                "remove the password, and save a new copy — or, if you have the password, "
                "decrypt the file first (e.g. qpdf --decrypt --password=PASS in.pdf out.pdf).",
            )
            ctx.error("PARSE_FAILED", "PDF is password-protected — provide a decrypted copy.")
            return ctx

        page_count = pdf.page_count

        # PyMuPDF can report 0 pages when the xref/object table has corrupted metadata
        # even though the content stream is intact. Try pdfplumber, which uses pdfminer
        # and is more tolerant of structural metadata corruption.
        if page_count == 0 and not pdf.is_encrypted:
            pdf.close()
            return _pdfplumber_fallback(path, ctx)

        if page_count > _MAX_PDF_PAGES:
            pdf.close()
            ctx.error(
                "PDF_TOO_MANY_PAGES",
                f"PDF has {page_count} pages; limit is {_MAX_PDF_PAGES}. "
                f"Set AKSHARAMD_MAX_PDF_PAGES to increase the limit.",
            )
            return ctx

        # Collect document-level metadata before Phase 1 I/O (independent of page content).
        pdf_metadata = dict(pdf.metadata)
        toc = pdf.get_toc()  # [[level, title, page], ...]

        # Phase 1: I/O extraction
        if page_count >= _PARALLEL_IO_THRESHOLD:
            # Large document — each thread opens its own fitz + pdfplumber handle so
            # chunks can be read concurrently.  Sharing a single fitz.Document across
            # threads is not safe; separate Document objects on the same file are fine.
            pdf.close()
            chunks = _chunk_pages(page_count)
            extractor = partial(_extract_page_chunk, str(path))
            raw_pages: list[RawPage] = []
            with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
                for chunk_result in pool.map(extractor, chunks):
                    raw_pages.extend(chunk_result)
            raw_pages.sort(key=lambda p: p.page_num)
        else:
            # Small document — reuse the already-open fitz handle; open pdfplumber
            # alongside for the borderless-table fallback.
            pdf_pl = None
            try:
                import pdfplumber
                pdf_pl = pdfplumber.open(str(path))
            except Exception:
                logger.debug("pdfplumber unavailable; borderless-table fallback disabled")
            try:
                raw_pages = [_extract_raw_page(pdf, i + 1, pdf_pl) for i in range(page_count)]
            finally:
                if pdf_pl is not None:
                    pdf_pl.close()
            pdf.close()

        # Phase 1 complete — emit a summary of what was found so the CLI can
        # show the user what's in the document before Phase 2 starts.
        if ctx.progress:
            image_pages = sum(1 for r in raw_pages if r.ocr_pixmap is not None)
            math_pages = sum(
                1 for r in raw_pages if len(r.math_bboxes) >= _MATH_EMPTY_SPAN_THRESHOLD
            )
            text_pages = page_count - image_pages
            parts = [f"{text_pages} text page{'s' if text_pages != 1 else ''}"]
            if image_pages:
                parts.append(f"{image_pages} image-only")
            if math_pages:
                parts.append(f"{math_pages} with equations")
            ctx.progress(f"Scanned {page_count} pages: {', '.join(parts)}")

        # Phase 2: Global analysis
        median = _median_font_size(raw_pages)
        removable = _detect_removable_spans(raw_pages)
        has_toc = len(toc) >= 3
        pdf_classification, pdf_stats = _classify_pdf(raw_pages)

        # Phase 3: Parallel processing — pure Python, no shared state
        results: dict[int, tuple[list[Block], list[Asset]]] = {}
        column_info_by_page: dict[int, dict] = {}
        workers = min(8, max(1, page_count))

        if workers > 1 and page_count > 4:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_process_raw_page, raw, removable, median, has_toc): raw.page_num
                    for raw in raw_pages
                }
                for future in as_completed(futures):
                    page_num, blocks, assets, col_info = future.result()
                    results[page_num] = (blocks, assets)
                    column_info_by_page[page_num] = col_info
        else:
            for raw in raw_pages:
                page_num, blocks, assets, col_info = _process_raw_page(raw, removable, median, has_toc)
                results[page_num] = (blocks, assets)
                column_info_by_page[page_num] = col_info

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

        # Collect rejected table candidates from all raw pages into document metadata.
        # Keyed by page number (int) so validators can look up by page.
        rejected_by_page: dict[int, list[dict]] = {}
        for raw in raw_pages:
            if raw.rejected_candidates:
                rejected_by_page[raw.page_num] = raw.rejected_candidates
        if rejected_by_page:
            pdf_metadata["table_rejected_candidates_by_page"] = rejected_by_page

        # Phase 4.5: Stitch tables split across page breaks into single tables
        page_heights = {raw.page_num: raw.height for raw in raw_pages}
        all_blocks = _stitch_page_break_tables(all_blocks, page_heights)
        for i, blk in enumerate(all_blocks):
            all_blocks[i] = blk.model_copy(update={"index": i})
        idx = len(all_blocks)

        # Phase 5: Vision enhancement — re-extract image-only pages with Marker
        vision_pages = 0
        ocr_hallucination = False
        if _marker_available():
            image_page_count = sum(1 for r in raw_pages if r.ocr_pixmap is not None)
            if image_page_count and ctx.progress:
                if not _MARKER_LOAD_ATTEMPTED:
                    ctx.progress(
                        "Vision model: loading Marker"
                        " (first use downloads ~3 GB — subsequent runs are fast)"
                    )
                ctx.progress(
                    f"Vision model: reconstructing {image_page_count} image page"
                    f"{'s' if image_page_count != 1 else ''} (Marker)"
                )
            all_blocks, marker_assets, vision_pages, ocr_hallucination = _apply_marker_to_image_pages(
                path, raw_pages, all_blocks
            )
            all_assets.extend(marker_assets)
            if vision_pages and ctx.progress:
                ctx.progress(f"Vision complete: {vision_pages} page{'s' if vision_pages != 1 else ''} reconstructed")

        # Phase 6: Math OCR — recover undecodable font spans (math symbols) via pix2tex
        math_equations = 0
        if _math_available():
            math_page_count = sum(
                1 for r in raw_pages if len(r.math_bboxes) >= _MATH_EMPTY_SPAN_THRESHOLD
            )
            if math_page_count and ctx.progress:
                ctx.progress(f"Math OCR: extracting equations from {math_page_count} page{'s' if math_page_count != 1 else ''} (pix2tex)")
            all_blocks, math_equations = _apply_math_ocr_to_blocks(path, raw_pages, all_blocks)
            if math_equations and ctx.progress:
                ctx.progress(f"Math OCR complete: {math_equations} equation{'s' if math_equations != 1 else ''} extracted")

        multi_column_pages = sorted(
            pg for pg, ci in column_info_by_page.items() if ci["num_columns"] > 1
        )
        # Store column info for ALL pages (not just multi-column) so the
        # multicolumn validator can access page_width for independent cluster analysis.
        pdf_metadata["pdf_column_info"] = dict(column_info_by_page)
        pdf_metadata["pdf_multi_column_pages"] = multi_column_pages

        pdf_metadata["pdf_classification"] = pdf_classification
        pdf_metadata["pdf_stats"] = pdf_stats
        # When Marker processed image pages, Surya served as the OCR engine —
        # mark OCR as available so validators don't emit OCR_REQUIRED.
        pdf_metadata["pdf_ocr_available"] = _ocr_available() or vision_pages > 0
        pdf_metadata["pdf_vision_available"] = _marker_available()
        pdf_metadata["pdf_vision_pages"] = vision_pages
        pdf_metadata["pdf_ocr_hallucination"] = ocr_hallucination
        pdf_metadata["pdf_math_available"] = _math_available()
        pdf_metadata["pdf_math_equations"] = math_equations

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
