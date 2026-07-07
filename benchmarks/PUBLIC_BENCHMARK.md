# AksharaMD Public Reproducible Benchmark

## What This Is

A reproducible parser-coverage benchmark for AksharaMD, built from two sources:

- **34 real PDFs** from [py-pdf/sample-files][sample-files] (CC-BY-SA-4.0) — measures
  PDF robustness across generators, page counts, encodings, forms, and edge cases.
- **100 programmatically generated files** — 10 variants per format across 10 formats —
  measures coverage, consistency, and regression detection for non-PDF parsers.

**Total corpus: 134 files.** Everything can be re-run from scratch by anyone with an
internet connection and the AksharaMD dependencies installed.

## What It Measures

| Metric | Recorded |
| --- | --- |
| Parser success / failure | Yes |
| Expected failures (e.g. encrypted PDFs) | Yes — flagged separately |
| Block count and types | Yes |
| Output character count | Yes |
| Estimated token count | Yes (chars / 4) |
| Elapsed time per file | Yes |
| Warnings and error codes | Yes |
| Readiness score | Yes (if available) |

## What It Does **Not** Measure

- Answer correctness or LLM judge scores
- RAG faithfulness or citation accuracy
- Semantic agent performance
- Comparison against other tools
- Parsing quality of scanned/OCR documents (no OCR fixtures in this corpus)

For LLM-judge quality comparisons against MarkItDown, Docling, etc., see
`LLM_QA_BENCHMARK.md`. For ParseBench semantic parsing comparisons, see
`PARSEBENCH_ADAPTER.md`.

## Corpus

### PDF subset — py-pdf/sample-files (CC-BY-SA-4.0)

All 34 available files, selected to cover:

| Category | Representative Files |
| --- | --- |
| Minimal / trivial | pdf-001, pdf-002 |
| Multi-page (3–117 pages) | pdf-004, pdf-006, pdf-008, pdf-015, pdf-017, pdf-025, pdf-027, pdf-028 |
| Forms | pdf-011, pdf-013, pdf-014 |
| Images (various encodings) | pdf-003, pdf-007, pdf-009, pdf-010, pdf-020, pdf-021, pdf-022, pdf-023, pdf-024 |
| Generator diversity | pdfTeX, LibreOffice, ReportLab, Google Docs, FPDF2, pdfkit, Ghostscript, pypdf |
| RTL / non-Latin text | pdf-016, pdf-017, pdf-029 |
| Large academic PDF (117 pp) | pdf-027, pdf-028 |
| Encrypted (expected failure) | pdf-005 |
| Complex geometry (rotation, crop, scale) | pdf-033 |
| Unusual metadata | pdf-019 |

Source: https://github.com/py-pdf/sample-files — License: CC-BY-SA-4.0

### Synthetic formats

100 files total — 10 variants per format:

| Format | Variants | Variant themes |
| --- | --- | --- |
| DOCX | 10 | headings, tables, unicode, lists, long-text, metadata, nested-headings, mixed, minimal |
| XLSX | 10 | single-sheet, multi-sheet, empty-cells, numeric, large-table, strings, dates, unicode, wide-headers, minimal |
| PPTX | 10 | title-content, multi-slide, with-table, bullets, unicode, mixed-layouts, long-text, minimal, title-only, content-heavy |
| HTML | 10 | headings, tables, lists, unicode, nested-divs, metadata-tags, code-blocks, links-images, empty-body, minimal |
| CSV | 10 | simple, unicode, empty-cells, large-50rows, single-column, quoted-fields, numeric, mixed-types, header-only, minimal |
| JSON | 10 | flat-object, nested, array-of-objects, deeply-nested, unicode, null-values, large-array, mixed-types, empty-object, minimal |
| XML | 10 | simple, nested, attributes, unicode, mixed-content, deep-nesting, namespaced, cdata, empty-elements, minimal |
| TXT | 10 | paragraphs, headings-sections, unicode, long-text, lists, minimal, ascii-table, code-like, mixed-sections, whitespace-heavy |
| MD | 10 | full-document, tables, code-blocks, unicode, nested-lists, headings-h1-h6, links-images, blockquotes, minimal, long-text |
| ZIP | 10 | mixed-formats, single-text, nested-dirs, many-small-10, unicode-filenames, large-text, source-code-tree, config-files, empty-mixed, minimal |
| **Total** | **100** | |

## Run Modes

### Smoke mode (20 files — fast verification)

10 representative PDFs + 1 variant per format. Completes in seconds. Suitable for CI
and quick regression checks.

### Full mode (134 files — complete benchmark)

All 34 PDFs + 10 variants per format. Takes 1–5 minutes depending on network speed
for PDF downloads. Suitable for release validation and public reporting.

## How To Run

### 1. Install dependencies

```bash
pip install aksharamd[all]  # or: pip install -e ".[all]"
```

Optional format-specific deps: `python-docx`, `openpyxl`, `python-pptx`

### 2. Build corpus

**Full corpus (134 files):**
```bash
python benchmarks/build_public_corpus.py
```

**Smoke subset (20 files, fast):**
```bash
python benchmarks/build_public_corpus.py --smoke
```

**Skip PDF downloads (synthetic only):**
```bash
python benchmarks/build_public_corpus.py --skip-pdf
```

**Limit download size (skip large PDFs):**
```bash
python benchmarks/build_public_corpus.py --max-download-mb 50
```

### 3. Run benchmark

**Full run (134 files):**
```bash
python benchmarks/run_public_benchmark.py
```

**Smoke run (20 files):**
```bash
python benchmarks/run_public_benchmark.py --smoke
```

**Limit PDF count:**
```bash
python benchmarks/run_public_benchmark.py --max-pdfs 10
```

Writes two files to `benchmarks/results/`:
- `public_benchmark_<timestamp>.jsonl` — one JSON record per file
- `public_benchmark_<timestamp>.md` — human-readable summary table

### 4. Inspect results

```bash
# Quick summary:
python -c "
import json, sys
from pathlib import Path
f = sorted(Path('benchmarks/results').glob('public_benchmark_*.jsonl'))[-1]
rows = [json.loads(l) for l in f.read_text().splitlines()]
ok = sum(1 for r in rows if r['outcome'] == 'success')
print(f'{ok}/{len(rows)} succeeded')
"
```

## Interpreting Results

- **success** — AksharaMD produced a Document with at least one block.
- **error (expected)** — The file was expected to fail (e.g. encrypted PDF); this is
  correct behavior, not a regression.
- **error (unexpected)** — An error that was not anticipated; worth investigating.
- **skipped** — File was not found in `.public_corpus/`; run `build_public_corpus.py`.

## Re-running on a New AksharaMD Version

The corpus is stable. To track regressions across releases, compare JSONL outputs:

```bash
# Compare two runs:
python -c "
import json
from pathlib import Path
results = sorted(Path('benchmarks/results').glob('public_benchmark_*.jsonl'))
if len(results) < 2:
    print('Need at least two runs to compare')
else:
    a = {r['id']: r for l in results[-2].read_text().splitlines() for r in [json.loads(l)]}
    b = {r['id']: r for l in results[-1].read_text().splitlines() for r in [json.loads(l)]}
    regressions = [id for id in a if a[id]['outcome'] == 'success' and b.get(id, {}).get('outcome') != 'success']
    print('Regressions:', regressions or 'none')
"
```

## License

PDF files: CC-BY-SA-4.0 (py-pdf/sample-files).
Synthetic files: no license restrictions (generated, no original authorship).
Benchmark code: same license as AksharaMD (see repository root).

[sample-files]: https://github.com/py-pdf/sample-files
