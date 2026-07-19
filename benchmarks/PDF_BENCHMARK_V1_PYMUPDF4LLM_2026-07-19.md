# PDF Benchmark v1 — PyMuPDF4LLM adapter (2026-07-19)

**Tool:** PyMuPDF4LLM `1.27.2.3`
**Commit under evaluation:** `cb44f02a35c1bfbfe59b6507b122d79f890d24c0`
**Python:** 3.12.2 · **Platform:** Windows-11-10.0.26200-SP0

**No AksharaMD production code changes.** `SCORING_POLICY_VERSION` remains `"1.0"`. Phase 2 of Issue #68 — one competitor in isolation, no cross-parser ranking here.

## Evaluation semantics — differences from AksharaMD Phase 1

This adapter deliberately does NOT reuse AksharaMD-specific fields:

- **No readiness score or quality band.** PyMuPDF4LLM does not compute one; these fields are `null` in every per-asset record.
- **No `OCR_REQUIRED` / `NEAR_EMPTY_OUTPUT` / `LOW_TEXT_DENSITY` warning codes.** PyMuPDF4LLM does not emit them. Substitutions used here are purely mechanical:
  - **`near_empty_equivalent`** = fewer than 50 non-whitespace characters in the output. Analogous to `NEAR_EMPTY_OUTPUT` but strictly threshold-based.
  - **`low_density_equivalent`** = `output_size_inflation < 0.0005` AND `non_whitespace_chars < 400`. Analogous to `LOW_TEXT_DENSITY` but tool-neutral.
- **No multicolumn / heading / table warnings.** PyMuPDF4LLM does not expose per-block diagnostics comparable to AksharaMD's warning surface. Structural quality is captured via `repeat_content_ratio`, `image_placeholder_ratio`, and human review.

All other definitions are identical to AksharaMD Phase 1: `execution_success` (function did not raise), `output_package_created` (return value is a non-empty string), `meaningful_content` (≥ 200 non-whitespace chars AND not near-empty-equivalent), `structurally_usable` (content-extracted AND (`< 100` tokens OR `repeat_content_ratio < 0.50`) AND (`low_density_equivalent` did NOT fire OR PDF has no text layer)). Runtime boundary matches: single `to_markdown` call, wall-clock time only.

## Interpretation guardrails

- **Do not extrapolate the human-review sample rate to the whole corpus.** The reviewed set is the same 28 files reviewed for AksharaMD (or subset when a file failed to parse).
- **Do not compare directly to AksharaMD numbers on the same corpus without noting the evaluation-semantics differences above.** Two adapters can legitimately report different `content_extracted` counts on the same input if the definitions differ. This report keeps definitions as close to Phase 1 as tool-neutrality permits, but the substitutions above are not exact equivalents.
- **No competitor ranking here.** Phase 3 will combine adapters after each is independently reviewed and stable.

## Headline metrics

| Metric | Value |
|---|---:|
| Files evaluated | 45 |
| `execution_success_rate` | 44 / 45 (97.8 %) |
| `output_package_created_rate` | 36 / 45 (80.0 %) |
| `meaningful_content_rate` | 22 / 45 (48.9 %) |
| `structurally_usable_rate` | 14 / 45 (31.1 %) |
| Near-empty-equivalent files | 18 |
| Low-density-equivalent files | 11 |
| Runtime p50 / p95 (s) | 0.249 / 0.757 |
| Deterministic rate | None |
| Human-usable rate (sample) | 12 / 29 (41.4 %) |

## Per-slice results

### image-only

- n = 13
- execution_success: 13 (100.0%)
- meaningful_content: 1 (7.7%)
- structurally_usable: 0 (0.0%)
- runtime p50/p95 (s): 0.213 / 0.445
- tokens p50: 0
- near-empty: 9, low-density: 10
- human-reviewed: 13 · usable-rate: 0.1538

### malformed

