# PDF Benchmark v1 — Unlimited-OCR adapter (2026-07-19)

**Tool:** Baidu Unlimited-OCR (HuggingFace `baidu/Unlimited-OCR`)
**Pinned revision:** `NOT SET — adapter refuses to load`
**Commit under evaluation:** `cb44f02a35c1bfbfe59b6507b122d79f890d24c0`
**Python:** 3.12.2 · **Platform:** Windows-11-10.0.26200-SP0

**No AksharaMD production code changes.** `SCORING_POLICY_VERSION` remains `"1.0"`.

## Environment feasibility

- torch: 2.12.1+cu126
- CUDA available: True
- Device: NVIDIA GeForce RTX 3060 (12.0 GB VRAM)
- Compute capability: 8.6
- BF16 supported: True

## Execution mode summary

- `dry_run`: 45

If `real_inference` is not present, the benchmark did NOT run the model on any file. Real inference requires: pinned revision configured, model downloaded to the local HuggingFace cache, NVIDIA GPU with BF16 support. See the ADR (`docs/adr/ocr_backend_strategy.md`) for the download procedure.

## Headline metrics

| Metric | Value |
|---|---:|
| Files evaluated | 45 |
| `execution_success_rate` | 0 / 45 (0.0 %) |
| `meaningful_content_rate` | 0 / 45 (0.0 %) |
| `structurally_usable_rate` | 0 / 45 (0.0 %) |

## Interpretation — evidence pending

This report was generated in `dry_run` / `model_not_cached` / `no_gpu` / `deps_missing` mode. The adapter, tests, and benchmark harness are in place, but real inference against the 45-asset corpus requires the ~14 GB `baidu/Unlimited-OCR` model download.

The paired human review vs. AksharaMD Phase 1 and vs. the other three adapters is deferred until real inference has run against every eligible asset.

## Constraints observed

- No AksharaMD parser / validator / scoring / warning-penalty / packaging / model code changed.
- `SCORING_POLICY_VERSION` remains `"1.0"`.
- Same 45-asset frozen manifest as AksharaMD Phase 1 and all other adapters.
- Same checksum-verified ParseBench cache.
- Offline enforcement: `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` set before `transformers` import.
- `use_safetensors=True` — refuses pickle-based weights.
- `trust_remote_code=True` gated on a PINNED revision — no mutable branch reference accepted.
- Model download NOT performed by this adapter.
- Per-file errors preserved; single failures do not abort the run.
- No cross-parser ranking or winner declaration.
