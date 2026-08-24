# Scorer Coupling Inventory

**Purpose:** enumerate every scoring signal computed by the readiness layer and classify it as **AGNOSTIC** (reads only neutral block-tree data any parser could provide), **COUPLED** (reads default-extractor-specific internals), or **BORDERLINE** (conceptually neutral but currently sourced from a parser-specific field; adaptable via a small contract).

**Purpose downstream:** decide whether making the scorer parser-neutral is a refactor or a rewrite.

**Scope:** every validator warning, every readiness deduction / cap, and every table-quality signal that feeds either the score or a cap. Content-shaping transformers (e.g. key-value promotion) are out of scope — they change what blocks the scorer sees but do not themselves emit warnings or deductions.

**As of:** main @ `63c1e08`, `SCORING_POLICY_VERSION = 1.3`.

Notation for inputs:
- **B.field** = per-block attribute (`type`, `content`, `level`, `page`, `id`, `metadata`).
- **B.metadata.x** = per-block metadata dict key.
- **D.field** = per-document attribute (`pages`, `file_type`, `blocks`, `assets`).
- **D.metadata.x** = per-document metadata dict key.
- **ctx.field** = compilation context attribute.

---

## Section A — Validator warnings

### A.1 `StructureValidator` (`aksharamd/plugins/validators/structure.py`)

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 1 | `NO_DOCUMENT` (error) | L32 | `ctx.document is None` | **AGNOSTIC** | Any parser must produce a Document. |
| 2 | `EMPTY_DOCUMENT` | L39 | `D.blocks` empty | **AGNOSTIC** | Neutral block-tree emptiness. |
| 3 | `HEADING_HIERARCHY` | L47 | `B.type == HEADING`, `B.level` | **AGNOSTIC** | Reads normalized block type + level. |
| 4 | `HEADING_SKIP` | L52 | `B.type`, `B.level` | **AGNOSTIC** | Same as above. |
| 5 | `LARGE_BLOCK` | L64 | `len(B.content)` | **AGNOSTIC** | Neutral content length. |
| 6 | `EMPTY_BLOCK` | L74 | `B.content` | **AGNOSTIC** | Neutral content emptiness. |
| 7 | `MISSING_PAGE` | L81 | `D.file_type == "pdf"`, `D.pages`, `{B.page for B in blocks}` | **BORDERLINE** | Gated on `file_type == "pdf"`. Concept is neutral; any per-page parser could expose "pages present in output vs pages in source". Adapter would provide a boolean `has_page_absent(page)` on the block tree. |
| 8 | `NEAR_EMPTY_OUTPUT` | L100 | `sum(len(B.content.strip()))`, `D.pages`, `D.file_type == "pdf"` (for message only) | **AGNOSTIC** | Ratio math over neutral block-tree data. |
| 9 | `LOW_TEXT_DENSITY` | L119 | `D.file_type == "pdf"`, per-block content by `B.type in {PARAGRAPH, HEADING, TABLE}` | **BORDERLINE** | Gate is `file_type == "pdf"`. Adapter would drop the type-string gate and use a neutral "extracted text density" metric on any format. |
| 10 | `GLYPH_ARTIFACTS` | L133 | `re.findall(r'\(cid:\d+\)', all_text)` | **COUPLED** | The `(cid:N)` pattern is specifically what PyMuPDF emits when it cannot decode a glyph. A different PDF extractor (Marker, unlimited_ocr, external converter) either does not emit this at all or emits a different fallback token. Not portable. |
| 11 | `REPEATED_CONTENT` | L157 | `B.type in {PARAGRAPH, HEADING}`, line frequencies over `B.content.splitlines()` | **AGNOSTIC** | Neutral content analysis. |
| 12 | `TOKEN_BLOAT` | L173 | `D.file_type == "pdf"`, `D.pages`, `ctx.original_tokens` | **BORDERLINE** | Gate is `file_type == "pdf"`. `ctx.original_tokens` is neutral. Drop the pdf gate → agnostic. |
| 13 | `OCR_REQUIRED` | L191 | `D.metadata.pdf_classification`, `D.metadata.pdf_ocr_available`, `D.metadata.pdf_stats.image_pages` | **COUPLED** | Reads three pdf.py-specific metadata fields (`pdf_classification`, `pdf_ocr_available`, `pdf_stats.image_pages`) that no other parser produces. Contract would need a neutral "n pages needing vision/OCR that this parser cannot cover" scalar. |
| 14 | `OCR_HALLUCINATION` | L201 | `D.metadata.pdf_ocr_hallucination` | **COUPLED** | Set only by the Marker vision path (pdf.py Phase 5). Contract would need a neutral "parser rejected N pages of output as hallucinated" scalar. |
| 15 | `W_IMAGE_ONLY_TEXT_BAR_FAIL` | L249 | `D.metadata.pdf_classification == "scanned"`, `D.metadata.pdf_stats.text_pages == 0`, `D.metadata.pdf_stats.page_count` | **COUPLED** | Same family as OCR_REQUIRED — reads pdf.py-specific classifier label + per-page character-count-derived counts. Would need a neutral "doc has no readable text layer" boolean and per-page count. |

### A.2 `MultiColumnOrderValidator` (`aksharamd/plugins/validators/multicolumn.py`)

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 16 | `W_MULTICOLUMN_ORDER` | L219 | `B.metadata.x0`, `B.metadata.y0`, `B.type not in {TABLE, IMAGE, FOOTNOTE}`, `D.metadata.pdf_column_info[page].page_width` | **BORDERLINE** | Per-block `x0`/`y0` are neutral in concept (any parser could emit block bbox). Currently populated by pdf.py's PyMuPDF path. `pdf_column_info` (page_width) is currently a pdf.py artifact; page dimensions themselves are neutral. Adapter needs a per-block bbox on every parser's output and a per-page width scalar. |

