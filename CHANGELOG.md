# Changelog

All notable changes to AksharaMD are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) / [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **PDF booktabs table detection (`_try_hrule_table`)**: tables that use only horizontal rules and no vertical lines (booktabs-style, common in Wiley/Dummies books) are now detected as a third fallback after PyMuPDF `find_tables()` and pdfplumber text-strategy. Uses `get_drawings()` horizontal rules as row separators and x-position gaps in the span distribution to determine column boundaries. Caption rows (matching "Table N-N", "Figure N") are excluded from the markdown so the column-header row becomes the table's first row, enabling cross-page stitching. Fixes Table 1-1 in *Fantasy Football For Dummies* (Wiley, 2007) — a 4-column table spanning pages 28-29 now renders as a single stitched markdown table instead of scattered prose fragments.

- **PDF block ordering (deferred table insertion)**: TABLE blocks were always prepended before prose blocks, placing tables that appear at the bottom of a page before the prose text that precedes them. TABLE blocks are now held in a `pending_tables` list and inserted at the correct y-position as prose spans are processed — tables whose top edge falls below a prose span are inserted just before that span.

- **PDF parallel parse performance**: switched Phase-1 page-extraction from `ProcessPoolExecutor` to `ThreadPoolExecutor`. On Windows, process spawning overhead added ~40 s to every compilation. PyMuPDF is thread-safe when each thread opens its own `fitz.Document`; thread-based parallelism eliminates the spawn cost while preserving concurrency.

- **PDF roman-numeral page numbers as headings**: running headers containing lowercase roman numerals (i, ii, xi, xiv …) were classified as H1 headings. Extended `_PAGE_NUM_RE` with a roman-numeral pattern; the existing header/footer zone guard in `_detect_removable_spans` now suppresses them correctly.

- **PDF drop-capital single characters as headings**: single-character spans with large font sizes (decorative initials, WileyCode drop capitals such as "I" or "S") were classified as H1 headings. Added an unconditional single-character guard at the top of `_heading_level` — a span of length 1 is never a heading regardless of font size.

- **Marker-pdf import failure loop**: if `marker-pdf` is installed but broken (e.g. missing `keras_nlp` backend), the import was retried on every page, adding ~5 s per compilation. Added a `_MARKER_LOAD_ATTEMPTED` sentinel that caches failure within the process.

## [0.3.5] — 2026-07-07

AksharaMD v0.3.5 is a production-credibility and ingestion-control release: tightened benchmark claims, clarified limitations, and two new CLI options for pipeline gating.

### Added

- **`--min-readiness-score INTEGER`** on `aksharamd compile`: exits non-zero when the readiness score is below the supplied threshold. Output files are still written. Designed as a CI/CD ingestion gate — e.g. `aksharamd compile doc.pdf --min-readiness-score 70` blocks low-quality extractions from entering a vector store automatically.
- **`--json`** on `aksharamd compile`: suppresses Rich panels and prints a single valid JSON object to stdout, containing `success`, `source`, `output_dir`, `readiness_score`, `quality_band`, `warning_codes`, `errors`, `chunks`, `pages`, `optimized_tokens`, and `elapsed_seconds`. Compatible with `--min-readiness-score` (`success: false` when threshold not met). Useful for scripting and CI pipelines.
- **"What AksharaMD does not guarantee"** section in README: explicitly separates extraction reliability from retrieval accuracy, answer correctness, citation correctness, and embedding quality. Advises running retrieval evals before production deployment.

### Fixed

- **Benchmark version consistency**: `benchmarks/LLM_QA_BENCHMARK.md` header now correctly states the benchmark was run on v0.3.3 (previously said v0.3.0); footer reconciles both version references. Current package (v0.3.5) noted with no parser changes affecting results.
- **Benchmark judge/answer-model clarity**: methodology section now explicitly names the answer model and judge model separately for all three validation runs (Claude Haiku 4.5, Gemini 2.5 Flash, GPT-4o mini). The GPT-4o mini per-format section no longer implies a single model served both roles without clarification.
- **Reproducibility section**: expanded with a clear split between what can be reproduced from committed files (100-document subset, harness, scoring prompts) and what requires assembling the full corpus from public sources.

### Improved

- **README benchmark claims**: hedged with corpus scope ("On our internal benchmark corpus..."), version references, and links to benchmark docs for methodology and reproducibility limitations. Results noted as corpus-composition-dependent.
- **`Compiler.stream()` docstring**: removed wording that implied streamed blocks are automatically safe for direct vector-store ingestion. Callers are now directed to apply readiness checks, chunking policy, and retrieval evaluation before embedding.
- **CLI docs**: `compile` command reference table documents `--min-readiness-score` and `--json` with JSON field reference.
- **Documentation restructure**: `BETA.md` replaced by `CONTRIBUTING.md` (installation check, format testing, bug report template, code contribution checklist). `DECISIONS.md` replaced by `ADR.md` (15 Architecture Decision Records covering all major design choices). Both linked from README.
- **CI action updates**: bumped `actions/setup-python` 5.6.0→6.3.0, `actions/checkout` 4.3.1→7.0.0, `actions/download-artifact` 4.3.0→8.0.1, `softprops/action-gh-release` 2.6.2→3.0.1, `codecov/codecov-action` 4.6.0→7.0.0.

## [0.3.4] — 2026-07-06

AksharaMD v0.3.4 is a production-readiness patch: security hardening, CLI fixes, output schema versioning, expanded CI, and benchmark/documentation additions.

### Added

- **`SECURITY.md`**: supported versions, responsible disclosure process (72 h acknowledgement, 14-day patch SLA), and in-scope attack surfaces (archive bombs, path traversal, SSRF, XML injection, PDF parser attacks, dependency CVEs).
- **`schema_version = "1.0"`** field added to all four exported JSON models (`Manifest`, `Document`, `ValidationReport`, `Chunk`) — enables downstream consumers to gate on schema compatibility.
- **Dependabot**: weekly dependency and GitHub Actions version updates (PRs capped at 5).
- **`pip-audit` in CI**: dependency vulnerability scan runs after bandit on every push.
- **Windows smoke job in CI**: `pytest tests/test_cli.py tests/test_security.py` on `windows-latest` (Python 3.11) validates CLI and archive safety on every push.
- **Wheel smoke test in publish workflow**: installs the built wheel in a clean environment and runs `aksharamd --help` before PyPI upload and GitHub release.
- **Benchmark methodology docs**: `benchmarks/corpus_manifest.json` (corpus provenance and per-format public availability), `benchmarks/scoring_prompt.md` (verbatim answer and judge prompts), `benchmarks/results/README.md` (summary results tables with reproduction instructions).
- **`docs/readiness-score.md`**: quality bands, per-format baselines, full penalty table, all warning codes with recommended actions, and known false positives.
- **`docs/output-schema.md`**: schema 1.0 compatibility guarantee and complete field reference for all four output models.
- **`docs/rag-integration.md`**: readiness-gated ingestion patterns, complete `AksharaMDLoader` (LangChain) and `AksharaMDReader` (LlamaIndex) implementations, RISKY document handling strategies, and environment variables table.

### Fixed

- **S3 CLI acceptance**: `aksharamd compile s3://bucket/key` no longer fails argument validation. `_SourceArg` and `_output_stem` now recognise `s3://` URIs alongside `http(s)://`.
- **Archive safety tests expanded**: decompression-bomb limit and nested-archive non-recursion are now covered by unit tests.

### Improved

- **Benchmark reproducibility caveats**: `benchmarks/corpus_manifest.json` and `benchmarks/results/README.md` now state clearly that raw document files are not committed (size/licensing), which formats are re-downloadable from public sources (arXiv, Wikipedia, Project Gutenberg), which are synthetic, and that the committed `eval_corpus_qa.yaml` is a 100-document validation subset with local absolute paths.
- **README**: documentation section links all new guides; format coverage count updated to "40+ document categories, 118 registered extensions".

## [0.3.3] — 2026-07-07

AksharaMD v0.3.3 adds math OCR for PDF equations, a first-run onboarding panel, a `doctor` command, and an XML parser overhaul validated by three LLM judges.

### Added

- **Math OCR (`[math]` extra)**: LaTeX equations in image-based PDFs are now extracted via `pix2tex`. Install with `pip install "aksharamd[math]"`.
- **`aksharamd doctor`**: diagnoses the local install — checks for optional extras (Tesseract, marker, pix2tex, Whisper, LibreOffice, Pandoc) and reports their status with install hints.
- **First-run onboarding panel**: on the first `aksharamd compile` invocation, a table of optional extras is shown so users know what's available before hitting a `MISSING_EXTRA` warning.
- **Live compile progress view**: multi-file compilations now show a real-time progress bar with per-file status.
- **XML attribute extraction**: leaf elements with only attributes (e.g. `<metric value="25" unit="pct"/>`) are now emitted as paragraph blocks instead of being silently dropped.

### Improved

- **XML parser overhaul**: tag-name prefix on leaf values prevents the page-number cleaner from stripping short numeric strings (ports, counts, dates). Container elements emit heading blocks for reverse-lookup context. Short-value threshold lowered to capture all non-empty text.
- **LLM QA benchmark**: validated with three judges — Claude Haiku 4.5 (9.3), Gemini 2.5 Flash (9.2), GPT-4o mini (9.3) — vs MarkItDown 8.7/8.6/8.7.

### Fixed

- XML structural containers (`<section>`, `<chapter>`) no longer emit spurious headings.
- `math_page_nums` set comprehension now guards against `None` page numbers.

## [0.3.2] — 2026-07-05

AksharaMD v0.3.2 is a parser-polish and input-support patch.

### Added

- **DOCX page-break tracking**: blocks after explicit page breaks now carry accurate page numbers,
  improving chunk provenance for RAG retrieval.
- **Markdown admonition support**: GitHub-style `> [!NOTE]`, `> [!WARNING]`, `> [!TIP]`,
  `> [!IMPORTANT]`, `> [!CAUTION]` and MkDocs `!!! type` callout blocks are parsed as
  `ADMONITION` blocks with typed metadata instead of generic blockquotes.
- **HTML admonition support**: blockquotes with common CSS classes (`note`, `warning`, `tip`,
  `important`, `caution`, `danger`) and GitHub-style `[!TYPE]` first-paragraph patterns are
  detected and emitted as typed `ADMONITION` blocks.
- **S3 input support**: documents can now be compiled directly from `s3://bucket/key` URIs.
  Requires `pip install aksharamd[cloud]`. Credentials follow the standard boto3 chain
  (env vars, `~/.aws/credentials`, IAM role).
- **OneNote detection**: `.one` files now produce a clear `LIBREOFFICE_REQUIRED` error with
  install instructions instead of a generic parse failure.

### Improved

- **PDF invisible-text filtering**: `_extract_raw_page()` now uses PyMuPDF's `rawdict` mode
  and filters characters with PDF text rendering mode `Tr=3` (no fill, no stroke — visually
  invisible but present in the byte stream). Prevents ghost text from polluting chunks and
  inflating token counts on PDFs that embed invisible overlay layers.
- **README positioning**: opening section and "Why AksharaMD" now lead with the AI Readiness
  Score / trust-before-embedding story. Token-efficiency data and benchmark numbers are
  preserved unchanged. LangChain and LlamaIndex examples updated with readiness-score gating
  patterns.

### Tests

- 18 new regression tests covering DOCX page tracking, Markdown admonitions, HTML admonitions,
  PDF invisible-text filtering, and S3 input.
- Full suite: **542 passed, 1 skipped**.

## [0.3.1] — 2026-07-05

### Added
- **Optional Pandoc parser backend**: AksharaMD now supports niche markup formats — AsciiDoc,
  Org-mode, Textile, MediaWiki, OPML, DocBook, man, and roff — by delegating to the system
  `pandoc` binary when available on PATH. Pandoc is not required for the base install and is
  not used for core formats (PDF, DOCX, HTML, Markdown, CSV, JSON, XML, EPUB, RST, ODT).
  Missing Pandoc produces a clear `PANDOC_UNAVAILABLE` user-facing error with install
  instructions. 17 mocked unit tests added; integration test skips gracefully without Pandoc.

## [0.3.0] — 2026-07-05

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