- n = 2
- execution_success: 2 (100.0%)
- meaningful_content: 0 (0.0%)
- structurally_usable: 0 (0.0%)
- runtime p50/p95 (s): 0.212 / 0.215
- tokens p50: 19
- near-empty: 1, low-density: 0
- human-reviewed: 2 · usable-rate: 0.0

### multicolumn

- n = 7
- execution_success: 7 (100.0%)
- meaningful_content: 7 (100.0%)
- structurally_usable: 7 (100.0%)
- runtime p50/p95 (s): 0.308 / 0.609
- tokens p50: 1009
- near-empty: 0, low-density: 0
- human-reviewed: 7 · usable-rate: 0.8571

### multilingual

- n = 4
- execution_success: 4 (100.0%)
- meaningful_content: 1 (25.0%)
- structurally_usable: 1 (25.0%)
- runtime p50/p95 (s): 0.25 / 0.458
- tokens p50: 7
- near-empty: 3, low-density: 0
- human-reviewed: 2 · usable-rate: 0.5

### native-text

- n = 19
- execution_success: 18 (94.7%)
- meaningful_content: 13 (68.4%)
- structurally_usable: 6 (31.6%)
- runtime p50/p95 (s): 0.311 / 20.314
- tokens p50: 271
- near-empty: 5, low-density: 1
- human-reviewed: 5 · usable-rate: 0.6

## By corpus source

### parsebench

- n = 12
- execution / content / structural: 12 / 9 / 9
- runtime p50/p95 (s): 0.306 / 0.471
- near-empty: 3, low-density: 3

### public

- n = 33
- execution / content / structural: 32 / 13 / 5
- runtime p50/p95 (s): 0.229 / 9.509
- near-empty: 15, low-density: 8

## Image-only audit

| asset | hidden-text? | text-layer chars | output chars | tokens | placeholder ratio | warnings-equivalent |
|---|:---:|---:|---:|---:|---:|---|
| `parsebench/japanese_case` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `parsebench/letter3` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `parsebench/myctophidae` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/003-pdflatex-image/pdflatex-image.pdf` | yes | 609 | 679 | 169 | 0.0 | — |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/007-imagemagick-images/imagemagick-images.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/008-reportlab-inline-image/inline-image.pdf` | yes | 5 | 62 | 15 | 0.0 | — |
| `public/018-base64-image/base64image.pdf` | yes | 70 | 72 | 18 | 0.0 | low_density_equivalent |
| `public/019-grayscale-image/grayscale-image.pdf` | no | 0 | 0 | 0 | — | near_empty_equivalent, low_density_equivalent |
| `public/023-cmyk-image/cmyk-image.pdf` | no | 0 | 55 | 13 | 0.0 | near_empty_equivalent, low_density_equivalent |
| `public/028-image-references-deduplication/wrong-references.pdf` | yes | 30 | 146 | 36 | 0.0 | — |

## Failure catalogues

### Execution failures (function raised)

- `public/017-unreadable-meta-data/unreadablemetadata.pdf` — IndexError: range object index out of range

### Content failures (returned but content-poor)

