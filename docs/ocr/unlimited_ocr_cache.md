# Unlimited-OCR safe-size cache

The Unlimited-OCR portable runtime persists the largest chunk size
that succeeded end-to-end on the current hardware+software combination
so subsequent runs can skip the "probe and fall back" cost. This page
describes where that cache lives, how to override its location, how
to disable it, and how to clear it.

The cache is optional. Deleting it, disabling it, or losing it never
breaks a run — the runtime falls back to a live-VRAM formula and
records a new value on the next successful document.

## Default location

| OS      | Default path                                                        |
|---------|---------------------------------------------------------------------|
| Windows | `%LOCALAPPDATA%\aksharamd\ocr_safe_size_cache.json`                 |
| POSIX   | `$XDG_CACHE_HOME/aksharamd/ocr_safe_size_cache.json` if set, else `~/.cache/aksharamd/ocr_safe_size_cache.json` |

The directory is created on first successful write. Nothing is written
until at least one document completes end-to-end.

## Override the location

Set `AKSHARAMD_OCR_CACHE_PATH` to an absolute path to redirect reads
and writes. Useful for shared / per-project caches:

```bash
# POSIX
export AKSHARAMD_OCR_CACHE_PATH="/srv/aksharamd/ocr_safe_size_cache.json"

# Windows PowerShell
$env:AKSHARAMD_OCR_CACHE_PATH = "D:\aksharamd\ocr_safe_size_cache.json"
```

## Disable the cache

Set `AKSHARAMD_OCR_CACHE_DISABLE` to `1`, `true`, or `yes`
(case-insensitive) to skip the cache entirely — no reads, no writes.
Every run will start from the live-VRAM formula.

```bash
export AKSHARAMD_OCR_CACHE_DISABLE=1
```

Callers using the Python API should check
`aksharamd.plugins.ocr_backends.unlimited_ocr.is_cache_disabled()` and
pass `cache_path=None` to `infer_pdf_portable` when it returns True.

## Clear the cache

Delete the JSON file, or call `clear_cache()`:

```python
from aksharamd.plugins.ocr_backends.unlimited_ocr import clear_cache

# Clear the default location.
clear_cache()

# Or clear a specific file.
from pathlib import Path
clear_cache(Path("/srv/aksharamd/ocr_safe_size_cache.json"))
```

`clear_cache()` returns `True` if a file was removed, `False` if it
did not exist. Permission errors are tolerated silently by design.

## What the record contains

Each cache entry is keyed on a fingerprint of every axis that changes
what fits in VRAM:

- **`gpu_identity`** — `nvidia-smi --query-gpu=uuid` when available,
  else `torch.cuda.get_device_name(0)`
- **`total_vram_gib_bucket`** — GPU total VRAM rounded to the nearest
  integer GiB
- **`model_revision`** — pinned Unlimited-OCR HuggingFace revision
- **`precision`** — inference dtype (currently `bf16`)
- **`torch_version`**, **`cuda_version`** — from the live process
- **`render_policy_version`** — PDF→image pipeline version
- **`chunking_policy_version`** — chunk-orchestration logic version

Any change in any of the above invalidates the record — the runtime
returns a miss and falls back to the live-VRAM formula.

For each key the record stores:

- `successful_chunk_size` — largest recent success
- `largest_known_successful_size` — high-water mark across all runs
- `smallest_known_failed_size` — tightest known upper bound on what
  will OOM (used to shrink cached successes if a later failure
  invalidated them)
- `peak_reserved_mib_observed` — peak reserved VRAM at the last
  success
- `updated_at` — ISO-8601 UTC timestamp
- `notes` — optional operator notes; failure category label when a
  failure is recorded

The cache is a single JSON file with `schema_version: "1"`. A schema
mismatch or corrupted file is treated as a miss and replaced on the
next successful run.
