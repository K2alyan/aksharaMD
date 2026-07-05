# Changelog

All notable changes to AksharaMD are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) / [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-07-05 — Private Beta

### Added
- **AI Readiness Score with quality bands**: every compilation returns a 0–100 confidence score
  labelled HIGH (≥85) / OK (≥70) / RISKY (≥50) / POOR (<50). Score and band appear in the
  CLI panel, `manifest.json`, and the Python API (`manifest.readiness_score`, `manifest.quality_band`).
- **Plain-English validation warnings**: nine warning codes (OCR_REQUIRED, GLYPH_ARTIFACTS,
  NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY, TOKEN_BLOAT, REPEATED_CONTENT, ENCRYPTED_PDF,
  MISSING_PAGE, LARGE_BLOCK) rewritten as actionable, non-developer messages with install
  instructions and fix suggestions.
- **PDF classification**: each PDF is classified as `native_text`, `scanned`, `hybrid`,
  `table_heavy`, `layout_heavy`, or `low_confidence` and reported in the CLI, manifest, and
  confidence notes.
- **Encrypted PDF detection**: password-protected PDFs are detected before parsing and produce
  a clear user-facing error with decryption instructions (`qpdf --decrypt`).
- **OCR-required warning**: scanned and hybrid PDFs without Tesseract installed produce a
  POOR score and a targeted install instruction rather than silent empty output.
- **Manifest clarity fields**: `quality_band`, `pdf_classification`, `ocr_available`,
  `image_pages`, `warning_codes` added to `manifest.json` and the `Manifest` Pydantic model.
- **Output Files panel**: CLI always shows the exact paths of all output files after compilation.
- **MCP server**: `aksharamd mcp-config --write` wires AksharaMD into Claude Desktop as
  four tools — `compile_document`, `compile_document_multimodal`, `get_supported_formats`,
  `get_stats`. HTTP mode with rate limiting and path-restriction is also supported.
- **Corpus pipeline**: `aksharamd corpus <dir>` walks a directory, deduplicates near-identical
  documents via MinHash LSH, and packs results into token-budget-bounded chunks ready for
  vector stores and RAG pipelines. Python API: `Compiler.compile_corpus()`.
- **Multimodal output**: `Compiler.compile_to_multimodal()` returns an Anthropic-compatible
  content array with text and base64 images interleaved at their document positions.
- **118 registered extensions** across 40+ user-facing document categories.

### Fixed
- **Windows CLI crash (U+26A0 ⚠)**: the WARNING SIGN character is not encodable in the
  legacy Windows cp1252 console; replaced with ASCII `!` in the Warnings panel.
- **NEAR_EMPTY_OUTPUT false positive on short files**: single-page and two-page documents
  (`.txt`, `.md`, and other formats) no longer score RISKY due to low byte count. The check
  now requires ≥ 3 pages. Single/two-page PDFs are still covered by `LOW_TEXT_DENSITY`.
- **NEAR_EMPTY_OUTPUT message referenced "PDF" for non-PDF files**: message is now
  format-aware; the OCR install hint only appears for PDF files.
- **Penalty stacking on scanned PDFs**: OCR_REQUIRED, NEAR_EMPTY_OUTPUT, and LOW_TEXT_DENSITY
  were all deducting independently for the same missing-content gap. OCR_REQUIRED now
  suppresses the other two penalties when it fires.
- **LOW_TEXT_DENSITY false positive on table-heavy PDFs**: TABLE blocks now counted alongside
  PARAGRAPH and HEADING in the density calculation.
- **TOKEN_BLOAT unreachable threshold**: lowered from 2500 to 1500 tokens/page to match
  realistic dense PDF output (600–1200 tokens/page typical).
- **Scanned PDF misclassified as hybrid**: image-ratio threshold raised from 0.70 to 0.80.
- **Encrypted PDF manifest gap**: pipeline now surfaces ENCRYPTED_PDF warning in the CLI even
  when manifest building is skipped due to early-exit.
- **README license footer**: corrected "MIT" to "PolyForm Noncommercial 1.0.0".
- **`[ocr]` stripped as Rich markup**: warning messages and confidence notes now passed through
  `rich.markup.escape()` so bracket-containing install commands render correctly.

### Changed
- README repositioned: AksharaMD is an LLM ingestion pipeline, not a Markdown converter.
  "Why AksharaMD" section now leads with the raw-file problem before benchmark numbers.
- CLI `--help` description updated to "LLM Document Ingestion Pipeline".
- GLYPH_ARTIFACTS penalty increased from −15 to −25 to push garbled-text extractions into
  the RISKY band (<70) where they belong.

## [0.2.0] — 2026-07-05

### Fixed — PDF parser
- **Horizontal-only ruled tables bypassing `find_tables()` (regression)**: Tables drawn with an outer border rectangle and horizontal row-divider lines but no internal vertical column dividers produce zero interior crossings — `_has_ruled_table` returned False, `find_tables()` was skipped, and pdfplumber's text-strategy produced garbled output. Added a secondary detection path: ≥ 3 h-lines whose widths are within 15% of the median width indicate parallel row-dividers. A single page-border rectangle adds only 2 h-lines and cannot trigger this path.
- **2-column prose pages misdetected as tables**: pdfplumber's `min_words_vertical` was 3, easily met by any prose column. Raised to 5 so column detection requires more evidence; this filters out short multi-column blocks (title pages, masthead layouts) while still catching legitimate borderless tables with 5+ rows.
- **Prose-length cells not rejected**: added an avg-words-per-cell guard to `_is_quality_table` — if the average non-empty data cell exceeds 8 words it is narrative text wrapped across columns, not tabular data.
- **Word-split tables with 2 data rows not rejected**: `_is_quality_table` Pattern B (adj-cell split ratio) was guarded by `len(data_rows) >= 3`, so 2-row word-split tables slipped through as "quality". Lowered threshold to `>= 2`.
- **Layout-column tables with sparse data not rejected**: pdfplumber whitespace-strategy occasionally detects paragraph-in-column layouts as multi-column tables where >50% of cells are empty spacers. Added a pre-Pattern-B guard: if more than 50% of data cells across all rows are empty, the table is rejected as a layout artifact.
- **Bold body-font headings not detected**: section labels at body font size (ratio ≈ 1.0) were absorbed into paragraph text. Added a fallback rule: if a span is bold, ≤4 words, not prose, and does not end with `:`, it is promoted to H4. Fires only when no TOC is present.
- **Cover-page bordered layouts rendered as garbled word-split tables**: Some technical reports have a bordered letterhead grid on the cover page. PyMuPDF detected this as a table and extracted it with mid-word cell splits (e.g. "Company Nam L" | "e, Inc."). Fixed by including the header row in the adj_split/adj_total count; the header's own word-split pair pushes the combined ratio over the 30% rejection threshold.
- **Standalone digit spans removed as page numbers mid-page**: `_PAGE_NUM_RE` matched `^\d+$` anywhere on the page, silently dropping quantities, footnote numbers, and list counters. Fixed by applying the bare-digit pattern only when the span is in the top 12% or bottom 12% of the page.
- **Financial amounts dropped from short PDFs**: deduplication threshold `max(2, ...)` caused any text appearing on both pages of a 2-page document to be stripped. Raised minimum to 3 so deduplication cannot fire on documents with ≤ 2 pages.
- **LaTeX `\lineno` document rendered as pipe table**: pdfplumber whitespace-strategy detected left-margin line numbers as a two-column table. Two-pronged fix: `_is_quality_table` rejects tables with small bare-integer or digit+letter header cells; new `_filter_latex_line_numbers()` strips them at the span level before table detection runs.
- **2-column paragraph merging**: in multi-column layouts all text within each column accumulated into a single paragraph. Added gap-based paragraph flushing when the baseline-to-baseline gap exceeds 1.8 × the previous span's font size.
- **Heading over-detection in 2-column layouts**: `_heading_level()` now requires bold or all-caps evidence for the H3 threshold (ratio 1.3–1.6). Added a `_prose` guard applied across H3, H4, H5, and the is_caps fallback rule.
- **`is_caps` false positive on geographic abbreviations**: added an exclusion for texts that contain both a comma and a period.
- **Table over-detection**: replaced raw drawing-line counts with interior intersection geometry (`_has_interior_intersections`). A decorative page border generates zero interior crossings; a real table grid produces ≥ 3.
- **Duplicate table content in body text**: `_filter_table_spans()` now uses a 6pt margin on all sides instead of exact center-point matching.
- **CID glyph artifacts in table cells**: `_CID_RE.sub()` now applied inside `norm()` in `_cells_to_markdown()`.
- **Page furniture leaking into table cells**: print timestamps and copyright year strings removed via `_CELL_FURNITURE_RE` inside `norm()`.
- **TOC dot-leader rows**: tightened pattern from `\.{3,}` to `\.{5,}` to avoid rejecting cells containing ellipsis (`...`).
- **Column count cap**: tables with >8 columns are rejected as garbage.
- **Unit abbreviations flagged as word-split fragments**: short measurement abbreviations ("pct", "mph", "psi", "cfm", "rpm", "lbs", etc.) added to `_FRAG_WHITELIST`.

### Added
- **Examples**: four runnable partner onboarding scripts (`examples/01_compile_file.py`, `02_compile_url.py`, `03_batch_compile.py`, `04_extract_and_chunk.py`) covering the full Python API surface
- **CI restructure**: two-tier GitHub Actions — `fast-gate` job (lint + mypy + bandit + tests at 75% coverage, every push) and `integration` job (Tesseract system dep, full suite, PRs to main only)
- **Test suite expansion**: 434 tests across 32 files; 75.02% coverage
- PDF table extraction via `tab.extract()` + custom renderer — eliminates ColN artifacts from multi-row headers
- PDF embedded image OCR: JBIG2/CCITT formats now decoded via Pixmap before passing to Tesseract
- DOCX math equations: `_omml_to_latex` recursive converter; inline `$...$` vs block `$$...$$` correctly distinguished
- Dockerfile for MCP streamable-http deployment (Tesseract + ffmpeg included)
- Version single-source-of-truth: `pyproject.toml` is the only hardcoded version; `__init__.py` and `manifest.py` read from `importlib.metadata`

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
