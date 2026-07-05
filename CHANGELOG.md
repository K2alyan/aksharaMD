# Changelog

All notable changes to AksharaMD are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) / [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — PDF parser (feed-sid, 2026-07-05, continued)
- **Cover-page bordered layouts rendered as garbled word-split tables**: Some technical reports have a bordered letterhead grid on the cover page. PyMuPDF detected this as a table and extracted it with mid-word cell splits (e.g. "Company Nam L" | "e, Inc."). The Pattern B adj_split ratio was 27%, just below the 30% rejection threshold. Fixed by including the header row in the adj_split/adj_total count (after stripping cell padding spaces); the header's own word-split pair pushes the combined ratio to 33% → rejected. The cover page content now flows as clean prose.
- **Standalone digit spans removed as page numbers mid-page**: `_PAGE_NUM_RE` matched `^\d+$` anywhere on the page, silently dropping order quantities ("1"), footnote numbers, and list counters mid-page. Fixed by applying the bare-digit pattern only when the span is in the top 12% or bottom 12% of the page (where actual page numbers live). Multi-token patterns ("Page N of M", print timestamps, ranges) are still removed globally.
- **Financial amounts dropped from short PDFs (email receipts)**: `_detect_removable_spans` computed deduplication thresholds as `max(2, int(page_count × 0.4))`. For a 2-page document (e.g. a 2-page email receipt export with a charge-summary page and a full itemised receipt page), the threshold was 2 — any text appearing on both pages was stripped as a "running duplicate". Dollar amounts, line-item labels, and totals all disappeared from the output. Fixed by raising the minimum to 3, so deduplication cannot fire on documents with ≤ 2 pages.
- **LaTeX `\lineno` document rendered as pipe table**: pdfplumber's whitespace-strategy detected the left-margin line numbers (1, 2, 3…) as a two-column table, wrapping the entire document body in pipe-table markdown. Two-pronged fix: (1) `_is_quality_table` now rejects tables whose header first cell is a small bare integer (≤ 20) in a ≤ 3-column table, and rejects tables where the header first cell is a digit sequence followed by an uppercase letter ("1 S" = line 1 bleeding into the first character "S" of the next word); (2) a new `_filter_latex_line_numbers()` function strips span-level line numbers from the span list before any processing, so they cannot appear as table cells or prose text.
- **2-column paragraph merging (multi-column regression)**: in multi-column academic papers, all text within each column accumulated into a single massive paragraph because there was no mechanism to detect paragraph boundaries within a column. Added gap-based paragraph flushing: when consecutive spans in the same column have a baseline-to-baseline gap larger than 1.8 × the previous span's font size, the current paragraph is flushed before the next span is appended. This correctly separates paragraphs and section labels from the following body text without affecting tightly-spaced running text.

### Fixed — PDF parser (feed-sid, 2026-07-05)
- **Heading over-detection in 2-column journals**: `_heading_level()` now requires bold or all-caps evidence for the H3 threshold (ratio 1.3–1.6). Previously, any span at ratio ≥ 1.3 unconditionally became H3. In 2-column academic journals where reference text dominates the document (median ~7pt vs 10pt body text), this caused every body-text line to be classified as a heading (20+ false H3 per page). Fix also adds a `_prose` guard (starts lowercase/punctuation, ends `,`/`;`, or contains a URL) applied across H3, H4, H5, and the is_caps fallback rule.
- **`is_caps` false positive on geographic abbreviations**: Python's `str.isupper()` returns `True` for `"CA, USA."` (all *cased* characters are uppercase). Added an exclusion for texts that contain both a comma and a period.
- **Table over-detection**: replaced raw drawing-line counts with interior intersection geometry (`_has_interior_intersections`). A decorative page border (single rectangle) generates zero interior crossings; a real table grid crosses column-dividers against row-dividers at ≥ 3 interior points. Threshold = 3.
- **Duplicate table content in body text**: `_filter_table_spans()` used exact center-point matching; spans whose center was just outside the table bbox leaked into prose. Added a 6pt margin on each side.
- **CID glyph artifacts in table cells**: `_CID_RE.sub()` was applied to span text but not to table cells extracted via `tab.extract()`. Now also applied inside `norm()` in `_cells_to_markdown()`.
- **Page furniture leaking into table cells**: print timestamps (`MM/DD/YYYY HH:MM AM Page N`) and copyright year strings appeared as table cell content. Removed via `_CELL_FURNITURE_RE` inside `norm()`.
- **TOC dot-leader rows**: tightened pattern from `\.{3,}` to `\.{5,}` to avoid rejecting cells containing ellipsis (`...`).
- **Column count cap**: tables with >8 columns are rejected as garbage (pdfplumber word-strategy false positives on dense pages).

### Added
- PDF table extraction via `tab.extract()` + custom renderer — eliminates ColN artifacts from multi-row headers
- PDF embedded image OCR: JBIG2/CCITT formats now decoded via Pixmap before passing to Tesseract
- DOCX math equations: `_omml_to_latex` recursive converter (superscript, subscript, fractions, radicals, integrals, matrices); inline `$...$` vs block `$$...$$` correctly distinguished; mixed text+math paragraphs rendered in document order
- Dockerfile for MCP streamable-http deployment (Tesseract + ffmpeg included)
- Version single-source-of-truth: `pyproject.toml` is the only hardcoded version; `__init__.py` and `manifest.py` read from `importlib.metadata`

## [0.2.0] — 2026-07-02

### Added
- **Examples**: four runnable partner onboarding scripts (`examples/01_compile_file.py`, `02_compile_url.py`, `03_batch_compile.py`, `04_extract_and_chunk.py`) covering the full Python API surface
- **CI restructure**: two-tier GitHub Actions — `fast-gate` job (lint + mypy + bandit + tests at 75% coverage, every push) and `integration` job (Tesseract system dep, full suite, PRs to main only)
- **Test suite expansion**: 298 tests across 20 files; 75.72% coverage; new coverage for notebook, EML, RTF, archive (tar/gz/7z), multimodal exporter, PPTX, ODF, EPUB, image parser, and CLI e2e paths
- Audio parser (`audio.py`) and legacy office parser (`legacy_office.py`) excluded from coverage gate; both require unavailable system binaries in CI

### Fixed
- `_read_7z` in `archive_tar.py` called non-existent `SevenZipFile.read()` (py7zr 1.x API); replaced with `sz.extract()` to a `tempfile.TemporaryDirectory()`
- pdfplumber borderless table detection for whitespace-aligned tables

### Changed
- README rewritten: accurate CLI reference, verified Python API examples, multimodal API section, Known Limitations, Optional Dependencies table, and updated benchmark numbers

## [0.1.0] — 2026-06-30

### Added — Security, testing, CI/CD
- 100-test suite across 11 files (parsers, plugins, security, MCP)
- GitHub Actions CI: ruff lint + bandit security scan + pytest with 60% coverage threshold on Python 3.11 + 3.12
- Security: HTML path-traversal block on image src; 500 MB file size gate (env-configurable via `AKSHARAMD_MAX_FILE_BYTES`); Whisper model whitelist; structured MCP error logging replacing silent `except: pass`
- Bug fixes: markdown list duplicate-emit; IMAGE block deduplication in TokenOptimizer; list/code indentation preservation in DefaultCleaner

### Added — DOCX nested lists
- DOCX list paragraph detection via `w:numPr` XML (numId + ilvl) with style-name fallback
- Ordered vs. unordered resolved from `w:abstractNum` numbering definitions
- 2-space indent per `ilvl`; per-level counters reset on ascent

### Added — Multimodal pipeline and 7 parser optimizations
- `compile_to_multimodal()`: interleaves text + image blocks for multimodal LLM input
- Image asset extraction from PDF, DOCX, PPTX, HTML, EPUB into `Asset` objects
- MCP tool `compile_document_multimodal` returning `MCPImage` sequences
- RST parser via docutils html5 writer + HTML walker
- PDF: TOC block from `get_toc()` bookmarks; tighter heading thresholds when TOC present
- HTML/Markdown: recursive nested list rendering with depth indentation
- PPTX: layout-inherited bullet detection via placeholder type and `lvl` attribute
- XLSX: merged cell expansion; `read_only` mode for files > 10 MB
- URL input: `http://`/`https://` sources fetched to temp file before compilation

### Added — Core pipeline
- 10-stage document compilation pipeline: detect → parse → clean → optimize → validate → chunk → tokenize → manifest → score → export
- 35+ format parsers covering PDF, DOCX, PPTX, XLSX/XLS, ODF, HTML, Markdown, RST, EPUB, RTF, EML/MSG, JSON/CSV/YAML/TOML/XML/RSS, audio (Whisper), images (Tesseract OCR), ZIP/TAR/7z archives, Jupyter notebooks, legacy Office via LibreOffice
- CLI (`aksharamd compile`, `aksharamd stats`) with Rich progress display
- MCP server (`aksharamd-mcp`) with stdio and streamable-http transport
- TokenOptimizer: deduplication, header/footer removal, fragment merging — 20–80% token reduction
- Semantic chunker with configurable token budget
- AI Readiness Score 0–100
- Persistent ledger (`~/.aksharamd/ledger.jsonl`) for lifetime savings tracking
- Markdown + JSON exporters

### Benchmark (v3 vs MarkItDown on 101-file corpus)
- OmniMark: avg 21,199 tokens, 1.40s, 3.7 noise lines, 23 format types
- MarkItDown: avg 331,171 tokens, 0.48s, 250.1 noise lines, 16 format types
- OmniMark wins token efficiency in 21/25 type comparisons (15.6× fewer tokens overall)
