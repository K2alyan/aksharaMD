# Advanced Fidelity Validation — 2026-07-18

Validation run of AksharaMD `main` at `29dbb9d` against the advanced document paths that were **not** exercised by the base-install E2E validation in PR #40. Companion machine-readable file: [`ADVANCED_FIDELITY_2026-07-18.json`](./ADVANCED_FIDELITY_2026-07-18.json).

## Executive verdict

**Works end-to-end for the base install with two silent-fidelity concerns and one large architectural gap.**

- 25 of 26 documents in the curated subset compiled with valid output; the one non-zero exit was an intentionally-encrypted PDF that correctly raised `ENCRYPTED_PDF` and refused.
- Readiness scoring accurately signalled degradation on **6 of 8** documents where content was lost or heavily reduced.
- **Two documents scored HIGH while losing material content**: the multicolumn LaTeX PDF (reading order silently interleaved from both columns) and the PDF-with-attachment (embedded file dropped without any signal). Both are recorded as follow-up issues, not fixed in this PR.
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
| pdf.multi_page | 0 | OK | 81 | 2 | 4 | 619 | W_MULTICOLUMN_ORDER |
| **pdf.encrypted** | **1** | – | – | – | – | – | ENCRYPTED_PDF (intentional failure) |
| pdf.outline | 0 | OK | 81 | 3 | 4 | 1275 | — |
| pdf.image_only | 0 | POOR | 41 | 1 | 6 | 106 | NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY |
| pdf.large_prose (117 pages) | 0 | HIGH | 87 | 155 | 117 | 60104 | W_TABLE_EXPECTED_NOT_EXTRACTED (experimental, penalty=0) |
| pdf.form | 0 | RISKY | 67 | 1 | 1 | 20 | LOW_TEXT_DENSITY |
| pdf.arabic | 0 | RISKY | 67 | 1 | 1 | 8 | LOW_TEXT_DENSITY |
| pdf.rotated | 0 | POOR | 41 | 1 | 4 | 61 | NEAR_EMPTY_OUTPUT, LOW_TEXT_DENSITY |
| pdf.annotations | 0 | RISKY | 67 | 1 | 1 | 11 | LOW_TEXT_DENSITY |
| **pdf.attachment** | **0** | **HIGH** | **87** | 1 | 1 | 171 | **—** |
| **pdf.multicolumn** | **0** | **HIGH** | **85** | 9 | 3 | 2307 | HEADING_SKIP, W_TABLE_EXPECTED_NOT_EXTRACTED |
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

- **`pdf.multicolumn` (multicolumn.pdf), band HIGH, score 85, tokens 2307** — Headings survive ("## Two-Column", "### Abstract", "## Document with Lorem Ipsum"). **Body is silently interleaved between columns**: "This is a sample document with with Lorem Ipsum text. Lorem ipsum dolor sit amet, iscing elit." — "iscing elit" is the tail of "adipiscing elit" spliced from the second column mid-word. Multiple paragraphs show the same left/right splice pattern. `HEADING_SKIP` and `W_TABLE_EXPECTED_NOT_EXTRACTED` fire, but neither describes the column-interleaving damage. **Silent-fidelity defect: readiness overstates usable quality on multicolumn LaTeX PDFs.**

- **`pdf.multi_page` (pdflatex-4-pages.pdf), band OK, score 81** — 4 pages, 619 tokens. `W_MULTICOLUMN_ORDER` fires (correct — this fixture appears to be identified as multicolumn even though its intent was 4-page single-column). Signal accurate; content extracted.

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

- **`pdf.attachment` (with-attachment.pdf), band HIGH, score 87** — Output is just the primary page's Lorem Ipsum text. The **embedded file attachment is not surfaced anywhere** in the document.md, block metadata, or manifest. No warning code fires to signal that content was dropped. **Silent-fidelity defect: PDFs with embedded file attachments compile as HIGH readiness with no signal that the attachment was ignored.**

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
- **Current output**: band HIGH, score 85, 2307 tokens, warnings `HEADING_SKIP` + `W_TABLE_EXPECTED_NOT_EXTRACTED`
- **Actual damage**: left- and right-column sentences interleaved mid-word ("iscing elit", "abitur auctor", etc.)
- **User impact**: RAG / retrieval pipelines relying on `--min-readiness-score 70` will ingest scrambled multicolumn text as production-quality content.
- **Warning gap**: `W_MULTICOLUMN_ORDER` exists in the scoring allowlist (`readiness.py:509`) but does not fire on this document.
- **Classification**: **severe silent-fidelity defect** + **readiness/warning defect**.
- **Narrow fix available?** No. The correct fix is Phase-2-style calibration of `W_MULTICOLUMN_ORDER` sensitivity + potentially a new `W_COLUMN_INTERLEAVED` code. That is a scoring-surface change requiring calibration data, not a narrow parser change.
- **Follow-up**: File as separate issue (see §Delivery).

### F2 — Silent-fidelity defect: embedded PDF attachments dropped without signal

- **Reproducer**: `benchmarks/.public_corpus/pdf/025-attachment/with-attachment.pdf`
- **Current output**: band HIGH, score 87, no warnings
- **Actual damage**: the embedded attachment is not extracted; no manifest entry, no block, no note in document.md, no warning code.
- **User impact**: users compiling document sets that include cover-page + attached primary content receive only the cover page, with HIGH readiness misleading them into believing extraction was clean.
- **Classification**: **severe silent-fidelity defect** + **readiness/warning defect**.
- **Narrow fix available?** Possibly — a `W_PDF_ATTACHMENT_IGNORED` informational warning with penalty=0 could be added at the point where `pymupdf` detects `/EmbeddedFiles` in the catalogue. But it's a new signal that would need calibration, so best done as a follow-up rather than in this validation PR.
- **Follow-up**: File as separate issue.

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

### F8 — Documentation gap: `pdf.multi_page` (pdflatex-4-pages.pdf) fires `W_MULTICOLUMN_ORDER` on what appears to be a single-column 4-page document

- **Classification**: **readiness/warning defect** (potential false-positive on the `W_MULTICOLUMN_ORDER` detector).
- **Confidence**: low. This requires visual inspection of the actual PDF to determine whether the document is genuinely multicolumn or the detector fires spuriously.
- **Follow-up**: File as separate issue for detector calibration.

## Phase 8 — Narrow fixes

**No parser or scoring code changes are included in this PR.** All findings above are either:
- large architectural work (F1, F2) requiring calibration and new warning-code registration, best tracked separately,
- environment/dependency limitations (F3–F6) outside this repository's scope, or
- detector-calibration questions (F7, F8) for a Phase-2-style scoring change.

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
2. **Silent-fidelity: PDF attachments dropped without a warning** (F2). New candidate warning code `W_PDF_ATTACHMENT_IGNORED` proposed.
3. **Environment/CI: exercise the OCR (`[ocr]`), vision (`[vision]`), and math (`[math]`) paths end-to-end in CI** (F3, F4, F5). Extend the `Extras (*)` jobs or add a scheduled `advanced-fidelity` workflow.
4. **Benchmark corpus: pin ParseBench binary corpus for reproducible regression** (F6).
5. **Detector calibration: `W_MULTICOLUMN_ORDER` false-positive on `pdflatex-4-pages.pdf`** (F8).
