# PDF Benchmark v1 — MarkItDown adapter (2026-07-19)

**Tool:** MarkItDown `0.1.6`
**Commit under evaluation:** `cb44f02a35c1bfbfe59b6507b122d79f890d24c0`
**Python:** 3.12.2 · **Platform:** Windows-11-10.0.26200-SP0

**No AksharaMD production code changes.** `SCORING_POLICY_VERSION` remains `"1.0"`. Phase 2 of Issue #68 — second competitor adapter in isolation, no cross-parser ranking here.

## Configuration

- `MarkItDown()` constructor with all defaults (builtins registered, plugins disabled unless installed).
- **No LLM client** — `_llm_client` is `None`. No external service call.
- **No OCR / vision extras enabled** for this run.
- **No Document-Intelligence extras** activated.
- PDF backend: MarkItDown's built-in `PdfConverter`.
- Fully offline — checked via `_llm_client is None` at run time.

## Evaluation semantics — differences from AksharaMD Phase 1

This adapter deliberately does NOT reuse AksharaMD-specific fields:

- **No readiness score / quality band / warning codes.**
- **`near_empty_equivalent`** = non-whitespace char count < 50 (same as PyMuPDF4LLM adapter).
- **`low_density_equivalent`** = `output_size_inflation < 0.0005` AND `non_whitespace_chars < 400`.
- All four success levels: identical to Phase 1 subject to those substitutions.
- Test `test_artifact_four_success_levels_are_monotone` enforces `execution ≥ package ≥ content ≥ structural` per row.

## Interpretation guardrails

- **`meaningful_content` and `structurally_usable` are benchmark-rule classifications** — tool-neutral deterministic gates. NOT substitutes for human judgment.
- **AksharaMD readiness scores / warning codes are NOT applied to MarkItDown.**
- **Human-usable rate is a sample rate.** See § Matched human-review parity.
- **No cross-parser winner declaration.** Phase 3 will discuss slice-level differences.

## Headline metrics

| Metric | Value |
|---|---:|
| Files evaluated | 45 |
| `execution_success_rate` | 45 / 45 (100.0 %) |
| `output_package_created_rate` | 37 / 45 (82.2 %) |
| `meaningful_content_rate` | 23 / 45 (51.1 %) |
| `structurally_usable_rate` | 14 / 45 (31.1 %) |
| Near-empty-equivalent files | 18 |
| Low-density-equivalent files | 10 |
| Runtime p50 / p95 (s) | 0.226 / 2.55 |
| Deterministic rate | None |
| Human-usable rate (sample) | 9 / 29 (31.0 %) |

## Matched human-review parity vs. AksharaMD Phase 1

| Metric | Value |
|---|---:|
| Matched sample size | **28** |
| AksharaMD usable (matched) | 17 |
| MarkItDown usable (matched) | 9 |
| Both usable | 9 |
| AksharaMD only usable | 8 |
| MarkItDown only usable | 0 |
| Neither usable | 11 |

**AksharaMD-only usable:**
- `parsebench/japanese_case`
- `parsebench/letter3`
- `parsebench/myctophidae`
- `parsebench/simple2`
- `parsebench/strikeUnderline`
- `public/010-pdflatex-forms/pdflatex-forms.pdf`
- `public/012-libreoffice-form/libreoffice-form.pdf`
- `public/026-latex-multicolumn/multicolumn.pdf`

## Matched human-review parity vs. PyMuPDF4LLM

| Metric | Value |
|---|---:|
| Matched sample size | **29** |
| PyMuPDF4LLM usable (matched) | 13 |
| MarkItDown usable (matched) | 9 |
| Both usable | 8 |
| PyMuPDF4LLM only usable | 5 |
| MarkItDown only usable | 1 |
| Neither usable | 15 |

## Three-way paired outcome — AksharaMD ∩ PyMuPDF4LLM ∩ MarkItDown

Three-way matched sample size: **28**.

| Bucket | Count |
|---|---:|
| All three usable | 8 |
| AksharaMD + PyMuPDF4LLM only | 3 |
| AksharaMD + MarkItDown only | 1 |
| PyMuPDF4LLM + MarkItDown only | 0 |
| Only AksharaMD | 5 |
| Only PyMuPDF4LLM | 2 |
| Only MarkItDown | 0 |
| None usable | 9 |