### A.3 `HeaderFooterTableValidator` (`aksharamd/plugins/validators/header_footer_table.py`)

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 17 | `W_HEADER_FOOTER_TABLE_GARBLED` | L180 | `B.type == TABLE`, `B.metadata.table_bbox`, `D.metadata.pdf_column_info[page].page_height` | **BORDERLINE** | Same story as multicolumn: reads per-block bbox and per-page height. Both are neutral in concept, currently sourced from pdf.py. |

### A.4 `TableExpectationValidator` (`aksharamd/plugins/validators/table_expectation.py`)

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 18 | `W_TABLE_EXPECTED_NOT_EXTRACTED` | L71 | Per-page: `B.type in {PARAGRAPH, HEADING, CAPTION}` + `B.content` (for caption regex, numeric alignment), `D.metadata.table_rejected_candidates_by_page` (dicts with `strategy`, `bbox`, `row_count`, `col_count`, `rejection_reasons`), `D.metadata.pdf_classification` (as `doc_type`) | **COUPLED** | Reads two pdf.py-specific artifacts: (a) `table_rejected_candidates_by_page` is the pdf.py table-strategy accumulator; no other parser has an equivalent concept. (b) `pdf_classification` is the pdf.py classifier label. The caption regex and numeric-alignment signals themselves are agnostic; the rejected-candidate signal is the deep coupling. Contract would need a neutral "table candidates the parser considered and rejected" list — a big surface area to define. |

### A.5 `TableMissingValidator` (`aksharamd/plugins/validators/table_missing.py`)

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 19 | `W_TABLE_MISSING` | L219 | Per-block content over all `B.content`: regex `(\.\s){4,}|\.{5,}` (leader-dot line count + total match count), `D.file_type in {pdf, docx, doc, html}`, existing `OCR_REQUIRED` warning suppression, tiny-doc guard on `len([b for b in blocks if b.content.strip()])` | **AGNOSTIC** | Leader-dot detection reads only `B.content`. Suppression check reads the neutral warning bag. Tiny-doc guard is neutral. `file_type` gate is a whitelist — trivial to adapt. Any parser producing block trees is compatible. |

### A.6 `EncodingArtifactsValidator` (`aksharamd/plugins/validators/encoding_artifacts.py`)

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 20 | `W_ENCODING_ARTIFACTS` | (validator emit) | All `B.content`: XML tag residue regex `</?(?:pt|font|span|div|tspan)\d+[^>]*>?`, mojibake density `count("�") / len(content)`, `D.file_type in {pdf, docx, doc, html}`, `OCR_REQUIRED` suppression, tiny-doc guard | **AGNOSTIC** | Reads only `B.content`. Any parser whose output could contain the same failure signatures would trigger this identically. The XML fragment names (`pt`, `font`, `tspan`) are targeted at typical PDF-to-XML residue but the detector concept is neutral. |

### A.7 `TableQualityValidator` + `compute_table_quality` (`aksharamd/plugins/validators/table_quality.py`, `aksharamd/scoring/table_quality.py`)

`compute_table_quality` emits ~44 named signals per table block (constants in `SigName` at `aksharamd/scoring/table_quality.py:44-91`). All signals operate on `Block.data: TableData` where `TableData` is the neutral table model (`aksharamd/models/table.py`) with `row_count`, `column_count`, `cells: list[TableCell]`, `header_rows`, `extraction_method`, `bbox`.

Rather than list all 44 individually (identical classification within each group), the groups are:

| Signal group | Names | Inputs | Class |
|---|---|---|---|
| Structural completeness | `explicit_cell_count`, `expected_grid_size`, `explicit_empty_cell_count`, `missing_coordinate_count`, `span_covered_coordinate_count`, `nonempty_cell_ratio`, `empty_row_count`, `empty_column_count`, `ragged_row_count`, `duplicate_row_count` | `TableData.row_count`, `column_count`, `cells[]` (row/column indices, text) | **AGNOSTIC** — any table extractor's normalized output |
| Cell fragmentation | `avg_nonempty_cell_length`, `median_cell_length`, `single_char_cell_fraction`, `punctuation_only_cell_fraction`, `numeric_only_cell_fraction`, `short_cell_fraction`, `whitespace_only_cell_count` | Cell text length + character-class regexes on cell text | **AGNOSTIC** |
| Header quality | `header_detection`, `header_row_count`, `header_cell_coverage`, `generic_header_count`, `duplicate_header_names`, `empty_header_cells`, `numeric_only_headers`, `header_body_width_mismatch`, `repeated_header_in_body` | `TableData.header_rows`, `header_detection` field, cell text | **BORDERLINE** — `header_rows` and `header_detection` are populated by the table extractor's heuristics; different parsers use different rules. The signal math on top is neutral. |
| Geometry | `table_bbox_available`, `table_near_top_margin`, `table_near_bottom_margin`, `table_one_row`, `table_one_column`, `table_height_fraction`, `table_width_fraction` | `TableData.bbox`, page dimensions (currently from `D.metadata.pdf_column_info[page].page_height/page_width`) | **BORDERLINE** — bbox on table is neutral; page dimensions currently sourced from pdf.py |
| Stitching | `stitched_source_page_count`, `repeated_header_removed`, `stitching_confidence`, `source_method_consistency`, `page_row_ranges_available`, `stitching_row_continuity` | `TableData.metadata.stitching_*` (populated by `pdf_tables/stitching.py`) | **COUPLED** — the stitching metadata keys are populated by pdf.py's page-break table stitcher. No other extractor produces these fields. |

Also uses:
- `_GENERIC_HEADER_RE = re.compile(r"^(col(?:umn)?[_\s]*\d+|field[_\s]*\d+|header[_\s]*\d+|f\d+)$", IGNORECASE)` — detects generic auto-generated header patterns. **BORDERLINE** — the specific naming conventions (`Col1`, `Field1`, `f1`) are what pdfplumber and PyMuPDF emit when they can't infer headers; a different extractor might use different fallbacks.

