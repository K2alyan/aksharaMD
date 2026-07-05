# Engineering Decisions — AksharaMD PDF Parser

This file captures *why* specific decisions were made in the PDF parser
(`aksharamd/plugins/parsers/pdf.py`).  Read this before changing any of the
rules below — most of them exist to fix a specific real-world regression and
the fix is not obvious from the code alone.

---

## 1. Heading detection (`_heading_level`)

### Problem
`_heading_level` compares a span's font size against the **document-wide median**
font size.  In 2-column academic journals (Lancet, Nature Medicine) a single page
often has:

| Text type       | Typical size | Approx. % of spans |
|-----------------|-------------|---------------------|
| Body text       | 9–10 pt     | ~40%                |
| References/footnotes | 6.5–8 pt | ~55%           |
| Headings        | 12–18 pt    | ~5%                 |

Because reference text dominates the document, `statistics.median()` lands at
~7 pt.  Body text at 10 pt then has **ratio = 1.43**, which exceeded the old
unconditional `if ratio >= 1.3: return 3` threshold.  Every line of body text
became an H3 heading (20+ false positives per page on the Lancet mpox article).

### Decision: require bold or all-caps for H3 at ratio 1.3–1.5

For ratio 1.3–1.6 the function now requires **explicit heading evidence**:
- `bold = True` (PyMuPDF flag bit 16), OR
- `is_caps = True` (see caveat below)

A secondary word-count fallback (`≤ 5 words`) only fires at `ratio >= 1.5`,
where body-text contamination is much rarer.

**What was tried and rejected:**
- *Word count only* (≤ 10 words): individual wrapped lines in a paragraph can
  easily be 3–8 words, so "148 deaths worldwide." (3 words) still triggered.
- *Ends-in-period exclusion*: paper titles and full-sentence headings also end
  in periods; too broad.
- *Switching to 75th-percentile baseline*: cleaner fix but changes every
  document's heading ratios simultaneously — high regression risk, punted.

**Do not revert to the unconditional `return 3`.**  It catastrophically breaks
all Lancet-style journals.

### Decision: `_prose` signal applied to ALL heading levels

A span is considered body prose if:
- `text[0].islower()` or `text[0] in ".,;:("` — mid-sentence fragment
- `text.endswith(",")` or `text.endswith(";")` — sentence runs to next line
- `"http" in text` — URL/DOI metadata annotation, never a heading

This guard is now applied to H3, H4, H5, and the is_caps fallback rule.  It
was scoped to H3 initially but "yap.boum2@pasteur-bangui.cf; @YapBoum2"
(bold, 8.5 pt, ratio 1.21) still hit the H4 rule without the guard.

### Decision: `is_caps` excludes texts with both comma and period

Python's `str.isupper()` returns `True` for `"CA, USA."` because *all cased
characters* (C, A, U, S, A) are uppercase; punctuation is ignored.  Geographic
abbreviations were triggering the all-caps heading path.

Fix: `is_caps = text.isupper() and len(text) > 3 and not ("," in text and "." in text)`

Legitimate all-caps headings (`INTRODUCTION`, `METHODS`, `THE LANCET INFECTIOUS
DISEASES`) do not contain both a comma and a period.

---

## 2. Ruled table detection (`_has_ruled_table`)

### Problem
PyMuPDF's `page.find_tables()` fires on any page that has drawing lines,
including decorative page borders.  A TÜV SÜD quote PDF with a single
rectangular border on every page was producing 5 spurious table extractions
per page.

### Decision: interior intersection geometry, not line counts

A decorative border (one rectangle) has drawing lines that only intersect at
**corners** (endpoints).  A real table grid has column-divider lines that cross
row-divider lines at **interior** points (strictly inside the horizontal line's
x-span).

`_has_interior_intersections(h_lines, v_lines, tol=5, threshold=3)` counts
how many (h, v) pairs where `hx0 + tol < vx < hx1 - tol` and the v-line's
y-span overlaps the h-line's y.  A single page border produces 0 interior
crossings; a 2-column 3-row table produces ≥ 6.  Threshold is set at 3
(rejects a border + single divider, accepts a minimal real table).

**Do not replace this with raw line counts.**  The old heuristic (counting
distinct y-buckets) incorrectly triggered on pages with many horizontal rules
that weren't table row-dividers.

The geometry check gates `page.find_tables()` — if `_has_ruled_table()` is
False, we skip PyMuPDF table extraction entirely for that page and fall through
to pdfplumber whitespace-strategy detection.

---

## 3. Borderless table detection (`_try_pdfplumber_tables`)

### Decision: pdfplumber text-strategy, not ruling-line strategy

pdfplumber's default `vertical_strategy="lines"` only finds tables with ruling
lines — same limitation as PyMuPDF.  `vertical_strategy="text"` clusters
columns by text x-position, catching whitespace-aligned tables (e.g., financial
statements, ASHRAE specifications).

Settings used (`_PDFPLUMBER_TEXT_SETTINGS`):
```python
{
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_x_tolerance": 3,
    "snap_y_tolerance": 3,
    "min_words_vertical": 3,
    "min_words_horizontal": 3,
}
```

`min_words_*` = 3 prevents single-column text flows from being detected as
tables.  Lower values produce too many false positives on dense narrative pages.

pdfplumber is skipped for pages with `total_chars > _PDFPLUMBER_CHAR_LIMIT`
(~8,000 chars) because on dense pages it clusters almost everything as a table.

### Decision: pdfplumber bboxes are flipped before storing

