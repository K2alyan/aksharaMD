# PDF Benchmark v1 — Unlimited-OCR (pass 2, 2026-07-20)

**Assets attempted:** 45  ·  **PASS:** 42  ·  **FAIL:** 3
**Timeouts:** 0  ·  **OOM:** 2

## Runtime

- Median: 16.15 s  ·  p95: 336.31 s
- Median s/page: 8.74  ·  p95: 84.078

## Per document class

| Class | n | PASS | Runtime p50 (s) | Runtime p95 (s) | s/page p50 | s/page p95 |
|---|---:|---:|---:|---:|---:|---:|
| image-only | 13 | 13 | 4.22 | 64.5 | 4.22 | 64.5 |
| malformed | 2 | 2 | 3.54 | 7.83 | 3.54 | 7.83 |
| multicolumn | 7 | 7 | 37.9 | 106.94 | 35.647 | 63.04 |
| multilingual | 4 | 4 | 6.96 | 58.44 | 1.91 | 58.44 |
| native-text | 19 | 16 | 16.81 | 336.31 | 8.44 | 84.078 |

## Failures

| Asset | Category | Signature | Runner healthy after |
|---|---|---|:-:|
| `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` | oom | infer_failed | yes |
| `public/009-pdflatex-geotopo/GeoTopo.pdf` | oom | infer_failed | yes |
| `public/017-unreadable-meta-data/unreadablemetadata.pdf` | other_exception | infer_failed | yes |