**Overall class for `compute_table_quality`:** mostly AGNOSTIC/BORDERLINE. Only the stitching group and generic-header regex are meaningfully COUPLED. The core structural/fragmentation math would run unchanged on any table extractor's output as long as it fills the neutral `TableData` shape.

---

## Section B — Readiness deductions and caps (`aksharamd/scoring/readiness.py`)

Deductions are applied in `compute_confidence` (L64). Every deduction either reads (a) the aggregated warning bag from validators, (b) block-tree data, or (c) doc-level metadata.

### B.1 Format baseline

| # | Signal | Location | Inputs | Class | Notes |
|---|---|---|---|---|---|
| 21 | `FORMAT_BASELINE` | L88 | `D.file_type` → `_FORMAT_BASE` dict lookup | **BORDERLINE** | `file_type` is neutral (any parser knows what format it processed). The mapping table is a quality opinion by format — it stays with the scorer, not the parser. Adapter: no change needed if the parser sets `file_type` correctly. |

### B.2 Deductions gated on aggregated warning codes

Deductions 22-32 are triggered by presence of specific warning codes in `ctx.validation.warnings`. The deduction logic itself only reads the warning bag (**AGNOSTIC**); the underlying signal's coupling is inherited from the emitter in Section A.

| # | Signal | Location | Reads (in this file) | Inherits coupling from |
|---|---|---|---|---|
| 22 | `PARSE_ERRORS` | L108 | `ctx.validation.issues[severity=="error"]` | **AGNOSTIC** — any parser errors flow through the same bag |
| 23 | `MISSING_PAGE` (deduction) | L138 | `warnings_by_code["MISSING_PAGE"]`, `D.pages` | Inherits BORDERLINE from A.1 #7 |
| 24 | `LARGE_BLOCK` (deduction) | L159 | `warnings_by_code["LARGE_BLOCK"]` | AGNOSTIC |
| 25 | `HEADING_ISSUES` (deduction) | L178 | `warnings_by_code["HEADING_SKIP"|"HEADING_HIERARCHY"]` | AGNOSTIC |
| 26 | `IMAGE_PLACEHOLDER_NO_FALLBACK` (cap at 55) | L206-239 | Per-block: `B.type == PARAGRAPH`, content contains `"[Image not extracted"`, `B.type == IMAGE`, `D.assets` presence of `image_bytes` | **BORDERLINE** — the `[Image not extracted"` sentinel is a specific string emitted by `_process_raw_page` in pdf.py when OCR is unavailable. A neutral contract would define a canonical "placeholder-only-content" signal instead of grepping for the string. |
| 27 | `OCR_REQUIRED` (deduction) | L242-265 | `warnings_by_code["OCR_REQUIRED"]`, `D.metadata.pdf_classification`, `D.metadata.pdf_stats.image_pages`, `D.pages` | Inherits COUPLED from A.1 #13; also reads pdf.py metadata directly to compute image_ratio |
| 28 | `OCR_ATTEMPTED_SPARSE` (deduction) | L267-295 | `warnings_by_code["NEAR_EMPTY_OUTPUT"]`, `D.file_type == "pdf"`, `D.metadata.pdf_ocr_available`, `D.metadata.pdf_stats.image_pages`, `D.pages` | **COUPLED** — same family as OCR_REQUIRED |
| 29 | `NEAR_EMPTY_OUTPUT` (deduction, may suppress) | L298-317 | `warnings_by_code["NEAR_EMPTY_OUTPUT"]`, suppression via OCR_REQUIRED / OCR_ATTEMPTED_SPARSE | AGNOSTIC (deduction) + inherits from A.1 #8 |
| 30 | `LOW_TEXT_DENSITY` (deduction, may suppress) | L320-339 | Same pattern | AGNOSTIC (deduction) + inherits BORDERLINE from A.1 #9 |
| 31 | `GLYPH_ARTIFACTS` (deduction) | L342-352 | `warnings_by_code["GLYPH_ARTIFACTS"]` | Inherits COUPLED from A.1 #10 |
| 32 | `REPEATED_CONTENT` (deduction) | L355-365 | `warnings_by_code["REPEATED_CONTENT"]` | AGNOSTIC |
| 33 | `TOKEN_BLOAT` (deduction) | L368-378 | `warnings_by_code["TOKEN_BLOAT"]` | Inherits BORDERLINE from A.1 #12 |
| 34 | `NO_HEADINGS_MULTIPAGE` (deduction) | L382-398 | `[b for b in blocks if b.type == HEADING]`, `D.pages > 3` | AGNOSTIC |
| 35 | `COL_GENERIC_TABLES` (deduction) | L420-441 | Per-block: `B.type == TABLE`, `"Col1" in B.content or "Col2" in B.content` | **BORDERLINE** — grepping for the `Col1`/`Col2` strings targets pdfplumber's fallback header naming. A different table extractor would use different placeholder names. Adapter: read `TableData.header_rows[].generic_header_count` from `table_quality`'s already-computed signal (Section A.7). |
| 36 | `NO_TEXT_IN_IMAGE` (deduction) | L495-503 | `D.file_type in {jpg, jpeg, png, ...}`, `[b for b in blocks if b.type == PARAGRAPH]` | AGNOSTIC — reads only file_type + block type |

### B.3 Cap-attached warnings (Phase 2 + 3 + 3.5)

