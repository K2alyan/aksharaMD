# Unlimited-OCR A1.5 feasibility smoke — 2026-07-20

**Result:** PASS

## Environment
- **python:** 3.12.2
- **platform:** Windows-11-10.0.26200-SP0
- **nvidia_driver:** 595.95
- **torch:** 2.12.1+cu126
- **cuda:** 12.6
- **gpu_name:** NVIDIA GeForce RTX 3060
- **gpu_vram_gib:** 12.0
- **bf16_supported:** True

## Load
- **elapsed_seconds:** 19.39
- **rss_before_mib:** 511
- **rss_after_mib:** 904
- **call_log:** ['load_trusted_manifest', 'verify_snapshot_against_manifest', 'fast_verify', 'import_transformers', 'get_class_from_dynamic_module', 'install_module_local_eval_override', 'AutoTokenizer.from_pretrained', 'AutoModel.from_pretrained']
- **loaded:** True
- **load_error:** 
- **peak_allocated_mib_load:** 6456
- **peak_reserved_mib_load:** 6490

## Per-asset

| Asset | Status | Runtime (s) | Peak alloc (MiB) | Peak reserved (MiB) | RSS (MiB) | Output chars | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| `parsebench/japanese_case` | PASS | 583.85 | 6928 | 7088 | - | 17250 | - |
| `parsebench/2colmercedes` | PASS | 34.18 | 6928 | 7076 | - | 3689 | - |
| `public/019-grayscale-image/grayscale-image.pdf` | PASS | 2.43 | 6928 | 7076 | - | 84 | - |

Written to: `C:\Users\kalya\Omnimark\benchmarks\PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_2026-07-20.json` and `C:\Users\kalya\Omnimark\benchmarks\PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_2026-07-20.md`