## Runtime-boundary parity

Reported `runtime_seconds` is one primary parse per asset. Millisecond-level comparison across adapters is not defensible; the tools use different process boundaries.

| Included in runtime | AksharaMD Phase 1 | PyMuPDF4LLM | MarkItDown (this run) |
|---|:---:|:---:|:---:|
| Process startup | **yes** (subprocess) | no | no |
| Import + package loading | **yes** | no | no |
| Converter/backend init | in subprocess | no (reused) | no (reused instance) |
| PDF parsing | yes | yes | yes |
| OCR | yes (Marker) | no | no |
| LLM call | no | no | no |
| Markdown generation | yes | yes | yes |
| Output serialisation | yes (disk write) | no (in-memory string) | no (in-memory string) |
| Checksum verification | no | no | no |
| Deterministic second parse | no | no | no |

**Consequence.** AksharaMD's per-asset runtime includes CLI subprocess startup + package load + disk write; PyMuPDF4LLM and MarkItDown are direct in-process library calls with a single shared instance re-used across assets. **No exact speed ratio is claimed** between adapters. Latency-category discussion is deferred to Phase 3.

## Per-slice results

### image-only

- n = 13
- execution_success: 13 (100.0%)
- meaningful_content: 1 (7.7%)
- structurally_usable: 1 (7.7%)
- runtime p50/p95 (s): 0.069 / 0.204
- tokens p50: 0
- near-empty: 11, low-density: 10
- human-reviewed: 13 · usable-rate: 0.1538

### malformed

- n = 2
- execution_success: 2 (100.0%)
- meaningful_content: 0 (0.0%)
- structurally_usable: 0 (0.0%)
- runtime p50/p95 (s): 0.242 / 0.245
- tokens p50: 18
- near-empty: 1, low-density: 0
- human-reviewed: 2 · usable-rate: 0.0

### multicolumn

- n = 7
- execution_success: 7 (100.0%)
- meaningful_content: 7 (100.0%)
- structurally_usable: 7 (100.0%)
- runtime p50/p95 (s): 1.179 / 1.456
- tokens p50: 1047
- near-empty: 0, low-density: 0
- human-reviewed: 7 · usable-rate: 0.4286

### multilingual

- n = 4
- execution_success: 4 (100.0%)
- meaningful_content: 1 (25.0%)
- structurally_usable: 1 (25.0%)
- runtime p50/p95 (s): 0.188 / 1.431
- tokens p50: 15
- near-empty: 2, low-density: 0
- human-reviewed: 2 · usable-rate: 0.5

### native-text

- n = 19
- execution_success: 19 (100.0%)
- meaningful_content: 14 (73.7%)
- structurally_usable: 5 (26.3%)
- runtime p50/p95 (s): 0.504 / 43.345
- tokens p50: 227
- near-empty: 4, low-density: 0
- human-reviewed: 5 · usable-rate: 0.6

## Image-only audit