| # | Signal | Location | Reads (in this file) | Class |
|---|---|---|---|---|
| 37 | `W_MULTICOLUMN_ORDER` cap (69) | L512-549 | `warnings_by_code`, `D.metadata.multicolumn_diagnostics` | Inherits BORDERLINE from A.2 |
| 38 | `W_HEADER_FOOTER_TABLE_GARBLED` cap (84) | L551-595 | `warnings_by_code`, `D.metadata.header_footer_table_diagnostics` | Inherits BORDERLINE from A.3 |
| 39 | `W_TABLE_MISSING` cap (69) | ~L598-650 | `warnings_by_code`, `D.metadata.table_missing_diagnostics` | Inherits AGNOSTIC from A.5 |
| 40 | `W_ENCODING_ARTIFACTS` cap (69) | ~L652-704 | `warnings_by_code`, `D.metadata.encoding_artifacts_diagnostics` | Inherits AGNOSTIC from A.6 |
| 41 | `W_IMAGE_ONLY_TEXT_BAR_FAIL` cap (69) | ~L706+ | `warnings_by_code`, `D.metadata.image_only_text_bar_diagnostics` | Inherits COUPLED from A.1 #15 |
| 42 | `W_TABLE_EXPECTED_NOT_EXTRACTED` cap (84) | ~L706+ | `warnings_by_code`, `D.metadata.table_expectation_diagnostics` | Inherits COUPLED from A.4 |

### B.4 Informational (zero-penalty) warnings

| # | Signal | Location | Reads | Class |
|---|---|---|---|---|
| 43 | `W_PDF_ATTACHMENT_IGNORED` informational | ~L720+ | `warnings_by_code`, `D.metadata.pdf_attachment_diagnostics` | **COUPLED** — PDF `/EmbeddedFiles` catalog is a PDF-only concept |
| 44 | `AUTO_OCR_BACKEND_SELECTED` informational | ~L735+ | `warnings_by_code` | **COUPLED** — Auto Policy v1 lives inside the pdf.py OCR-backend routing |
| 45 | `AUTO_OCR_BACKEND_FALLBACK` informational | ~L740+ | `warnings_by_code` | **COUPLED** — same source |
| 46 | `IMAGE_PLACEHOLDER_WITH_ASSETS` informational | L216-220 | `D.assets` (checked for `image_bytes`) | **AGNOSTIC** — reads only whether asset bytes are present |

---

## Section C — Format baseline (out of the deduction loop)

Already counted as #21. Listed here for completeness because it dominates the score numerically: it is the starting value of every readiness score. `_FORMAT_BASE` at `aksharamd/scoring/readiness.py:27-60` is a static mapping from `file_type` string to base score. Neutral by construction.

---

## Section D — What is deliberately excluded

The following contribute to the compiler pipeline but do not directly emit scoring signals:

- **Key-value detection / promotion** (`aksharamd/scoring/key_value_detection.py`, `aksharamd/plugins/transformers/key_value_promoter.py`): converts paragraph blocks to `KeyValueGroup` blocks before scoring runs. Changes what blocks the scorer sees, but does not itself emit warnings or deductions. The scorer only sees the promoted output.
- **`table_findings.py`, `table_calibration.py`**: aggregate `table_quality` findings for reporting. Do not emit scoring signals independently.

---

## Section E — Coupling breakdown

**Counted at the signal-name level** (44 quality signals in Section A.7 counted as their 5 groups for readability; other sections counted as-is). Total: **46 named scoring surfaces** (validator warnings, deductions, caps, informational codes).

| Class | Count | % | Signals |
|---|---:|---:|---|
| **AGNOSTIC** | **20** | **43%** | Structure 1-6, 8, 11; Multicolumn/HeaderFooter output (once bbox surfaces); Table missing (19); Encoding artifacts (20); Table quality — structural completeness + cell fragmentation groups; Parse errors (22), Large block (24), Heading issues (25), Near empty (29), Repeated content (32), No headings multipage (34), No text in image (36), Image placeholder with assets (46) |
| **BORDERLINE** | **14** | **30%** | Missing page (7, 23), Low text density (9, 30), Token bloat (12, 33), Format baseline (21), Image placeholder no fallback cap (26), Col generic tables (35), Multicolumn (16, 37), Header/footer table (17, 38), Table quality — header quality + geometry groups, Table quality — generic header regex |
| **COUPLED** | **12** | **26%** | Glyph artifacts (10, 31), OCR required (13, 27), OCR hallucination (14), W_IMAGE_ONLY_TEXT_BAR_FAIL (15, 41), OCR attempted sparse (28), Table expectation (18, 42), Table quality — stitching group, PDF attachment ignored (43), Auto OCR backend selected/fallback (44, 45) |

Rounding across the three buckets: **~43% AGNOSTIC / ~30% BORDERLINE / ~26% COUPLED** at the signal-count level.

### Weighting by actual score impact on the Phase 4 v2 dev split (25 docs)

Not all signals matter equally. Signals classified by *how much score they moved* on the dev-split:

