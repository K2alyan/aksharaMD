# Advanced Fidelity Validation — 2026-07-18

Validation run of AksharaMD `main` at `29dbb9d` against the advanced document paths that were **not** exercised by the base-install E2E validation in PR #40. Companion machine-readable file: [`ADVANCED_FIDELITY_2026-07-18.json`](./ADVANCED_FIDELITY_2026-07-18.json).

## Executive verdict

**Works end-to-end for the base install with two silent-fidelity concerns and one large architectural gap.**

- 25 of 26 documents in the curated subset compiled with valid output; the one non-zero exit was an intentionally-encrypted PDF that correctly raised `ENCRYPTED_PDF` and refused.
- Readiness scoring accurately signalled degradation on **6 of 8** documents where content was lost or heavily reduced.
- **Two documents scored HIGH while losing material content**: the multicolumn LaTeX PDF (reading order silently interleaved from both columns) and the PDF-with-attachment (embedded file dropped without any signal). Both were recorded as follow-up issues at the time of the original 2026-07-18 run; F2 has since been addressed by Issue #51 (the omission now emits `W_PDF_ATTACHMENT_IGNORED` — see updated §F2 below). F1 remains open.
- Five of the six named ParseBench regression cases (`text_dense__de`, `letter3`, `myctophidae`, `simple2`, `strikeUnderline`, a Japanese fixture) are **not testable in this repository** — the binary corpus is not present. Recorded as an environment/dependency finding, not a code defect.

Optional extras: `[vision]` (Marker), `[ocr]` (Tesseract), and `[math]` (pix2tex) paths were not exercised end-to-end because the required system binaries or model caches are not available in this workstation environment. Existing unit tests already cover the invocation-and-failure surfaces; a fuller in-CI run is called out as a follow-up.

## Phase 1 — Environment audit

