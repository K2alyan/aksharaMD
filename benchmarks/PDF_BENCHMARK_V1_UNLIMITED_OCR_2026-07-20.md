# PDF Benchmark v1 — Unlimited-OCR (first pass, 2026-07-20)

**Assets attempted:** 45  ·  **PASS:** 42  ·  **FAIL:** 3
**Timeouts:** 0  ·  **OOM:** 2  ·  **Other exceptions:** 1
**Hallucination flags:** 3

## Runtime (per document, from the reporting-revision plan update)

- Median elapsed: 13.16 s
- p95 elapsed: 299.55 s
- Median s/page: 8.38
- p95 s/page: 74.888

## Per document class

| Class | n | PASS | Runtime p50 (s) | Runtime p95 (s) | s/page p50 | s/page p95 |
|---|---:|---:|---:|---:|---:|---:|
| image-only | 13 | 13 | 3.23 | 62.36 | 3.19 | 62.36 |
| malformed | 2 | 2 | 3.34 | 7.07 | 3.34 | 7.07 |
| multicolumn | 7 | 7 | 35.36 | 93.3 | 31.1 | 58.81 |
| multilingual | 4 | 4 | 6.15 | 54.41 | 1.538 | 54.41 |
| native-text | 19 | 16 | 13.16 | 299.55 | 7.68 | 74.888 |

## Slowest five assets

| Asset | Class | Pages | Elapsed (s) | s/page | Peak reserved MiB | Status |
|---|---|---:|---:|---:|---:|---|
| `public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf` | native-text | 4 | 1048.05 | 262.012 | 7668 | PASS |
| `parsebench/japanese_case` | image-only | 1 | 507.96 | 507.96 | 7076 | PASS |
| `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` | native-text | 4 | 299.55 | 74.888 | 7666 | PASS |
| `public/026-latex-multicolumn/multicolumn.pdf` | multicolumn | 3 | 93.3 | 31.1 | 7522 | PASS |
| `public/006-pdflatex-outline/pdflatex-outline.pdf` | native-text | 4 | 73.63 | 18.407 | 7666 | PASS |

## Failures

| Asset | Category | Elapsed (s) | Runner healthy after | Exception |
|---|---|---:|:-:|---|
| `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` | oom | 65.25 | yes | infer_failed: OutOfMemoryError: CUDA out of memory. Tried to allocate 19.01 GiB. GPU 0 has a total c |
| `public/009-pdflatex-geotopo/GeoTopo.pdf` | oom | 58.97 | yes | infer_failed: OutOfMemoryError: CUDA out of memory. Tried to allocate 19.01 GiB. GPU 0 has a total c |
| `public/017-unreadable-meta-data/unreadablemetadata.pdf` | other_exception | 0.01 | yes | infer_failed: AssertionError: image_files must be a non-empty list for multi-image inference! |

## Cold load

- **elapsed_seconds:** 18.53
- **rss_before_mib:** 512
- **rss_after_mib:** 906
- **peak_allocated_mib_load:** 6456
- **peak_reserved_mib_load:** 6490