- `parsebench/japanese_case` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `parsebench/letter3` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `parsebench/myctophidae` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-images.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/007-imagemagick-images/imagemagick-lzw.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/008-reportlab-inline-image/inline-image.pdf` (class image-only) — chars=62, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True
- `public/010-pdflatex-forms/pdflatex-forms.pdf` (class malformed) — chars=22, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/012-libreoffice-form/libreoffice-form.pdf` (class malformed) — chars=135, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True
- `public/013-reportlab-overlay/reportlab-overlay.pdf` (class native-text) — chars=68, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True
- `public/015-arabic/habibi-oneline-cmap.pdf` (class multilingual) — chars=28, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/015-arabic/habibi-rotated.pdf` (class multilingual) — chars=28, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/015-arabic/habibi.pdf` (class multilingual) — chars=28, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/016-libre-office-link/libre-office-link.pdf` (class native-text) — chars=37, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/018-base64-image/base64image.pdf` (class image-only) — chars=72, near-empty-equiv=False, low-density-equiv=True, hidden-text-layer=True
- `public/019-grayscale-image/grayscale-image.pdf` (class image-only) — chars=0, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/020-xmp/output_with_metadata_pymupdf.pdf` (class native-text) — chars=16, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/022-pdfkit/pdfkit.pdf` (class native-text) — chars=44, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/023-cmyk-image/cmyk-image.pdf` (class image-only) — chars=55, near-empty-equiv=True, low-density-equiv=True, hidden-text-layer=False
- `public/024-annotations/annotated_pdf.pdf` (class native-text) — chars=55, near-empty-equiv=True, low-density-equiv=False, hidden-text-layer=True
- `public/028-image-references-deduplication/wrong-references.pdf` (class image-only) — chars=146, near-empty-equiv=False, low-density-equiv=False, hidden-text-layer=True

### Structural failures (content but not structurally usable)

- `public/001-trivial/minimal-document.pdf` (class native-text) — repeat=0.9592, low-density-equiv=False
- `public/002-trivial-libre-office-writer/002-trivial-libre-office-writer.pdf` (class native-text) — repeat=0.9691, low-density-equiv=False
- `public/003-pdflatex-image/pdflatex-image.pdf` (class image-only) — repeat=0.8, low-density-equiv=False
- `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` (class native-text) — repeat=0.995, low-density-equiv=False
- `public/006-pdflatex-outline/pdflatex-outline.pdf` (class native-text) — repeat=0.9601, low-density-equiv=False
- `public/014-outlines/mistitled_outlines_example.pdf` (class native-text) — repeat=0.9601, low-density-equiv=False
- `public/025-attachment/with-attachment.pdf` (class native-text) — repeat=0.9592, low-density-equiv=False
- `public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf` (class native-text) — repeat=0.6816, low-density-equiv=False

## Human review — stratified sample

Reviewed: 29 files. Same asset ids as AksharaMD Phase 1 where available (see § Evaluation semantics — the reviewer's usability grade is on PyMuPDF4LLM's specific output, not the AksharaMD output).

| asset | class | usability | evidence |
|---|---|---|---|
| `parsebench/2colmercedes` | multicolumn | usable | 3737 chars; audit-report body extracted with section headings preserved; column order correct. |
| `parsebench/3colpres` | multicolumn | usable_with_minor_defects | 4188 chars; extracts magazine headline + body sections. Column order reads plausibly on inspection; some fragmentation but not the interleaved sentence-splicing seen in AksharaMD's |
| `parsebench/battery` | multicolumn | usable | 2551 chars; safety-warning content extracted with lettered list preserved (a/b/c bullets); heading '## CAUSE BATTERY EXPLOSION' rendered correctly. |
| `parsebench/eastbaytimes` | multicolumn | usable | Single-column news article extracted cleanly; byline + body preserved. |
| `parsebench/elpais` | multicolumn | materially_damaged | 4036 chars but Spanish accents are garbled: 'EL PA?S' instead of 'EL PAÍS'. Also 'S?BADO', 'A?o' — character encoding failure on Latin-1 supplement. Reading order plausible on insp |
| `parsebench/ikea3` | native-text | materially_damaged | 5285 chars but content appears interleaved between columns similar to AksharaMD: 'Ideas Styles Shop at IKEA Here we have gathered lots of Creating a certain style in Here we have g |
| `parsebench/japanese_case` | image-only | unusable | **0 chars extracted** — no OCR; Japanese magazine image produces empty output. AksharaMD Marker delivered 991 chars of Japanese. |
| `parsebench/letter3` | image-only | unusable | **0 chars extracted** — PyMuPDF4LLM has no OCR; image-only UK Home Office letter produces empty output. AksharaMD Marker OCR delivered 2353 chars on this asset. |
| `parsebench/myctophidae` | image-only | unusable | **0 chars extracted** — no OCR; scientific-taxonomy image plate produces empty output. AksharaMD Marker delivered 2431 chars. |
| `parsebench/simple2` | multicolumn | usable | 4394 chars; extracts European Fund evaluation cleanly. Section headings preserved; body text flows correctly. |
| `parsebench/strikeUnderline` | native-text | usable_with_minor_defects | 7772 chars, LARGER than AksharaMD output. Preserves strikethrough markup as '~~text~~' — semantic markup is retained. Body content readable; markdown fidelity higher than AksharaMD |
| `parsebench/text_dense__de` | multilingual | usable_with_minor_defects | German dense-text extracted; layout not fully verified at review DPI, similar to AksharaMD's review. |
| `public/001-trivial/minimal-document.pdf` | native-text | usable | 598 chars; Lorem Ipsum body extracted verbatim. |
| `public/003-pdflatex-image/pdflatex-image.pdf` | image-only | usable_with_minor_defects | Image caption text extracted; image itself referenced as 'picture intentionally omitted'. |
| `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` | native-text | usable | 14491 chars; 4-page LaTeX doc extracted with content per page preserved. |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | image-only | unusable | **0 chars** — single image page; no OCR. |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | image-only | unusable | **0 chars** — single image page; no OCR. |
| `public/007-imagemagick-images/imagemagick-images.pdf` | image-only | unusable | **0 chars** — 6-page image sequence. No OCR. |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | image-only | unusable | **0 chars** — single image page; no OCR. |
| `public/008-reportlab-inline-image/inline-image.pdf` | image-only | materially_damaged | 62 chars; near-empty output with only a placeholder. |
| `public/010-pdflatex-forms/pdflatex-forms.pdf` | malformed | materially_damaged | 22 chars — near-empty output. Form field text not preserved. AksharaMD delivered 63 chars on this asset. |
| `public/012-libreoffice-form/libreoffice-form.pdf` | malformed | materially_damaged | 135 chars — sparse content. Static text partially extracted; form structure lost. |
| `public/015-arabic/habibi.pdf` | multilingual | materially_damaged | 28 chars — 'habibi' + Arabic letters partially extracted but with garbled/duplicated ordering. Reading order for Arabic RTL is broken. |
| `public/017-unreadable-meta-data/unreadablemetadata.pdf` | native-text | unusable | **Execution failure** — PyMuPDF4LLM raises IndexError on this PDF. AksharaMD parses it (usable_with_minor_defects). |
| `public/018-base64-image/base64image.pdf` | image-only | usable_with_minor_defects | Caption text extracted; image referenced with placeholder. |
| `public/019-grayscale-image/grayscale-image.pdf` | image-only | unusable | **0 chars** — single grayscale image; no OCR. |
| `public/023-cmyk-image/cmyk-image.pdf` | image-only | unusable | **0 chars** — single CMYK image; no OCR. |
| `public/026-latex-multicolumn/multicolumn.pdf` | multicolumn | usable | 7168 chars; two-column LaTeX document extracted with section structure preserved. Same result as AksharaMD (both preserve the body). |
| `public/028-image-references-deduplication/wrong-references.pdf` | image-only | materially_damaged | Content-poor multi-page image references PDF; small text extracted, most content lost. |

## Constraints observed

- No AksharaMD parser / validator / scoring / warning-penalty / packaging / model code changed.
- `SCORING_POLICY_VERSION` remains `"1.0"`.
- Same 45-asset frozen manifest as AksharaMD Phase 1; same checksum-verified ParseBench cache.
- No network fetch.
- Per-file errors preserved; single failures do not abort the run.
- Tool-specific raw output NOT committed (only aggregated / sampled records live in git).
- No cross-parser ranking or winner declaration.