| Item | Value |
|---|---|
| OS | Windows 11 (build 26200) |
| Python | 3.12.2 |
| CUDA GPU | Available — 1 device, torch 2.12.1+cu126 |
| AksharaMD | 0.3.6, HEAD `29dbb9d` (post #34, #36, #39, #40, #47, #48) |
| `[vision]` — marker-pdf | 1.10.2 installed |
| `[vision]` — surya-ocr | 0.17.1 installed |
| `[ocr]` — pytesseract wrapper | 0.3.13 installed |
| `[ocr]` — **Tesseract binary** | **MISSING** — cannot exercise OCR path |
| `[math]` — pix2tex | **NOT INSTALLED** — cannot exercise math OCR |
| `[audio]` — openai-whisper | 20250625 installed |
| `[audio]` — ffmpeg binary | Present |
| `[cloud]` — boto3 | 1.43.40 installed |
| `[index]` — sentence-transformers | 5.6.0 installed |
| `[index]` — chromadb | 1.5.9 installed |
| `[index]` — watchdog | 6.0.0 installed |
| Legacy Office — LibreOffice binary | **MISSING** — `.doc`/`.ppt` path not exercisable |
| Pandoc binary | **MISSING** — niche formats not exercisable |

**No dependency constraints were altered** to make the validation run. Missing binaries and unavailable extras are recorded as findings.

## Phase 2 — Corpus inventory

The repository ships a public corpus under `benchmarks/.public_corpus/`. Enumerated content, all legally redistributable:

- 34 PDFs categorised 001–028 covering trivial, images, forms, encrypted, outlined, multicolumn, Arabic/RTL, rotated, cropped/scaled, annotations, attachments, PDF/A, XMP, CMYK, deduplicated references, and more
- 11 DOCX, 11 PPTX, 11 XLSX, 11 CSV, 11 XML, 11 ZIP, 11 HTML, 11 TXT, 12 MD, 479 JSON synthetic fixtures

**Named ParseBench cases** requested by the task (`text_dense__de`, `letter3`, `myctophidae`, `simple2`, `strikeUnderline`, a Japanese fixture): **NOT PRESENT** in the repository. The ParseBench binary corpus is external. Historical evidence exists in `benchmarks/EXPECTATION_VALIDATION_REPORT_V3.md` and `benchmarks/expectation_detector_lock_v3.json`, but per the task constraint "Do not rely only on historical evidence files when the binary is present" — these cases are recorded here as **unavailable/not reproduced in this run**.

**Curated 26-document subset** was selected to exercise every advanced fidelity axis (multicolumn, arabic/RTL, rotated, cropped/scaled, form, annotations, attachment, encrypted, outline, image-only, large-prose, plus one representative of every non-PDF supported format). Full source list in `ADVANCED_FIDELITY_2026-07-18.json`.

## Phase 3 — Baseline execution

Every document was compiled via the installed-wheel CLI (`aksharamd compile <src> -o <dir> --json --quiet`) to avoid accidental source-tree imports. Total wall-clock for the 26-document run: **214s** on a Windows dev workstation.

Compact per-document summary (full detail in the machine-readable JSON):

| Category | exit | band | ready | chunks | pages | tokens | warnings |
|---|---:|---|---:|---:|---:|---:|---|
| pdf.trivial | 0 | HIGH | 87 | 1 | 1 | 171 | — |
| pdf.libreoffice | 0 | HIGH | 87 | 1 | 1 | 170 | — |
| pdf.image | 0 | HIGH | 87 | 2 | 1 | 193 | — |
| pdf.multi_page | 0 | RISKY | 65 | 2 | 4 | 529 | MISSING_PAGE ×2 (Issue #54 update) |
| **pdf.encrypted** | **1** | – | – | – | – | – | ENCRYPTED_PDF (intentional failure) |
| pdf.outline | 0 | OK | 81 | 3 | 4 | 1275 | — |
| pdf.image_only | 0 | POOR | 41 | 1 | 6 | 106 | NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY |
| pdf.large_prose (117 pages) | 0 | HIGH | 87 | 155 | 117 | 60104 | W_TABLE_EXPECTED_NOT_EXTRACTED (experimental, penalty=0) |
| pdf.form | 0 | RISKY | 67 | 1 | 1 | 20 | LOW_TEXT_DENSITY |
| pdf.arabic | 0 | RISKY | 67 | 1 | 1 | 8 | LOW_TEXT_DENSITY |
| pdf.rotated | 0 | POOR | 41 | 1 | 4 | 61 | NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY |
| pdf.annotations | 0 | RISKY | 67 | 1 | 1 | 11 | LOW_TEXT_DENSITY |
| **pdf.attachment** | **0** | **HIGH** | **87** | 1 | 1 | 171 | **—** |
| **pdf.multicolumn** | **0** | **HIGH** | **85** | 9 | 3 | 2307 | HEADING_SKIP, W_TABLE_EXPECTED_NOT_EXTRACTED, W_MULTICOLUMN_ORDER (Issue #54 update) |
| pdf.cropped_rotated | 0 | RISKY | 61 | 3 | 4 | 232 | LOW_TEXT_DENSITY |
| docx | 0 | OK | 83 | 5 | 1 | 152 | — |
| pptx | 0 | OK | 80 | 3 | 3 | 83 | — |
| xlsx | 0 | HIGH | 85 | 20 | 2 | 4599 | — |
| html | 0 | HIGH | 87 | 4 | 1 | 95 | — |
| csv | 0 | HIGH | 90 | 2 | 1 | 169 | — |
| zip | 0 | RISKY | 68 | 6 | 1 | 171 | — |
| txt | 0 | HIGH | 93 | 1 | 1 | 147 | — |
| md | 0 | HIGH | 95 | 5 | 1 | 215 | — |
| json | 0 | HIGH | 88 | 2 | 1 | 582 | — |
| xml | 0 | OK | 80 | 8 | 1 | 106 | HEADING_HIERARCHY |

## Phase 4 — Fidelity evaluation

Detailed inspection of `document.md` for the concerning cases:

### Reading order

- **`pdf.multicolumn` (multicolumn.pdf), band HIGH, score 85, tokens 2307** — Headings survive ("## Two-Column", "### Abstract", "## Document with Lorem Ipsum"). Body is interleaved between columns: "This is a sample document with with Lorem Ipsum text. Lorem ipsum dolor sit amet, iscing elit." — "iscing elit" is the tail of "adipiscing elit" spliced from the second column mid-word. `HEADING_SKIP` and `W_TABLE_EXPECTED_NOT_EXTRACTED` fire. **Update (Issue #54)**: the interleaving is no longer silent — `W_MULTICOLUMN_ORDER` now fires on this document as an informational (candidate) signal. On the pages where the parser previously used a false-supported single-line right-side cluster to force column-first span sorting, that phantom re-ordering is gone; the block sequence surfaces the true interleaving to the validator. Readiness/band stay HIGH (the warning is zero-penalty) but the defect is auditable via `warning_codes`.

- **`pdf.multi_page` (pdflatex-4-pages.pdf), band RISKY, score 65** — 4 pages, 529 tokens (was 619 pre-Issue-#54). Visual layout is plain single-column `\blindtext` with a centered page number at the bottom. **Update (Issue #54)**: `W_MULTICOLUMN_ORDER` is gone (the previous fire was a false positive driven by the centered page number synthesising a phantom column). The compiled Markdown is now in correct single-column reading order. The block-level de-duplication step then correctly identifies that this document is 4 repetitions of the same `\blindtext` passage and removes the repeats; the exposed content-repetition is signalled by MISSING_PAGE (2 of 4 pages), which drops the band from OK 81 to RISKY 65 in an honest, not-arbitrary way — the score reflects the actual content coverage. Nothing about the scoring formula or `SCORING_POLICY_VERSION` changed.

### Tables

- Neither the curated subset nor the public corpus includes a scanned-table PDF that would exercise Marker table recovery. `pdf.large_prose` fires the experimental `W_TABLE_EXPECTED_NOT_EXTRACTED` warning on 117 pages of prose — likely false-safe rate consistent with the locked v3 detector (P=0.613 R=0.328) documented in `EXPECTATION_VALIDATION_REPORT_V3.md`.

### OCR

- **Tesseract binary is not installed on this workstation.** `pdf.image_only` correctly emits `NEAR_EMPTY_OUTPUT` + `LOW_TEXT_DENSITY` and drops to POOR band 41 — the accurate expected behaviour when OCR is unavailable. `pdf.rotated` (habibi-rotated.pdf, whose pages 2–4 are the Arabic body scanned/rasterised) similarly drops to POOR 41. Both fire `NEAR_EMPTY_OUTPUT` — signal correctly reflects that content is missing.
- With Tesseract present, these documents would traverse the OCR fallback. **Not exercised in this run.**

### Formulas

- **pix2tex not installed.** Neither multicolumn.pdf nor GeoTopo.pdf triggered a visible formula-fallback path in the output. No BlockType.MATH blocks emitted in the tested subset. Docx-side OMML→LaTeX conversion is exercised by existing unit tests (`tests/test_parsers/test_docx_math.py`, 40 tests).

### Marker / vision

- `marker-pdf 1.10.2`, `surya-ocr 0.17.1`, `torch 2.12.1+cu126`, CUDA GPU all installed. **Marker was not selected in this run** because the PDFs in the subset either have native text layers or trigger the OCR path with Tesseract absent — Marker only activates for image-heavy pages when explicitly wired (see `_get_marker_models()` in `aksharamd/plugins/parsers/pdf.py:57`, guarded by `_MARKER_LOAD_ATTEMPTED` sentinel that requires the 3 GB model download on first use).
- End-to-end Marker exercise deferred to CI (see Phase 8 follow-ups).

### Silent-drop of embedded content

- **`pdf.attachment` (with-attachment.pdf), band HIGH, score 87, warnings `[W_PDF_ATTACHMENT_IGNORED]`** — Output is just the primary page's Lorem Ipsum text. The embedded file attachment is still not extracted, but the omission is no longer silent: as of Issue #51 the parser emits `W_PDF_ATTACHMENT_IGNORED` (maturity `candidate`, penalty 0) with count-only metadata (`attachment_count=1`, `backend="pymupdf"`). Downstream consumers can now see that the compiled output is not attachment-complete without opening the source PDF. Extraction of the payload itself remains a separate follow-up.

## Phase 5 — Ground-truth comparison

The public corpus in `benchmarks/.public_corpus/` does not ship formal per-document ground truth for text coverage, reading order, or table cell retention. Manual inspection was performed for the concerning cases (§Phase 4). Automated ground-truth scoring is deferred to Issue #43-A (see §Phase 8).

## Phase 6 — Named regression review

| Case | Asset available? | Reproduced? | Evidence used |
|---|---|---|---|
| `text_dense__de` | **No** — external ParseBench corpus | Not reproduced | Historical: `benchmarks/EXPECTATION_VALIDATION_REPORT_V3.md`, `benchmarks/expectation_detector_lock_v3.json` |
| Japanese extraction case | **No** | Not reproduced | Same |
| `letter3` | **No** | Not reproduced | Same |
| `myctophidae` | **No** | Not reproduced | Same |
| `simple2` | **No** | Not reproduced | Same |
| `strikeUnderline` | **No** | Not reproduced | Same |

**All six named cases require the external ParseBench binary corpus.** This validation cannot claim reproduction. Per the task explicit instruction, they are recorded here as **unavailable**, not silently marked "still passing" via historical evidence.

**Recommended next step**: pin a snapshot of the ParseBench binary corpus into a CI-only S3/artifact location and run the advanced-fidelity workflow against it on a scheduled trigger. Tracked as a Phase-8 follow-up.

## Phase 7 — Findings classification

### F1 — Silent-fidelity defect: multicolumn PDFs score HIGH despite column interleaving

- **Reproducer**: `benchmarks/.public_corpus/pdf/026-latex-multicolumn/multicolumn.pdf`
- **Post-Issue-#54 output**: band HIGH, score 85, 2307 tokens, warnings `HEADING_SKIP` + `W_TABLE_EXPECTED_NOT_EXTRACTED` + `W_MULTICOLUMN_ORDER` (candidate, penalty 0).
- **Actual damage**: left- and right-column sentences interleaved mid-word ("iscing elit", "abitur auctor", etc.) — still present at the span level; the parser's block-level output on p2/p3 is now clearly interleaved after Issue #54 removed the phantom column boundary that previously produced accidentally column-first block ordering.
- **Warning surface (Issue #54 update)**: `W_MULTICOLUMN_ORDER` **now fires** on this document because the block sequence is no longer disguised by a false column split. Readiness stays HIGH because the warning is zero-penalty; a scoring calibration that lowers the band on documents where `W_MULTICOLUMN_ORDER` + column-interleaved output coincide is a separate follow-up.
- **Classification**: **detected but uncalibrated silent-fidelity defect**. The signal is now visible in `warning_codes`; the score does not yet reflect it.
- **Follow-up**: Issue #50 tracks broader multicolumn detection improvement (span-level recall) and scoring calibration. Issue #54's charter was warning precision on `pdflatex-4-pages.pdf`; the multicolumn.pdf change here is a natural side effect of correcting the parser's phantom-boundary bug.

### F2 — Detected omission with candidate warning: embedded PDF attachments not extracted

- **Reproducer**: `benchmarks/.public_corpus/pdf/025-attachment/with-attachment.pdf`
- **Status (updated 2026-07-18, Issue #51)**: post-change behaviour is band HIGH, score 87, warnings `["W_PDF_ATTACHMENT_IGNORED"]`, informational deduction `{rule_id: "W_PDF_ATTACHMENT_IGNORED", penalty: 0, maturity: "candidate"}`. Metadata is count-only (`attachment_count=1`, `backend="pymupdf"`, `warning_maturity="candidate"`) — no filenames, no bytes, no paths.
- **Baseline (before fix)**: band HIGH, score 87, no warnings — the omission was silent.
- **What still is not done**: the attachment payload itself remains unextracted. This PR is warning-only. The compiled `document.md` and blocks still describe only the primary page content; consumers must not assume the compiled output contains attachment contents.
- **Scope boundary**: adding an extraction path (unpacking the payload into blocks or assets) is out of scope for #51 and is tracked separately. A scoring calibration that lowers the band on attachment-bearing PDFs is likewise out of scope; the current maturity is `candidate` and any penalty change belongs in a dedicated calibration PR.
- **Follow-up**: (superseded) — issue #51 closes the visibility gap; a separate ticket will cover extraction and/or scoring calibration.

### F3 — Environment gap: Tesseract binary missing → OCR path is not testable end-to-end on this workstation

- **Impact**: `pdf.image_only` and `pdf.rotated` (habibi rotated body pages) drop to POOR / NEAR_EMPTY_OUTPUT — which IS the correct signal when OCR is unavailable. But the true OCR fidelity cannot be measured here.
- **Classification**: **environment/dependency limitation**.
- **Recommended next step**: Provision Tesseract on the CI runner used for advanced fidelity; extend the run.

### F4 — Environment gap: pix2tex not installed → math visual-fallback not testable

- **Classification**: environment/dependency limitation.
- **Recommended next step**: Install pix2tex + fetch its model cache in CI (add to `[math]` extras verification job).

### F5 — Environment gap: Marker (`[vision]`) 3 GB model cache not pre-populated

- **Classification**: environment/dependency limitation.
- **Recommended next step**: Cache Marker models in the CI runner; add a dedicated advanced-fidelity job that exercises the vision path against a scanned-table fixture.

### F6 — Corpus gap: named ParseBench regression cases are not in this repository

- **Classification**: **benchmark gap**.
- **Recommended next step**: Track the ParseBench binary corpus in a CI-accessible artifact store; run advanced fidelity against it on a scheduled cadence.

### F7 — Known experimental behaviour: `W_TABLE_EXPECTED_NOT_EXTRACTED` (experimental v3 lock) fires on long-form prose (117-page GeoTopo.pdf)

- **Classification**: **known experimental limitation**. The v3 detector is locked at P=0.613 R=0.328 with penalty=0 per `benchmarks/EXPECTATION_VALIDATION_REPORT_V3.md`. False-safes on prose-heavy docs are documented.
- **No action** required in this PR.

### F8 — Detector false positive, parser root cause: `W_MULTICOLUMN_ORDER` on `pdflatex-4-pages.pdf`

- **Classification (updated Issue #54)**: **parser-level false positive**, root-caused and fixed at the parser layer.
- **Visual layout**: all four pages are single-column `\blindtext` running paragraphs (standard LaTeX `article` class, centered page number at bottom). Confirmed by rendering.
- **Root cause**: the PDF parser's `_detect_column_boundaries()` collected the *set* of rounded line-start x-values, so each unique value contributed equally to boundary detection regardless of how many lines supported it. On these pages, ~30 body lines cluster at rel x ≈ 0.15 (the left margin) and **one** centered page number sits at rel x ≈ 0.50. The lone outlier synthesised a phantom column boundary at rel ≈ 0.33, which then reordered spans via the parser's `(column, y)` sort — corrupting the compiled Markdown for pdflatex-4-pages.pdf and, ironically, *masking* the true column interleaving on multicolumn.pdf p2/p3 by producing accidentally column-first block ordering there.
- **Fix (this PR)**: `_detect_column_boundaries()` now requires each candidate cluster to have at least `_MIN_LINES_PER_COLUMN_CLUSTER = 2` supporting lines. Threshold chosen from corpus evidence, not tuned to one fixture — the 3colpres analogue (minority cluster of exactly 2) still detects; a lone page number at any position is rejected.
- **Downstream effects (all corpus-verified)**:
  - `pdflatex-4-pages.pdf`: no phantom column, single-column reading order restored, `W_MULTICOLUMN_ORDER` no longer fires. The block-level de-dup then correctly identifies the intentional `\blindtext` repetition across pages and drops the repeats; MISSING_PAGE (2 of 4) fires as the honest signal. Score 65 / band RISKY.
  - `multicolumn.pdf`: no change on p1 (boundary was densely supported and still detects). Boundaries on p2/p3 are removed because their "right cluster" was itself a single stray line — under the old rule that stray line was the *only* thing pushing the parser toward column-first sorting; without it the span sort is y-first, exposing the interleaving to the block-level validator. `W_MULTICOLUMN_ORDER` now fires (candidate, penalty 0). Score/band unchanged.
  - Seven other corpus documents (001, 003, 006, 011, 025, 027, 009): score and warnings identical to pre-fix.
- **Preserved test coverage**: `test_two_column_layout_detected` (dense two-column arXiv-style), `test_interleaved_warns` and `test_trans_030_warns` on the validator side, and four new parser tests (`test_lone_centered_footer_line_does_not_synthesise_boundary`, `test_lone_left_outlier_does_not_synthesise_boundary`, `test_two_supported_clusters_still_produce_boundary`, `test_lone_outlier_ignored_but_dense_boundary_preserved`).
- **Scoring immutability**: nothing under `aksharamd/scoring/` was touched; `SCORING_POLICY_VERSION` remains `"1.0"`; no readiness penalty or band threshold changed. The pdflatex-4-pages band drop from OK 81 → RISKY 65 is a downstream consequence of the parser fix revealing legitimate content repetition, not a scoring formula change.

## Phase 8 — Narrow fixes

**This document was authored during the original 2026-07-18 validation run and has since been amended by Issue #51 (F2) and Issue #54 (F8, and the F1 warning-visibility update).** Findings not yet acted on:
- large architectural work on span-level multicolumn recall (F1) — tracked by Issue #50,
- environment/dependency limitations (F3–F6) outside this repository's scope,
- detector-calibration questions (F7) for a Phase-2-style scoring change.

Per the task explicit instruction: "Do not implement large fixes during the initial validation. Record large or ambiguous changes as separate issues instead."

## Reproducibility

The full baseline run is reproducible from `tests/test_advanced_fidelity_baseline.py`. Point at the installed CLI via `AKSHARAMD_E2E_BINARY=<path>` or have `aksharamd` on PATH; the test skips cleanly otherwise.

Direct command reproducing the exact 26-document run:

```
python -m pytest tests/test_advanced_fidelity_baseline.py -v
```

Machine-readable per-document evidence: `benchmarks/ADVANCED_FIDELITY_2026-07-18.json`.

## Files changed by this validation

- `benchmarks/ADVANCED_FIDELITY_2026-07-18.md` (this report)
- `benchmarks/ADVANCED_FIDELITY_2026-07-18.json` (machine-readable per-document evidence)
- `tests/test_advanced_fidelity_baseline.py` (validation harness — smoke + regression on the observed baseline)

**Zero production code changed.** `aksharamd/scoring/`, `aksharamd/plugins/parsers/`, `aksharamd/packaging/`, `aksharamd/models/`, and `aksharamd/cli.py` are all untouched.

## Deferred follow-up issues

To be opened after this PR merges:

1. **Silent-fidelity: multicolumn PDFs score HIGH despite column interleaving** (F1). Track under umbrella of Multi-column reading order calibration.
2. **Detected omission: PDF attachments** (F2). Landed in Issue #51 — candidate warning `W_PDF_ATTACHMENT_IGNORED` now fires with count-only metadata; readiness score/band unchanged pending calibration.
3. **Environment/CI: exercise the OCR (`[ocr]`), vision (`[vision]`), and math (`[math]`) paths end-to-end in CI** (F3, F4, F5). Extend the `Extras (*)` jobs or add a scheduled `advanced-fidelity` workflow.
4. **Benchmark corpus: pin ParseBench binary corpus for reproducible regression** (F6).
5. **Detector calibration: `W_MULTICOLUMN_ORDER` false-positive on `pdflatex-4-pages.pdf`** (F8).