| asset | hidden-text? | text-layer chars | output chars | tokens | placeholder ratio | warnings-equivalent |
|---|:---:|---:|---:|---:|---:|---|
| `parsebench/japanese_case` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `parsebench/letter3` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `parsebench/myctophidae` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/003-pdflatex-image/pdflatex-image.pdf` | yes | 609 | 737 | 184 | 0.0 | — |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/007-imagemagick-images/imagemagick-images.pdf` | no | 0 | 53 | 13 | 0.0 | near_empty_equivalent |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/008-reportlab-inline-image/inline-image.pdf` | yes | 5 | 6 | 1 | 0.0 | near_empty_equivalent |
| `public/018-base64-image/base64image.pdf` | yes | 70 | 71 | 17 | 0.0 | low_density_equivalent |
| `public/019-grayscale-image/grayscale-image.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/023-cmyk-image/cmyk-image.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/028-image-references-deduplication/wrong-references.pdf` | yes | 30 | 35 | 8 | 0.0 | near_empty_equivalent, low_density_equivalent |

## Failure catalogues

No execution failures across 45 files.

### Content failures (returned but content-poor)
- `parsebench/japanese_case` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `parsebench/letter3` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `parsebench/myctophidae` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-images.pdf` (class image-only) — chars=53, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-lzw.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/008-reportlab-inline-image/inline-image.pdf` (class image-only) — chars=6, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/010-pdflatex-forms/pdflatex-forms.pdf` (class malformed) — chars=24, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/012-libreoffice-form/libreoffice-form.pdf` (class malformed) — chars=120, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True
- `public/013-reportlab-overlay/reportlab-overlay.pdf` (class native-text) — chars=67, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True
- `public/015-arabic/habibi-oneline-cmap.pdf` (class multilingual) — chars=22, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/015-arabic/habibi-rotated.pdf` (class multilingual) — chars=106, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True
- `public/015-arabic/habibi.pdf` (class multilingual) — chars=22, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/016-libre-office-link/libre-office-link.pdf` (class native-text) — chars=36, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/018-base64-image/base64image.pdf` (class image-only) — chars=71, near-empty-equiv=False, low-density-equiv=True, hidden-text-layer=True
- `public/019-grayscale-image/grayscale-image.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/020-xmp/output_with_metadata_pymupdf.pdf` (class native-text) — chars=15, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/022-pdfkit/pdfkit.pdf` (class native-text) — chars=27, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/023-cmyk-image/cmyk-image.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/024-annotations/annotated_pdf.pdf` (class native-text) — chars=45, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/028-image-references-deduplication/wrong-references.pdf` (class image-only) — chars=35, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=True

### Structural failures (content but not structurally usable)
- `public/001-trivial/minimal-document.pdf` (class native-text) — repeat=0.8687, low-density-equiv=False
- `public/002-trivial-libre-office-writer/002-trivial-libre-office-writer.pdf` (class native-text) — repeat=0.9691, low-density-equiv=False
- `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` (class native-text) — repeat=0.995, low-density-equiv=False
- `public/006-pdflatex-outline/pdflatex-outline.pdf` (class native-text) — repeat=0.9517, low-density-equiv=False
- `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` (class native-text) — repeat=0.6195, low-density-equiv=False
- `public/009-pdflatex-geotopo/GeoTopo.pdf` (class native-text) — repeat=0.6138, low-density-equiv=False
- `public/014-outlines/mistitled_outlines_example.pdf` (class native-text) — repeat=0.9517, low-density-equiv=False
- `public/025-attachment/with-attachment.pdf` (class native-text) — repeat=0.8687, low-density-equiv=False
- `public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf` (class native-text) — repeat=0.7196, low-density-equiv=False

## Human review — stratified sample

Reviewed: 29 files (same asset ids as AksharaMD Phase 1 / PyMuPDF4LLM where available; every judgment is on MarkItDown's own output).

| asset | class | usability | evidence |
|---|---|---|---|
| `parsebench/2colmercedes` | multicolumn | usable_with_minor_defects | 3725 chars; German audit-report body extracted with hyphenation preserved as line breaks ('pro-
vision'). Section headings preserved. |
| `parsebench/3colpres` | multicolumn | materially_damaged | 4191 chars but each word is line-broken with heavy whitespace ('A Message from the President / Dear / Members, / I write in an / effort to share'). Reading order is fragmented; con |
| `parsebench/battery` | multicolumn | usable | 2394 chars; safety-warning content extracted with lettered list (a/b/c) preserved. Same quality as PyMuPDF4LLM. |
| `parsebench/eastbaytimes` | multicolumn | usable | Single-column news article extracted cleanly; body preserved. |
| `parsebench/elpais` | multicolumn | materially_damaged | 6724 chars but rendered as broken table syntax with columns like '\| EL \| PERIÓDICO \|'; Spanish accents preserved (PERIÓDICO, ESPAÑOL, SÁBADO). Reading order corrupted by table-synt |
| `parsebench/ikea3` | native-text | materially_damaged | 7024 chars, cleaner than PyMuPDF4LLM's extraction of this asset but still shows magazine-layout interleaving. TOC-like content readable; body sections fragmented. |
| `parsebench/japanese_case` | image-only | unusable | **0 chars extracted** — no OCR; Japanese magazine image produces empty output. |
| `parsebench/letter3` | image-only | unusable | **0 chars extracted** — MarkItDown has no OCR; image-only UK Home Office letter produces empty output. Same failure mode as PyMuPDF4LLM; AksharaMD Marker delivered 2353 chars. |
| `parsebench/myctophidae` | image-only | unusable | **0 chars extracted** — no OCR; scientific-taxonomy image plate produces empty output. |
| `parsebench/simple2` | multicolumn | materially_damaged | 4646 chars but heavily fragmented: 'It / follows / (2015/1017). / the European Fund / This report presents the results of the' — words split across lines. Content present but order |
| `parsebench/strikeUnderline` | native-text | materially_damaged | 7434 chars but TOC entries have concatenated page numbers ('103101' = '10' + '3101'). Strikethrough markup is NOT preserved as markdown syntax (unlike PyMuPDF4LLM). Content readabl |
| `parsebench/text_dense__de` | multilingual | usable_with_minor_defects | German dense-text extracted; layout not fully verified at review DPI; typical MarkItDown quality on European text. |
| `public/001-trivial/minimal-document.pdf` | native-text | usable | 598 chars; Lorem Ipsum body extracted with line-break at column width but content complete. Same as PyMuPDF4LLM. |
| `public/003-pdflatex-image/pdflatex-image.pdf` | image-only | usable_with_minor_defects | Image caption text extracted; image not rendered as placeholder (MarkItDown emits fewer image tokens than PyMuPDF4LLM). |
| `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` | native-text | usable | Multi-page LaTeX doc extracted with content per page preserved. |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | image-only | unusable | **0 chars** — single image page; no OCR. |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | image-only | unusable | **0 chars** — single image page; no OCR. |
| `public/007-imagemagick-images/imagemagick-images.pdf` | image-only | unusable | **53 chars** — 'Background' repeated 4 times, no other content. No OCR; content lost. |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | image-only | unusable | **0 chars** — single image page; no OCR. |
| `public/008-reportlab-inline-image/inline-image.pdf` | image-only | unusable | 6 chars — essentially empty output. |
| `public/010-pdflatex-forms/pdflatex-forms.pdf` | malformed | unusable | 24 chars — only form labels 'Name / Check / Submit / 1'. Static text lost. |
| `public/012-libreoffice-form/libreoffice-form.pdf` | malformed | materially_damaged | 120 chars — sparse content; form structure lost. |
| `public/015-arabic/habibi.pdf` | multilingual | materially_damaged | 22 chars: 'حَبيبي habibi حَبيبي'. Arabic script preserved but reading order not verified; near-empty output. Similar degradation to PyMuPDF4LLM. |
| `public/017-unreadable-meta-data/unreadablemetadata.pdf` | native-text | usable_with_minor_defects | 2521 chars; extracts datasheet content ('Rotary Potentiometer', 'Electrocomponents Industrial'). **MarkItDown parses this file where PyMuPDF4LLM raises IndexError.** Content struct |
| `public/018-base64-image/base64image.pdf` | image-only | usable_with_minor_defects | Caption text extracted; image reference stripped. |
| `public/019-grayscale-image/grayscale-image.pdf` | image-only | unusable | **0 chars** — no OCR. |
| `public/023-cmyk-image/cmyk-image.pdf` | image-only | unusable | **0 chars** — no OCR. |
| `public/026-latex-multicolumn/multicolumn.pdf` | multicolumn | materially_damaged | 13201 chars but rendered as broken markdown table syntax ('\| Two-Column \| \| \| \| Document \| with \| Lorem'). Content present but not readable as normal prose; table reconstruction mi |
| `public/028-image-references-deduplication/wrong-references.pdf` | image-only | materially_damaged | Multi-page image references PDF; small text extracted, most content lost. |

## Constraints observed

- No AksharaMD parser / validator / scoring / warning-penalty / packaging / model code changed.
- `SCORING_POLICY_VERSION` remains `"1.0"`.
- Same 45-asset frozen manifest as AksharaMD Phase 1 and PyMuPDF4LLM.
- Same checksum-verified ParseBench cache.
- No network fetch.
- No LLM configured.
- Per-file errors preserved; single failures do not abort the run.
- Tool-specific raw output NOT committed (only aggregated / sampled records live in git).
- No cross-parser ranking or winner declaration.
