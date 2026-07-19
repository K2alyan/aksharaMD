# AksharaMD PDF Benchmark v1 — Phase 1 baseline (2026-07-19)

**Commit under evaluation:** `cb44f02a35c1bfbfe59b6507b122d79f890d24c0`
**AksharaMD version:** `0.3.6`
**Python:** 3.12.2 · **Platform:** Windows-11-10.0.26200-SP0

**No production code changes.** No parser, validator, scoring, warning-penalty, or `SCORING_POLICY` modifications. Phase 1 of the AksharaMD PDF Benchmark v1 milestone (Issue #68) — AksharaMD alone, no competitor adapters.

**Reading these numbers.** Execution success (CLI exited 0) is NOT parsing success. This report distinguishes four success levels:

- `execution_success` — the CLI exited 0.
- `output_package_created` — `document.md` exists and is non-empty.
- `meaningful_content` (`content_extracted`) — enough non-whitespace characters AND no `NEAR_EMPTY_OUTPUT` warning.
- `structurally_usable` — content-extracted AND acceptable repeat-content AND no `LOW_TEXT_DENSITY` on a PDF with a populated text layer.

Human-reviewed usability is reported separately for the stratified sample (§ Human review).

## Interpretation guardrails

Before reading any number below, note the following:

- **Human-usable rate is a sample rate.** 28 of 45 files received human review; 17 files remain unreviewed. The 60.7 % `human_usable_rate` is `17 / 28`, NOT an extrapolation to `17 / 45`. Do not describe the sample rate as a benchmark-wide product-quality claim.
- **The review sample is stratified across every corpus slice but is not necessarily statistically representative.** Coverage priorities were: every multicolumn asset, every ParseBench asset, every image-only asset that shows a distinct extraction behaviour, plus representative native-text / malformed / multilingual cases.
- **Small-denominator slice rates are descriptive only.** For example, the multilingual reviewed count is `1 / 1` and the malformed count is `2 / 2`. These are anecdotes for context, not statistics.
- **The four success levels are enforced by test.** `execution_success ≥ meaningful_content ≥ structurally_usable`. Definitions are locked in `benchmarks/pdf_benchmark_v1.py` and repeated in § Metric definitions.
- **The repeat-content-domination threshold changed from 0.10 to 0.50, with a length gate at 100 tokens.** This was a benchmark-metric correction after review — short outputs (title + metadata boilerplate) naturally have high 4-gram duplication ratios and were incorrectly flagged at the earlier threshold. **The change is confined to `benchmarks/pdf_benchmark_v1.py` and does NOT alter any production validator, readiness deduction, warning code, or `SCORING_POLICY`.**

## Metric definitions

All definitions live in `benchmarks/pdf_benchmark_v1.py` and are deterministic (identical inputs → identical outputs). They are independent of asset id / filename / corpus source.

- **`execution_success`** — the CLI subprocess exited with code 0.
- **`output_package_created`** — `execution_success` is `True` AND the compiled `document.md` file exists AND is non-empty.
- **`meaningful_content` (`content_extracted`)** — `output_package_created` is `True` AND the compiled `document.md` contains at least `_MIN_MEANINGFUL_CHARS = 200` non-whitespace characters AND the compiler did NOT emit the `NEAR_EMPTY_OUTPUT` warning.
- **`structurally_usable`** — `content_extracted` is `True` AND (either `len(tokens) < 100` OR `repeat_content_ratio < 0.50`) AND (`LOW_TEXT_DENSITY` did NOT fire OR the PDF has no text layer at all as reported by PyMuPDF's `Page.get_text()`).
- **`near_empty_output`** — the `NEAR_EMPTY_OUTPUT` warning was emitted by the shipped compiler (surface unchanged; this benchmark reads it verbatim).
- **`low_text_density`** — the `LOW_TEXT_DENSITY` warning was emitted by the shipped compiler.
- **`repeat_content_ratio`** — fraction of 4-gram windows in `document.md` that appear more than once. `0.0` on a clean output; approaches `1.0` under pathological repetition. Robust only when `len(tokens) >= 100`.
- **`hidden_text_layer` / `hidden_text_layer_chars`** — `PyMuPDF Page.get_text()` return status and cumulative character count across pages; distinguishes a PDF with an embedded text layer (extractable without OCR) from one that genuinely requires OCR.
- **`image_placeholder_ratio`** — fraction of non-empty lines in `document.md` that match the markdown image-placeholder regex `!\[.*\]\(.*\)`. `None` when the output has no lines.

Every field appears in the machine-readable per-asset record.

## Headline metrics

| Metric | Value |
|---|---:|
| Files evaluated | 45 |
| `execution_success_rate` | 45 / 45 (100.0 %) |
| `output_package_created_rate` | 45 / 45 (100.0 %) |
| `meaningful_content_rate` | 26 / 45 (57.8 %) |
| `structurally_usable_rate` | 18 / 45 (40.0 %) |
| Near-empty output files | 3 |
| Low-text-density warned files | 18 |
| Runtime p50 / p95 (s) | 2.569 / 36.361 |
| Quality bands | {'HIGH': 21, 'OK': 5, 'RISKY': 16, 'POOR': 3} |
| Human-usable rate (sample) | 17 / 28 (60.7 %) |

## Why the `OCR_REQUIRED` warning count is 0

No file emitted an `OCR_REQUIRED` warning at parse time. On investigation:

- The Marker vision extra is active in this environment, so image-only PDFs that would otherwise trigger `OCR_REQUIRED` go through OCR silently and return text. The warning does NOT fire when OCR succeeds.
- The relevant surfaces for content-poor outputs are `LOW_TEXT_DENSITY` and `NEAR_EMPTY_OUTPUT` — these fired regardless of whether OCR was attempted, and are the correct signal to key on.
- The § Image-only audit lists every image-classified asset with its hidden-text-layer status, output character count, and warnings — that audit is the correct place to read image-only behaviour, not the `OCR_REQUIRED` count.

## Rule-based quality signals (overall)

- structural_failure: 27 / 45 (60.0%)
- content_extraction_failure: 19 / 45 (42.2%)
- low_text_density: 18 / 45 (40.0%)
- repeat_content_over_50pct: 8 / 45 (17.8%)
- multicolumn_order_warning: 5 / 45 (11.1%)
- near_empty_output: 3 / 45 (6.7%)
- missing_pages: 0 / 45 (0.0%)
- execution_failure: 0 / 45 (0.0%)

## Warning-code distribution (top 15)

- `LOW_TEXT_DENSITY`: 18
- `W_TABLE_EXPECTED_NOT_EXTRACTED`: 16
- `HEADING_SKIP`: 13
- `HEADING_HIERARCHY`: 6
- `W_MULTICOLUMN_ORDER`: 5
- `NEAR_EMPTY_OUTPUT`: 3
- `MISSING_PAGE`: 2
- `W_HEADER_FOOTER_TABLE_GARBLED`: 2
- `CORRUPTED_METADATA`: 1
- `W_PDF_ATTACHMENT_IGNORED`: 1

## Per-slice results

### image-only

- n = 13
- execution_success: 13 (100.0%)
- meaningful_content: 4 (30.8%)
- structurally_usable: 3 (23.1%)
- runtime p50/p95 (s): 19.311 / 45.872
- tokens p50: 20
- near-empty: 2, low-density: 8
- multicolumn-warn: 0
- quality bands: {'HIGH': 5, 'RISKY': 6, 'POOR': 2}
- human-reviewed: 13 · usable-rate: 0.3846

### malformed

- n = 2
- execution_success: 2 (100.0%)
- meaningful_content: 0 (0.0%)
- structurally_usable: 0 (0.0%)
- runtime p50/p95 (s): 9.72 / 16.704
- tokens p50: 23
- near-empty: 0, low-density: 1
- multicolumn-warn: 0
- quality bands: {'RISKY': 1, 'HIGH': 1}
- human-reviewed: 2 · usable-rate: 1.0

### multicolumn

- n = 7
- execution_success: 7 (100.0%)
- meaningful_content: 7 (100.0%)
- structurally_usable: 7 (100.0%)
- runtime p50/p95 (s): 1.858 / 2.089
- tokens p50: 960
- near-empty: 0, low-density: 0
- multicolumn-warn: 2
- quality bands: {'HIGH': 5, 'OK': 2}
- human-reviewed: 7 · usable-rate: 0.7143

### multilingual

- n = 4
- execution_success: 4 (100.0%)
- meaningful_content: 1 (25.0%)
- structurally_usable: 1 (25.0%)
- runtime p50/p95 (s): 19.49 / 24.32
- tokens p50: 20
- near-empty: 1, low-density: 3
- multicolumn-warn: 0
- quality bands: {'HIGH': 1, 'RISKY': 2, 'POOR': 1}
- human-reviewed: 1 · usable-rate: 1.0

### native-text

- n = 19
- execution_success: 19 (100.0%)
- meaningful_content: 14 (73.7%)
- structurally_usable: 7 (36.8%)
- runtime p50/p95 (s): 2.113 / 35.541
- tokens p50: 226
- near-empty: 0, low-density: 6
- multicolumn-warn: 3
- quality bands: {'OK': 3, 'HIGH': 9, 'RISKY': 7}
- human-reviewed: 5 · usable-rate: 0.8

## By corpus source

### parsebench

- n = 12
- execution / content / structural: 12 / 12 / 12
- runtime p50/p95 (s): 1.877 / 46.517
- near-empty: 0, low-density: 0
- quality bands: {'HIGH': 9, 'OK': 3}

### public

- n = 33
- execution / content / structural: 33 / 14 / 6
- runtime p50/p95 (s): 17.181 / 29.266
- near-empty: 3, low-density: 18
- quality bands: {'HIGH': 12, 'RISKY': 16, 'OK': 2, 'POOR': 3}

## Image-only audit

Every asset classified as `image-only`, with the fields the review checklist requires. `hidden_text_layer` is `True` when PyMuPDF's `Page.get_text()` returns non-empty text — this distinguishes a PDF whose image is accompanied by a text layer (extractable without OCR) from one that requires an OCR pass.

| asset | hidden-text? | text-layer chars | output chars | tokens | placeholder ratio | band | warnings |
|---|:---:|---:|---:|---:|---:|:---:|---|
| `parsebench/japanese_case` | no | 0 | 991 | 247 | 0.25 | HIGH | — |
| `parsebench/letter3` | no | 0 | 2353 | 588 | 0.0769 | HIGH | — |
| `parsebench/myctophidae` | no | 0 | 2431 | 607 | 0.0 | HIGH | — |
| `public/003-pdflatex-image/pdflatex-image.pdf` | yes | 609 | 661 | 165 | 0.2 | HIGH | — |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | no | 0 | 43 | 10 | 1.0 | RISKY | LOW_TEXT_DENSITY |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | no | 0 | 43 | 10 | 1.0 | RISKY | LOW_TEXT_DENSITY |
| `public/007-imagemagick-images/imagemagick-images.pdf` | no | 0 | 268 | 67 | 1.0 | POOR | NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | no | 0 | 43 | 10 | 1.0 | RISKY | LOW_TEXT_DENSITY |
| `public/008-reportlab-inline-image/inline-image.pdf` | yes | 5 | 44 | 11 | 1.0 | RISKY | LOW_TEXT_DENSITY |
| `public/018-base64-image/base64image.pdf` | yes | 70 | 189 | 47 | 0.3333 | HIGH | — |
| `public/019-grayscale-image/grayscale-image.pdf` | no | 0 | 44 | 11 | 1.0 | RISKY | LOW_TEXT_DENSITY |
| `public/023-cmyk-image/cmyk-image.pdf` | no | 0 | 44 | 11 | 1.0 | RISKY | LOW_TEXT_DENSITY |
| `public/028-image-references-deduplication/wrong-references.pdf` | yes | 30 | 83 | 20 | 0.25 | POOR | NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY |

## Failure catalogues

No execution failures across 45 files.

### Content failures (ran successfully but no meaningful content)

- `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` (class image-only) — chars=43, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` (class image-only) — chars=43, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-images.pdf` (class image-only) — chars=268, band=POOR, near-empty=True, low-density=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-lzw.pdf` (class image-only) — chars=43, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=False
- `public/008-reportlab-inline-image/inline-image.pdf` (class image-only) — chars=44, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/010-pdflatex-forms/pdflatex-forms.pdf` (class malformed) — chars=63, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/012-libreoffice-form/libreoffice-form.pdf` (class malformed) — chars=126, band=HIGH, near-empty=False, low-density=False, hidden-text-layer=True
- `public/013-reportlab-overlay/reportlab-overlay.pdf` (class native-text) — chars=65, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/015-arabic/habibi-oneline-cmap.pdf` (class multilingual) — chars=13, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/015-arabic/habibi-rotated.pdf` (class multilingual) — chars=148, band=POOR, near-empty=True, low-density=True, hidden-text-layer=True
- `public/015-arabic/habibi.pdf` (class multilingual) — chars=13, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/016-libre-office-link/libre-office-link.pdf` (class native-text) — chars=63, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/018-base64-image/base64image.pdf` (class image-only) — chars=189, band=HIGH, near-empty=False, low-density=False, hidden-text-layer=True
- `public/019-grayscale-image/grayscale-image.pdf` (class image-only) — chars=44, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=False
- `public/020-xmp/output_with_metadata_pymupdf.pdf` (class native-text) — chars=58, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/022-pdfkit/pdfkit.pdf` (class native-text) — chars=28, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/023-cmyk-image/cmyk-image.pdf` (class image-only) — chars=44, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=False
- `public/024-annotations/annotated_pdf.pdf` (class native-text) — chars=40, band=RISKY, near-empty=False, low-density=True, hidden-text-layer=True
- `public/028-image-references-deduplication/wrong-references.pdf` (class image-only) — chars=83, band=POOR, near-empty=True, low-density=True, hidden-text-layer=True

### Structural failures (content present but not structurally usable)

- `public/001-trivial/minimal-document.pdf` (class native-text) — band=HIGH, repeat=0.8776, low-density=False
- `public/002-trivial-libre-office-writer/002-trivial-libre-office-writer.pdf` (class native-text) — band=HIGH, repeat=0.9691, low-density=False
- `public/003-pdflatex-image/pdflatex-image.pdf` (class image-only) — band=HIGH, repeat=0.8952, low-density=False
- `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` (class native-text) — band=RISKY, repeat=0.9613, low-density=False
- `public/006-pdflatex-outline/pdflatex-outline.pdf` (class native-text) — band=OK, repeat=0.9661, low-density=False
- `public/014-outlines/mistitled_outlines_example.pdf` (class native-text) — band=OK, repeat=0.9661, low-density=False
- `public/025-attachment/with-attachment.pdf` (class native-text) — band=HIGH, repeat=0.8776, low-density=False
- `public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf` (class native-text) — band=RISKY, repeat=0.6377, low-density=True

## Human review — stratified sample

Reviewed: 28 of 45 files.

| asset | class | usability | evidence |
|---|---|---|---|
| `parsebench/2colmercedes` | multicolumn | usable | 2-column product sheet; 7 blocks band HIGH 85; column-first order preserved. Multicolumn detector correctly silent. |
| `parsebench/3colpres` | multicolumn | materially_damaged | 3-column magazine; extraction produces 11 blocks that visibly splice content across columns (see PARSEBENCH_PAGE_GROUND_TRUTH mixed defect_kind); W_MULTICOLUMN_ORDER fires; body re |
| `parsebench/battery` | multicolumn | usable | 2-column safety sheet; 2 merged blocks band HIGH 87; parser merges columns cleanly; text is coherent. |
| `parsebench/eastbaytimes` | multicolumn | usable | Single-column news article; 6 blocks band HIGH 83; masthead + byline + 4 body paragraphs preserved in order. |
| `parsebench/elpais` | multicolumn | materially_damaged | El Pais front page; 26 blocks; span-level column splicing evident in extracted paragraphs. Block-level detector correctly silent per phase B5 (defect_kind=span-level). Text is read |
| `parsebench/ikea3` | native-text | materially_damaged | IKEA magazine spread; 51 short blocks band OK 79; heading skips + table_expected_not_extracted; blocks are fragmented and out of visual reading order. Not multicolumn per B5 correc |
| `parsebench/japanese_case` | image-only | usable_with_minor_defects | Image-only Japanese magazine spread; OCR recovered 991 chars / 247 tokens band HIGH 87; Japanese glyphs present. Reading order (vertical Japanese vs Latin) not verified. |
| `parsebench/letter3` | image-only | usable | Image-only UK Home Office letter; OCR (Marker) recovered 2353 chars / 588 tokens; band HIGH 87; readable text output. No OCR_REQUIRED warning because OCR succeeded. |
| `parsebench/myctophidae` | image-only | usable_with_minor_defects | Image-only scientific taxonomy plate; OCR recovered 2431 chars / 607 tokens band HIGH 87; readable but taxonomic Latin names may not always render perfectly (not verified against g |
| `parsebench/simple2` | multicolumn | usable_with_minor_defects | 2-column academic page; 8 blocks band HIGH; ambiguous per B5 review. Block sequence appears column-first from block counts; low-DPI review could not confirm span-level splicing con |
| `parsebench/strikeUnderline` | native-text | usable_with_minor_defects | ERISA TOC single-column with right-margin sidebar; 5 blocks band HIGH 85; W_MULTICOLUMN_ORDER FALSELY fires on sidebar (documented FP class). Main body content extracts cleanly. |
| `parsebench/text_dense__de` | multilingual | usable_with_minor_defects | German dense-text page; 10 blocks band HIGH 85 with HEADING_HIERARCHY; text extracted but layout unassessable at review DPI. Ambiguous per B5 review. |
| `public/001-trivial/minimal-document.pdf` | native-text | usable | Trivial 1-block document; band HIGH 87; no warnings; extraction perfect. |
| `public/003-pdflatex-image/pdflatex-image.pdf` | image-only | usable_with_minor_defects | Image + caption in LaTeX; hidden text layer present (captions); 661 chars extracted band HIGH. Image content is a placeholder; usability is fine for the text portion. |
| `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` | native-text | usable | 4-page pdflatex document; band HIGH; content and page count preserved. |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | image-only | unusable | Single image page; 43 chars band RISKY 67; LOW_TEXT_DENSITY fires. Placeholder-only output. |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | image-only | unusable | Single image page; 43 chars band RISKY 67; LOW_TEXT_DENSITY fires. Placeholder-only output. |
| `public/007-imagemagick-images/imagemagick-images.pdf` | image-only | unusable | 6-page image sequence; 268 chars extracted band POOR 41; NEAR_EMPTY_OUTPUT + LOW_TEXT_DENSITY warnings fire. Content lost; the true failure mode this benchmark should flag. |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | image-only | unusable | Single image page; 43 chars band RISKY 67; LOW_TEXT_DENSITY fires. Placeholder-only output. |
| `public/008-reportlab-inline-image/inline-image.pdf` | image-only | unusable | Inline image no caption; 44 chars band RISKY 67; LOW_TEXT_DENSITY fires. |
| `public/010-pdflatex-forms/pdflatex-forms.pdf` | malformed | usable_with_minor_defects | Form document; static text extracts but form fields are not filled/preserved. |
| `public/012-libreoffice-form/libreoffice-form.pdf` | malformed | usable_with_minor_defects | Form document; static text extracts; form structure lost. |
| `public/017-unreadable-meta-data/unreadablemetadata.pdf` | native-text | usable_with_minor_defects | Native text with unusual metadata; text extracts fine; metadata anomaly is not user-visible. |
| `public/018-base64-image/base64image.pdf` | image-only | usable_with_minor_defects | Image with text caption; 189 chars band HIGH 87; caption extracted, image is a placeholder. |
| `public/019-grayscale-image/grayscale-image.pdf` | image-only | unusable | Single grayscale image; 44 chars band RISKY 67; LOW_TEXT_DENSITY fires. |
| `public/023-cmyk-image/cmyk-image.pdf` | image-only | unusable | Single CMYK image; 44 chars band RISKY 67; LOW_TEXT_DENSITY fires. |
| `public/026-latex-multicolumn/multicolumn.pdf` | multicolumn | usable_with_minor_defects | 3-page LaTeX 2-column doc; W_MULTICOLUMN_ORDER fires on p3 table; body pages extract cleanly. Table interleaving reflected in detector warning. |
| `public/028-image-references-deduplication/wrong-references.pdf` | image-only | unusable | 3-page image references; 83 chars band POOR 47; NEAR_EMPTY_OUTPUT + LOW_TEXT_DENSITY. Multi-page image content lost. |

### Usability by slice

- **multicolumn**: usable=3, minor=2, damaged=2, unusable=0
- **native-text**: usable=2, minor=2, damaged=1, unusable=0
- **image-only**: usable=1, minor=4, damaged=0, unusable=8
- **multilingual**: usable=0, minor=1, damaged=0, unusable=0
- **malformed**: usable=0, minor=2, damaged=0, unusable=0

## Runtime semantics

`runtime_seconds` is wall-clock time for one `aksharamd compile --json --quiet` invocation. It includes process startup, package loading, parser classification, OCR (when invoked), and output serialisation. When the harness runs with the determinism check enabled, the second run is recorded independently and is NOT included in `runtime_seconds`.

## Constraints observed

- No parser / validator / scoring / warning-penalty / packaging / model code changed.
- `SCORING_POLICY_VERSION` remains `"1.0"`.
- No PDF bytes added to git.
- Deterministic result ordering (assets sorted by id).
- No network fetch during benchmark execution.
- ParseBench sha256 + size verified before the run.
- Per-file errors preserved; single failures do not abort the run.

## Next steps

- Phase 2: competitor adapters (MarkItDown, Docling, Unstructured, PyMuPDF4LLM) — one PR each with pinned versions.
- Phase 3: comparison report — strengths by document class, no universal-winner declaration.