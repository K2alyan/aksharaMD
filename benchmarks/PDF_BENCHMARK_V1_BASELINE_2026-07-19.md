# AksharaMD PDF Benchmark v1 — Phase 1 baseline (2026-07-19)

**Commit under evaluation:** `cb1a5cd7587c2354a581e29e37ef0e5a2df4fc05`
**AksharaMD version:** `0.3.6`
**Python:** 3.12.2 · **Platform:** Windows-11-10.0.26200-SP0

**No production code changes.** No parser, validator, scoring, warning-penalty, or `SCORING_POLICY` modifications. This is Phase 1 of the AksharaMD PDF Benchmark v1 milestone (Issue #68) — AksharaMD alone, no competitor adapters.

## Corpus

- Total assets: **46**
- Eligible: **45**
- By corpus source: {'public': 34, 'parsebench': 12}
- By document class: {'native-text': 19, 'image-only': 13, 'malformed': 3, 'multilingual': 4, 'multicolumn': 7}

Manifest artifact: `benchmarks/pdf_benchmark_v1_manifest.json`.

## Overall metrics

| Metric | Value |
|---|---:|
| Files evaluated | 45 |
| Parse success | 45 / 45 (100.0 %) |
| Runtime mean (s) | 10.461 |
| Runtime p50 (s) | 1.84 |
| Runtime p95 (s) | 30.915 |
| Tokens mean | 1903 |
| Output-size inflation (chars per PDF byte) | 0.0313 |
| Deterministic rate | None |
| OCR-required files | 0 |
| Missing-pages files | 0 |
| Multicolumn-warning files | 5 |
| Repeat-content mean ratio | 0.1796 |

### Quality-band distribution (overall)

- **HIGH**: 21
- **OK**: 5
- **POOR**: 3
- **RISKY**: 16

### Warning-code distribution (top 15)

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

## By corpus source
### parsebench

- Files: 12, Success: 12 (100.0%)
- Runtime mean/p50/p95 (s): 11.287 / 1.649 / 43.78
- Tokens mean/p50: 896 / 947
- Deterministic rate: None
- OCR-required: 0 · Missing pages: 0 · Multicolumn-warn: 2
- Quality bands: {'HIGH': 9, 'OK': 3}

### public

- Files: 33, Success: 33 (100.0%)
- Runtime mean/p50/p95 (s): 10.16 / 13.494 / 24.076
- Tokens mean/p50: 2270 / 37
- Deterministic rate: None
- OCR-required: 0 · Missing pages: 0 · Multicolumn-warn: 3
- Quality bands: {'HIGH': 12, 'RISKY': 16, 'OK': 2, 'POOR': 3}

## By document class
### image-only

- Files: 13, Success: 13 (100.0%)
- Runtime mean (s): 19.284, tokens mean: 138
- OCR-required: 0, missing pages: 0
- Quality bands: {'HIGH': 5, 'RISKY': 6, 'POOR': 2}

### malformed

- Files: 2, Success: 2 (100.0%)
- Runtime mean (s): 7.782, tokens mean: 23
- OCR-required: 0, missing pages: 0
- Quality bands: {'RISKY': 1, 'HIGH': 1}

### multicolumn

- Files: 7, Success: 7 (100.0%)
- Runtime mean (s): 1.634, tokens mean: 985
- OCR-required: 0, missing pages: 0
- Quality bands: {'HIGH': 5, 'OK': 2}

### multilingual

- Files: 4, Success: 4 (100.0%)
- Runtime mean (s): 13.588, tokens mean: 254
- OCR-required: 0, missing pages: 0
- Quality bands: {'HIGH': 1, 'RISKY': 2, 'POOR': 1}

### native-text

- Files: 19, Success: 19 (100.0%)
- Runtime mean (s): 7.299, tokens mean: 3994
- OCR-required: 0, missing pages: 0
- Quality bands: {'OK': 3, 'HIGH': 9, 'RISKY': 7}

## Failures

No parse failures across 45 files.

## Highest-impact failure classes (rule-based)

- repeat_content_over_10pct: 11 / 45 (24.4%)
- heading_skip_signal: 9 / 45 (20.0%)
- multicolumn_order_warning: 5 / 45 (11.1%)
- table_missing_signal: 4 / 45 (8.9%)
- ocr_required: 0 / 45 (0.0%)
- missing_pages: 0 / 45 (0.0%)
- parse_failure: 0 / 45 (0.0%)

## Constraints observed

- No parser / validator / scoring / warning-penalty / packaging / model code changed.
- `SCORING_POLICY_VERSION` remains `"1.0"`.
- No PDF bytes added to git. Public corpus lives at `benchmarks/.public_corpus/pdf/**`; ParseBench PDFs live in the local cache outside the repo.
- Deterministic result ordering (assets sorted by id).
- No network fetch during benchmark execution.
- Per-file errors preserved; single failures do not abort the run.

## Human-reviewed quality (scaffold)

Rule-based fidelity signals above are captured automatically. Human-reviewed quality per file (correct / usable-with-minor-defects / materially-damaged / unusable) has not been executed for this Phase 1 baseline — it appears here as a scaffold column for future reviewers. A subsequent PR under the umbrella issue will land the reviewer-graded ratings.

## Next steps

- Phase 2: competitor adapters (MarkItDown, Docling, Unstructured, PyMuPDF4LLM) — one PR each with pinned versions.
- Phase 3: comparison report — strengths by document class, no universal-winner declaration.