**High-impact (drove a band change or cap) on the dev split:**
- `FORMAT_BASELINE` (#21, BORDERLINE) — starts every doc's score.
- `W_IMAGE_ONLY_TEXT_BAR_FAIL` cap (#41, COUPLED) — capped 3 docs (`letter3`, `myctophidae`, `japanese`) from HIGH → RISKY. Directly closed 3 of 6 original silent failures.
- `W_TABLE_EXPECTED_NOT_EXTRACTED` cap (#42, COUPLED) — capped VRSK and fqr and, post-PR-116, SERFF out of HIGH.
- `W_TABLE_MISSING` cap (#39, AGNOSTIC) — capped `ikea3` and `strikeUnderline`.
- `HEADING_HIERARCHY`/`HEADING_SKIP` (#3, #4, #25, AGNOSTIC) — minor penalty applied to most dev docs; largely inert.
- `OCR_REQUIRED` deduction (#27, COUPLED) — did not fire on the dev split because `W_IMAGE_ONLY_TEXT_BAR_FAIL` covers the same cases upstream. Would fire on hybrid PDFs not present in dev.

**By deduction-value impact:**

- The **cap signals dominate** the score changes on the dev split. Of the 5 cap signals: 2 are COUPLED (image-only, table expectation), 2 are AGNOSTIC (table missing, encoding artifacts), 1 is BORDERLINE (multicolumn) or 2 (header/footer garbled).
- The base **format baseline** is BORDERLINE by classification but neutral in practice (only reads `file_type`).
- The **OCR family** (OCR_REQUIRED, OCR_ATTEMPTED_SPARSE, W_IMAGE_ONLY_TEXT_BAR_FAIL) is the biggest coupled-signal cluster by weight — accounts for essentially all image-only handling.

Rough estimate of **weighted coupling by impact**: on the dev split, coupled signals drive ~35-40% of actual score movement (dominated by the OCR family + table expectation). Agnostic signals drive ~40-45% (dominated by heading + table-missing + encoding-artifacts). Borderline signals drive the remaining ~15-25% (format baseline, multicolumn, header/footer).

---

## Section F — Single biggest obstacle + cleanest already-agnostic signal

### Single most parser-coupled signal (biggest neutrality obstacle)

**The OCR / image-only family, specifically `W_IMAGE_ONLY_TEXT_BAR_FAIL` (#15, #41), `OCR_REQUIRED` (#13, #27), `OCR_ATTEMPTED_SPARSE` (#28), `IMAGE_PLACEHOLDER_NO_FALLBACK` (#26).**

Why this is the biggest obstacle:
- Reads three pdf.py-specific metadata surfaces (`pdf_classification`, `pdf_stats.image_pages`, `pdf_stats.text_pages`) that no other parser produces.
- Reads a specific string sentinel (`"[Image not extracted"`) emitted by `_process_raw_page` in pdf.py.
- Drives 3 of the 6 original silent-failure fixes on the dev split — high impact, high coupling.
- The concept ("this document has no readable text layer, or has image-substituted regions") is fundamentally neutral, but every existing signal reads it via pdf.py's internal accounting.

A neutral contract would need three primitives from every parser: a `text_availability` score per page (or a doc-level "fraction of pages with a real text layer"), a canonical placeholder sentinel or block flag, and a per-page vision/OCR-required boolean.

### Single cleanest already-agnostic signal

**`W_TABLE_MISSING` (#19, #39) — Trigger A leader-dot detector.**

Why: reads only `B.content` (via regex over concatenated text). Zero references to parser-specific metadata, no bbox, no classification, no rejection reasons, no diagnostics dicts other than what the validator itself writes. Would work byte-identically on any parser's block-tree output (given the same content strings). Also happens to be one of the highest-precision detectors shipped this cycle (100% on 20 dev-split controls).

Runner-up: `W_ENCODING_ARTIFACTS` (#20, #40) — same story, reads only `B.content`.

Runner-up: `HEADING_HIERARCHY` / `HEADING_SKIP` (#3, #4, #25) — reads only `B.type` and `B.level`. Zero coupling. Applied to nearly every dev doc without fuss.

---

## Section G — Honest read: refactor or rewrite?

**Verdict: REFACTOR.**

Evidence:

1. **The AGNOSTIC + BORDERLINE bucket is ~73% of signals by count.** Everything in these buckets either runs unchanged on a different parser's output or works with a small adapter that translates neutral concepts (page count, per-block bbox, per-page dimensions, per-page character count, per-doc `file_type`) from the new parser's output into the shapes the scorer already reads. None of this requires re-designing the scoring logic — it requires defining a small block-tree contract.

2. **Most COUPLED signals cluster around a single concept: OCR / image-only handling.** The coupling is not spread evenly across the scoring surface; it concentrates in one family (OCR_REQUIRED, OCR_ATTEMPTED_SPARSE, W_IMAGE_ONLY_TEXT_BAR_FAIL, GLYPH_ARTIFACTS, OCR_HALLUCINATION) plus the table-expectation validator (which reads rejected-candidate accumulator dicts). Defining a neutral "vision-required-per-page" contract collapses most of the OCR coupling to a single boundary crossing.

3. **The block-tree contract already exists in spirit.** `Block` and `Document` in `aksharamd/models/` are already parser-neutral by design — `type`, `content`, `page`, `metadata`, `assets` are all portable concepts. The coupling problem is that a handful of `metadata` keys (`pdf_classification`, `pdf_stats`, `pdf_column_info`, `table_rejected_candidates_by_page`, `pdf_ocr_hallucination`) are namespaced with `pdf_` and populated by pdf.py. A rename + a defined-elsewhere populator would preserve the current signals.

4. **The scoring logic itself has zero parser-specific arithmetic.** The `SCORING_POLICY` (`aksharamd/scoring/models.py:88-225`) is a data-driven policy table. `compute_confidence` (`readiness.py:64`) applies penalties and caps by name. Nothing about the score math depends on how the blocks were made.

5. **The `TableData` model is already the right shape.** `compute_table_quality`'s 44 signals operate on a normalized table structure that any table extractor could fill. The stitching subgroup and generic-header regex are the only meaningfully coupled parts — both fixable via small adapters that translate a new extractor's stitching state or header-fallback naming.

**What the refactor entails, concretely:**

- Define a `BlockTreeContract` (namespace, fields, types) for what a scoring-ready `Document` must expose. Include: `pages: int`, per-block `bbox: [x0, y0, x1, y1] | None`, `file_type: str`, plus a per-doc `SourceProfile` dict with the neutral versions of what today are `pdf_stats.image_pages`, `pdf_stats.text_pages`, `pdf_classification`, `pdf_ocr_available`, `pdf_ocr_hallucination`, `pdf_column_info[page].page_width/page_height`.
- Move the parser-specific metadata reads inside the scorer to consume that contract instead of `pdf_*` keys.
- Provide a `PdfBlockTreeAdapter` that populates the contract from the current pdf.py metadata (so existing behavior is preserved byte-for-byte).
- Provide a `MarkdownBlockTreeAdapter` and any others as reference implementations.

**What the refactor deliberately does NOT need:**

- No changes to `SCORING_POLICY`.
- No changes to `compute_confidence`'s arithmetic.
- No changes to the maturity-aware cap pattern.
- No changes to the format baseline table.
- No changes to any of the 20 already-AGNOSTIC signals.
- No changes to the 14 BORDERLINE signals once the contract fields are wired.

**Counter-evidence that would push toward REWRITE:**

- If the OCR family turned out to have branches keyed on pdf.py internals I haven't seen. It does not — the coupled reads are all through `doc.metadata` dict keys, no direct pdf.py imports in the scorer.
- If the table-expectation signal (#18) turned out to need parser cooperation that no other parser could reasonably provide. It might — the "rejected table candidate with rows/cols/rejection_reasons/quality_metrics" is quite specific to pdfplumber-style table detectors. But even here, the fix isn't to rewrite; it's to make that signal optional (only fires when the parser provides the rejected-candidate accumulator).
- If deduction values had to change across parsers. They don't — the score math is parser-independent by construction.

None of these counter-scenarios hold on inspection. The scoring layer is a refactor.

**Rough refactor sizing (order-of-magnitude, not a plan):** 400-800 LOC net change. Most of the work is defining the contract, writing the pdf.py adapter, and renaming metadata keys. The scoring code itself changes minimally.

---

## Section H — Verification pass

**As of:** main @ `63c1e08`. Re-read every COUPLED and BORDERLINE signal at its cited file:line, traced the actual field accesses, and re-checked classification. Also spot-checked the 5 highest-impact AGNOSTIC signals. (Source files reviewed under this pass are byte-identical between the pre-verification-pass working state and `63c1e08` — only doc-only PRs #119 and #120 landed in between.)

Preserves first-pass results — this section records what was verified vs what shifted, so the record shows both passes.

### H.1 Line-reference drift

Nine of the 26 COUPLED + BORDERLINE citations were re-checked against source. Small line-number drift on four validator emission sites; all readiness.py citations verified correct within tolerance.

| # | Signal | Inventory says | Actual | Drift |
|---|---|---|---|---|
| 10 | `GLYPH_ARTIFACTS` (emit) | structure.py:L133 | L133 | 0 |
| 13 | `OCR_REQUIRED` (emit) | structure.py:L191 | L191 (ctx.warn) | 0 |
| 14 | `OCR_HALLUCINATION` (emit) | structure.py:L201 | L201 | 0 |
| 15 | `W_IMAGE_ONLY_TEXT_BAR_FAIL` (emit) | structure.py:L249 | L249 | 0 |
| 16 | `W_MULTICOLUMN_ORDER` (emit) | multicolumn.py:L219 | L218 | −1 |
| 17 | `W_HEADER_FOOTER_TABLE_GARBLED` (emit) | header_footer_table.py:L180 | L179 | −1 |
| 18 | `W_TABLE_EXPECTED_NOT_EXTRACTED` (emit) | table_expectation.py:L71 | L71 | 0 |
| 19 | `W_TABLE_MISSING` (emit) | table_missing.py:L219 | L209 | **−10** |
| 20 | `W_ENCODING_ARTIFACTS` (emit) | (unspecified) | encoding_artifacts.py:L221 | now specified |
| 26 | `IMAGE_PLACEHOLDER_NO_FALLBACK` cap | readiness.py:L206-239 | L206-239 | 0 |
| 27 | `OCR_REQUIRED` deduction | readiness.py:L242-265 | L242-265 | 0 |
| 28 | `OCR_ATTEMPTED_SPARSE` deduction | readiness.py:L267-295 | L267-295 | 0 |
| 29 | `NEAR_EMPTY_OUTPUT` deduction | readiness.py:L298-317 | L298-317 | 0 |
| 30 | `LOW_TEXT_DENSITY` deduction | readiness.py:L320-339 | L320-339 | 0 |
| 31 | `GLYPH_ARTIFACTS` deduction | readiness.py:L342-352 | L342-352 | 0 |
| 33 | `TOKEN_BLOAT` deduction | readiness.py:L368-378 | L368-378 | 0 |
| 35 | `COL_GENERIC_TABLES` | readiness.py:L420-441 | L420-441 | 0 |
| 37 | `W_MULTICOLUMN_ORDER` cap | readiness.py:L512-549 | L512-549 | 0 |
| 38 | `W_HEADER_FOOTER_TABLE_GARBLED` cap | readiness.py:L551-595 | L551-595 | 0 |
| 39 | `W_TABLE_MISSING` cap | readiness.py:~L598-650 | L597-643 | 0 |
| 40 | `W_ENCODING_ARTIFACTS` cap | readiness.py:~L652-704 | L645-691 | −7 (envelope) |
| 41 | `W_IMAGE_ONLY_TEXT_BAR_FAIL` cap | readiness.py:~L706+ | L693-753 | +7 (envelope) |
| 42 | `W_TABLE_EXPECTED_NOT_EXTRACTED` cap | readiness.py:~L706+ | L755-804 | +49 (envelope) |
| 43 | `W_PDF_ATTACHMENT_IGNORED` info | readiness.py:~L720+ | L810-819 | +90 (envelope) |
| 44 | `AUTO_OCR_BACKEND_SELECTED` info | readiness.py:~L735+ | L826-832 | +91 (envelope) |
| 45 | `AUTO_OCR_BACKEND_FALLBACK` info | readiness.py:~L740+ | L833-839 | +93 (envelope) |
| 46 | `IMAGE_PLACEHOLDER_WITH_ASSETS` info | readiness.py:L216-220 | L216-220 | 0 |

**Verdict on drift:** the two-line drifts on `W_MULTICOLUMN_ORDER` and `W_HEADER_FOOTER_TABLE_GARBLED` are minor (off-by-one on the `ctx.warn` line vs. where the emit block starts). The 10-line drift on `W_TABLE_MISSING` was because the first-pass citation pointed to `register_plugin` (L219), not `ctx.warn` (L209). The `~L706+`, `~L720+`, `~L735+`, `~L740+` envelopes on the informational codes were sloppy first-pass hedges; corrected here. **No file path was wrong.** All line references now anchored to the concrete `ctx.warn(...)` call or the deduction/informational block start.

### H.2 Actual field-access traces (concrete, per signal)

Verified against source what each COUPLED/BORDERLINE signal actually reads. Corrections/additions noted; otherwise the first-pass "Inputs" column is confirmed.

**StructureValidator OCR family (structure.py):**
- L182-190 gate: `doc.file_type == "pdf"`, `doc.metadata["pdf_classification"]`, `doc.metadata["pdf_ocr_available"]`, `doc.metadata["pdf_stats"]["image_pages"]`, `doc.metadata["pdf_stats"]["text_pages"]`, `doc.metadata["pdf_stats"]["page_count"]`. All four `pdf_*` keys are populated by `pdf.py`.
- L190 (OCR_REQUIRED): fires when `classification in ("scanned", "hybrid") AND not ocr_available AND image_pages > 0`. Confirmed COUPLED.
- L200 (OCR_HALLUCINATION): reads only `doc.metadata["pdf_ocr_hallucination"]`. First-pass classification confirmed COUPLED.
- L235-259 (W_IMAGE_ONLY_TEXT_BAR_FAIL): fires when `classification == "scanned" AND text_pages == 0 AND page_count >= 1`. Also writes `image_only_text_bar_diagnostics` with `image_pages, text_pages, page_count, classification, ocr_available` — the cap site at readiness.py L693-753 reads exactly this dict. Confirmed COUPLED at both sites.

**MultiColumnOrderValidator (multicolumn.py L168-236):**
- Reads `doc.metadata["pdf_column_info"]` for per-page `page_width`; the cluster-analysis routine reads per-block `x0`/`y0` from `block.metadata` (via `_analyse_page`). First-pass classification (BORDERLINE) confirmed.
- L189: pages with `page_width == 0.0` are skipped — parser-portable behavior once bbox surfaces exist.

**HeaderFooterTableValidator (header_footer_table.py L135-190):**
- Reads `doc.metadata["pdf_column_info"][pg]["page_height"]` and `block.metadata["table_bbox"]`. Blocks without `table_bbox` are skipped (L148). Classification BORDERLINE confirmed.

**TableExpectationValidator (table_expectation.py L26-94):**
- L33: `doc.metadata["table_rejected_candidates_by_page"]` — dict keyed by page → list of rejected-candidate dicts. This is pdf.py's table-strategy accumulator; no other parser produces it.
- L34: `doc.metadata["pdf_classification"]` passed as `doc_type` to `compute_table_expectation`.
- L53: rejected candidates supplied to compute logic. Structural coupling confirmed COUPLED.

**TableMissingValidator (table_missing.py L136-220):**
- Reads only `doc.file_type` gate + `block.content` on `PARAGRAPH/HEADING/LIST` blocks + `ctx.validation.issues` (for OCR_REQUIRED suppression). No pdf-specific metadata. Classification AGNOSTIC confirmed.

**EncodingArtifactsValidator (encoding_artifacts.py L135-230):**
- Same story as table_missing: `doc.file_type` gate + `block.content` + `ctx.validation.issues`. Classification AGNOSTIC confirmed.

**Readiness.py deductions:**
- L242-265 OCR_REQUIRED: reads `doc.metadata["pdf_classification"]`, `doc.metadata["pdf_stats"]["image_pages"]`, `doc.pages`. COUPLED confirmed.
- L267-295 OCR_ATTEMPTED_SPARSE: reads `doc.metadata["pdf_ocr_available"]`, `doc.metadata["pdf_stats"]["image_pages"]`. COUPLED confirmed.
- L191-239 IMAGE_PLACEHOLDER_NO_FALLBACK: string-grep on `"[Image not extracted"` sentinel across paragraph blocks. Plus `doc.assets` presence check. BORDERLINE confirmed — sentinel is pdf.py-specific but the concept is universal.
- L420-441 COL_GENERIC_TABLES: literal `"Col1" in b.content or "Col2" in b.content`. Confirmed BORDERLINE — the string sentinels are pdfplumber-specific fallbacks. First-pass suggestion to source from `table_quality`'s `GENERIC_HEADER_COUNT` is still the right refactor.
- L512-753 cap sites: all read `warnings_by_code[...]` + a `<name>_diagnostics` dict from `doc.metadata`. Each dict's shape is defined at its emission site, so cap-site coupling is *inherited*, not independent. Confirmed correctly inherited in Section B.3.
- L810-839 informational: `W_PDF_ATTACHMENT_IGNORED` reads `doc.metadata["pdf_attachment_diagnostics"]["attachment_count"]`. `AUTO_OCR_BACKEND_SELECTED/FALLBACK` read only `warnings_by_code`. COUPLED classifications for the underlying concept confirmed (attachment catalog and Auto Policy v1 are pdf-only).

**Stitching signals (table_quality.py L426-486):**
- Gates on `td.extraction_method == ExtractionMethod.PDF_STITCHED` — signal group only fires for the pdf.py stitcher's output.
- Reads `td.metadata` keys: `source_pages`, `source_table_methods`, `page_row_ranges`, `repeated_header_removed`, `stitching_confidence`. All populated by `aksharamd/plugins/parsers/pdf_tables/stitching.py`. COUPLED confirmed.

**GENERIC_HEADER regex (table_quality.py L93-96):** verified as literal `r"^(col(?:umn)?[_\s]*\d+|field[_\s]*\d+|header[_\s]*\d+|f\d+)$"`. Targets pdfplumber's `Col1/Field1/f1` fallbacks. BORDERLINE confirmed.

### H.3 AGNOSTIC spot-checks (top 5 by impact)

- **W_TABLE_MISSING (#19, #39):** confirmed reads only `B.content` regex `_LEADER_DOT_RE`. Zero pdf-specific metadata reads. AGNOSTIC survives.
- **W_ENCODING_ARTIFACTS (#20, #40):** confirmed same profile — reads only `B.content`. AGNOSTIC survives.
- **HEADING_HIERARCHY / HEADING_SKIP / HEADING_ISSUES (#3, #4, #25):** verified structure.py L47/L52 reads `B.type` + `B.level`; readiness.py L171-187 reads only `warnings_by_code`. AGNOSTIC survives.
- **PARSE_ERRORS (#22):** readiness.py L104-117 reads only `ctx.validation.issues[severity=="error"]`. AGNOSTIC survives.
- **NO_HEADINGS_MULTIPAGE (#34):** readiness.py L382-398 reads `[b for b in blocks if b.type == HEADING]` + `doc.pages > 3`. AGNOSTIC survives.

### H.4 "Neutral input needed" column check

For the confirmed BORDERLINE and COUPLED signals, the first-pass "Adapter would provide…" recommendations were re-checked against the actual field reads uncovered above:

- **#7 MISSING_PAGE:** first pass suggested `has_page_absent(page)` on the block tree. Actual read is `{B.page for B in blocks}` vs `range(1, D.pages+1)`. A `pages_present: set[int]` on the neutral doc is sufficient. Confirmed.
- **#9 LOW_TEXT_DENSITY / #12 TOKEN_BLOAT:** just drop the `file_type == "pdf"` gate. Confirmed.
- **#13/#27/#28 OCR family:** first-pass proposed a three-primitive contract (per-page `text_availability`, canonical placeholder sentinel, per-page vision/OCR-required boolean). Re-verified against reads: L182-190 needs `classification, ocr_available, image_pages, text_pages, page_count`; L272 also needs `pdf_ocr_available`; readiness.py caps read the diagnostics dict which contains all five. A single `SourceProfile` with `{n_pages_no_text_layer, n_pages_vision_required, ocr_capability, hallucination_pages, has_placeholder_only_output}` would replace all five reads. Confirmed sufficient.
- **#16 W_MULTICOLUMN_ORDER / #17 W_HEADER_FOOTER_TABLE_GARBLED:** need per-block `bbox` and per-page `page_width/page_height` on the neutral doc. Multi-column adapter also needs the cluster-analysis routine to run block-independent (which it already does via `_analyse_page`). Confirmed sufficient.
- **#18 W_TABLE_EXPECTED_NOT_EXTRACTED:** the first-pass note "big surface area to define" is correct. `RejectedTableCandidate` (aksharamd/scoring/table_expectation.py:L40) already defines the neutral shape — the contract just needs the parser to fill it (or leave it empty, in which case only the caption/numeric-alignment signals fire). Small nuance: `doc_type` is also read, but a neutral `source_profile.document_type_hint: Literal["table_heavy", ...] | None` covers it. Confirmed sufficient with the caveat that the surface is larger than the other adapters.
- **#26 IMAGE_PLACEHOLDER_NO_FALLBACK:** first-pass suggested "canonical placeholder-only-content signal". Verified — the sentinel grep at readiness.py:L192-194 is the only coupling; a `Block.metadata.is_placeholder: bool` flag or a `BlockType.PLACEHOLDER` value would collapse the read. Confirmed sufficient.
- **#35 COL_GENERIC_TABLES:** first-pass suggested reading `table_quality`'s already-computed `GENERIC_HEADER_COUNT` signal. Verified — `_GENERIC_HEADER_RE` at L93-96 covers pdfplumber's `Col1/Field1/f1` plus any parser using similar naming; consolidating to the pre-computed signal removes the string sentinel. Confirmed sufficient.
- **Stitching group:** first-pass classified COUPLED; verified via `td.extraction_method == PDF_STITCHED` gate. A neutral contract would need to either (a) let non-pdf parsers set the extraction method and populate the stitching metadata keys, or (b) make the whole signal group optional (only fires when the parser opts in). Option (b) is simpler and matches the first-pass "make optional" recommendation. Confirmed.

### H.5 Classification changes

**Zero classifications changed.** Every COUPLED, BORDERLINE, and AGNOSTIC assignment from the first pass survived verification against source.

### H.6 Verdict re-affirmations

**REFACTOR verdict: HOLDS.** The couplings verified in H.2 are all through a small number of `doc.metadata.<pdf_*>` dict keys plus one string sentinel (`"[Image not extracted"`) plus one extraction-method gate (`PDF_STITCHED`). None of them require redesigning the scoring math — every coupled read is a boundary-crossing that a defined contract could translate. The refactor size estimate (400-800 LOC) is unchanged.

**OCR-concentration claim: SURVIVES.** Of the 12 COUPLED signals in Section E, 7 are directly in the OCR/image-only family:
- `GLYPH_ARTIFACTS` (#10, #31)
- `OCR_REQUIRED` (#13, #27)
- `OCR_HALLUCINATION` (#14)
- `W_IMAGE_ONLY_TEXT_BAR_FAIL` (#15, #41)
- `OCR_ATTEMPTED_SPARSE` (#28)
- Plus one BORDERLINE-adjacent: `IMAGE_PLACEHOLDER_NO_FALLBACK` (#26) — conceptually OCR-family (no OCR available → placeholder-only output).

The remaining 5 COUPLED signals split as: **table expectation** (2 sites, #18 + #42), **stitching** (group), and **auto-OCR / PDF-attachment informational** (3 signals with zero score impact). The concentration claim from Section F is confirmed by direct re-read: the coupled reads cluster into (a) OCR family via `pdf_classification`/`pdf_stats`/`pdf_ocr_*`, (b) table-expectation via `table_rejected_candidates_by_page`, (c) stitching via `td.extraction_method`. A neutral `SourceProfile` closes (a); an opt-in `RejectedTableCandidate` list closes (b); the extraction-method gate closes (c) trivially.

### H.7 What this verification does NOT cover

- No runtime execution — line references are static reads.
- No cross-check against `parsebench` fixtures' actual metadata dicts.
- The `bbox` metadata surfacing (PR #118, groundwork for the future block-tree contract) is unmerged; the multicolumn/header-footer-table adapters would need it as a hard input.
