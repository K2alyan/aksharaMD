# Unlimited-OCR GeoTopo Portable Validation — 2026-07-20

**GPU:** NVIDIA GeForce RTX 3060 (12.0 GiB)
**Torch/CUDA:** 2.12.1+cu126 / 12.6

| Asset | Run | Status | Pages | Init | Final | Restarts | Wall s | Peak MiB | SHA-256 (16) |
|---|:-:|:-:|:-:|:-:|:-:|:-:|---:|---:|---|
| `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` | 1 | PASS | 117/117 | 5 | 5 | 0 | 4815.41 | 7606 | `5be3e298fce3d85d` |
| `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` | 2 | PASS | 117/117 | 5 | 5 | 0 | 4308.92 | 7606 | `5be3e298fce3d85d` |
| `public/009-pdflatex-geotopo/GeoTopo.pdf` | 1 | PASS | 117/117 | 5 | 5 | 0 | 4195.71 | 7606 | `fa47e4a25970e4fc` |
| `public/009-pdflatex-geotopo/GeoTopo.pdf` | 2 | PASS | 117/117 | 5 | 5 | 0 | 4149.16 | 7606 | `fa47e4a25970e4fc` |

## Determinism per asset

- `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` — SHA match: **True**, char-count match: **True**
- `public/009-pdflatex-geotopo/GeoTopo.pdf` — SHA match: **True**, char-count match: **True**

## Acceptance criteria

- all_runs_complete: **True**
- all_pages_returned_exactly_once: **True**
- no_missing_pages: **True**
- peak_gpu_memory_below_safe_ceiling: **True**
- output_sha256_matches_across_pairs: **True**