pdfplumber uses PDF bottom-left origin (y increases upward).  PyMuPDF uses
top-left origin (y increases downward).  `_try_pdfplumber_tables()` converts:
```python
y0_fitz = page_height - pl_bbox[3]
y1_fitz = page_height - pl_bbox[1]
```
The stored bbox is always in PyMuPDF coordinates.  All downstream code
(`_filter_table_spans`, span overlap checks) uses PyMuPDF coords.  **Do not
remove this flip** — it will cause tables to be placed at the wrong vertical
position and span-filtering to fail.

---

## 4. Table quality filtering (`_is_quality_table`)

Filters are applied in order; first match that rejects wins.

| Check | Threshold | Reason |
|-------|-----------|--------|
| `< 3 lines` | always | not enough structure |
| `< 2 columns in header` | always | single-column = not a table |
| `> 8 columns` | always | pdfplumber text-strategy word-splits long sentences into many "columns" |
| `< 1 data row` | always | header-only = not useful |
| TOC dot-leaders `\.{5,}` | > 40% rows | table of contents detection |
| Short alpha fragments | > 25% rows | rejects split-sentence false positives |
| Pattern A: single-letter first cell + lowercase next | > 20% rows | list-like layout, not a table |
| Pattern B: adjacent cells left-ends-alpha, right-starts-lowercase | > 30% pairs | word-split paragraphs |

**The column cap was changed from ≤ 6 to ≤ 8.**  A cap of 6 rejected a
legitimate 7-column pricing table in the TÜV SÜD document.  A cap of 8 still
rejects the worst word-split false positives (10–15 columns).

**The dot-leader threshold was changed from `\.{3,}` to `\.{5,}`.**  Three
dots (`...`) appears in real table cells (truncated text, ellipsis); five dots
(`.....|`) is always a TOC leader.

---

## 5. Span filtering around tables (`_filter_table_spans`)

### Problem
After extracting a table's cells, the spans that overlap with the table bbox
should not also appear as body text (duplicate content).  The original code
used exact center-point containment: `cx = (x0 + x1) / 2; if tx0 <= cx <= tx1`.
Spans whose center was just outside the table bbox (e.g., a span beginning
1px before the table's left edge) leaked through and appeared as prose alongside
the table.

### Decision: 6pt margin on all sides

```python
_MARGIN = 6.0  # pt
if tx0 - _MARGIN <= cx <= tx1 + _MARGIN and ty0 - _MARGIN <= cy <= ty1 + _MARGIN:
```

6pt ≈ 2mm, covering typical cell padding and alignment jitter.  Do not increase
this significantly — a large margin will suppress spans that legitimately appear
near tables without being part of them (e.g., figure captions adjacent to a table).

---

## 6. Cell content normalization (`norm()` in `_cells_to_markdown`)

Table cells go through `tab.extract()` → `_cells_to_markdown()` → `norm()`,
which is a **different code path** from span extraction.  Fixes that apply to
span text must also be applied in `norm()`.

Current `norm()`:
```python
def norm(v) -> str:
    text = re.sub(r"\s+", " ", _CID_RE.sub("", (v or "").replace("|", "\\|"))).strip()
    return "" if _CELL_FURNITURE_RE.match(text) else text
```

- `_CID_RE`: removes `(cid:N)` glyphs from fonts with no Unicode mapping.
  These appear as literal `(cid:42)` strings in PyMuPDF's table extraction
  output and were appearing verbatim in table cells.
- `_CELL_FURNITURE_RE`: removes print timestamps, "Page N of M", and
  `"20XX ©"` copyright strings that Lancet/Elsevier PDFs embed in table cells.
  Note: `_PAGE_NUM_RE` was used here initially but it includes `^\d+$`, which
  matched numeric data like "100".  `_CELL_FURNITURE_RE` is intentionally
  narrower to avoid rejecting valid numeric cells.

---

## 7. Multi-column reading order

`_detect_column_boundaries()` identifies columns by clustering line-start
x-positions (leftmost span per text line).  Gaps > 10% of page width between
consecutive x-clusters indicate a column boundary.  The boundary midpoint is
stored.

`_process_raw_page()` then sorts spans by `(column_index, y)` before iterating,
ensuring spans are read left-column-first, top-to-bottom within each column.

**Known limitation**: the column boundary `0.30 < xs[i] < 0.70` constraint was
relaxed in the current implementation (the check is on `xs[i]`, the right
column's leftmost x, not the midpoint).  For 3-column layouts with columns at
~4%, ~37%, ~67% of page width, this correctly detects two boundaries.

For Lancet corrections pages (3-column of correction notices), the reading order
is correct (col1 → col2 → col3), but the correction headers are still
fragmented into multiple heading blocks because each line of the header is a
separate bold span in the PDF.

---

## 8. Known limitations to address in future work

| Issue | Impact | Notes |
|-------|--------|-------|
| Median pulled by reference text | H3 false positives suppressed but H4/H5 at bold could still misfire in extreme cases | Consider 70th-percentile baseline |
| 2-column para interleaving | Some sentences from adjacent columns merge into one paragraph block | Requires paragraph-boundary detection across columns |
| Correction-page header fragmentation | "Correction to / Lancet Infect Dis / 3099(20)30159-6" becomes 3 H3 blocks | Consecutive same-level heading spans should be joined |
| Scanned PDFs | OCR path falls back to Tesseract page-raster; structure (headings, tables) not recovered | Future: Tesseract HOCR output parsing |
| Table caption detection | `_CAPTION_RE` covers "Figure N" / "Table N" but not numbered equations or boxes | Extend regex if false negatives observed |
