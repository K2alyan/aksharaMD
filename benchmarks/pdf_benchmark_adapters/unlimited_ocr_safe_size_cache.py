"""Persistent safe-size cache for Unlimited-OCR.

Purpose
-------
Once a document has completed end-to-end at a given chunk size inside
the subprocess-isolated orchestrator, remember that size for the SAME
combination of hardware, model, and software so future runs can skip
the "probe and fall back" cost.

Deliberate constraints (per PR 3 review):

* Write a size ONLY after an entire document completes successfully.
  Never write partial-success or single-chunk-success values.
* Key on every axis that can change what fits: GPU identity, total
  VRAM bucket, model revision, precision, torch version, CUDA version,
  render-policy version, and chunking-policy version. A change in any
  of them invalidates the cached value.
* Treat cached values as a STARTING HINT, not truth. The caller must
  still check live free VRAM before trusting a cached value — someone
  else may be using the GPU right now.
* Atomic writes: write to a temp path in the same directory, then
  os.replace() so the cache file is never observed half-written.
* Tolerate missing or corrupt cache files by returning defaults.
  A broken cache must never break a benchmark run.

The cache is a single JSON file. Multiple records are stored as a
dict keyed by ``cache_key``.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_CACHE_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class SoftwareFingerprint:
    """Every field is part of the cache key. Any change invalidates
    the record. Kept as a dataclass so the fields are enumerable at
    test time and drift is easy to see."""

    gpu_identity: str
    """UUID from `nvidia-smi --query-gpu=uuid` when available, else
    normalized device name (``torch.cuda.get_device_name(0)``)."""

    total_vram_gib_bucket: int
    """Round ``torch.cuda.get_device_properties(0).total_memory`` to
    the nearest integer GiB. Buckets neighbouring devices with tiny
    VRAM differences (e.g. two RTX 3060 SKUs with 11.99 vs 12.00 GiB
    reported)."""

    model_revision: str
    """Pinned model revision from the adapter."""

    precision: str
    """"bf16", "fp16", "fp32", "int8", …. From the adapter."""

    torch_version: str
    """Full torch version string, e.g. "2.12.0+cu126"."""

    cuda_version: str
    """CUDA runtime major.minor, e.g. "12.6"."""

    render_policy_version: str
    """Version of the PDF-to-image pipeline. Changes here (e.g. DPI,
    resampling) can change per-page cost significantly."""

    chunking_policy_version: str
    """Version of the chunk-orchestration logic. Changes here can
    invalidate old sizes."""


def build_cache_key(fp: SoftwareFingerprint) -> str:
    """Return a short stable key derived from every fingerprint field.

    We SHA-256 the concatenated fields rather than joining them into
    the key so long GPU names or model revisions don't blow up the
    JSON file's readability. The full fingerprint is stored alongside
    the record for audit.
    """
    canonical = "|".join([
        _CACHE_SCHEMA_VERSION,
        fp.gpu_identity,
        str(fp.total_vram_gib_bucket),
        fp.model_revision,
        fp.precision,
        fp.torch_version,
        fp.cuda_version,
        fp.render_policy_version,
        fp.chunking_policy_version,
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass
class CacheRecord:
    """One entry in the cache. Multiple records per fingerprint are
    kept in a rolling window so we can spot regressions."""

    fingerprint: dict[str, Any]
    successful_chunk_size: int
    largest_known_successful_size: int
    smallest_known_failed_size: int | None
    peak_reserved_mib_observed: int | None
    updated_at: str
    """ISO-8601 UTC timestamp."""

    schema_version: str = _CACHE_SCHEMA_VERSION
    notes: list[str] = field(default_factory=list)


def load_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    """Return the cache dict or ``{}`` on any error. Never raises.

    A corrupted cache is treated the same as no cache — the caller
    will fall back to the PR 1 formula, and the next successful run
    will replace the corrupted file.
    """
    try:
        raw = cache_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    version = data.get("schema_version")
    if version != _CACHE_SCHEMA_VERSION:
        # A future migration would go here; for now, a mismatch is a miss.
        return {}
    records = data.get("records")
    if not isinstance(records, dict):
        return {}
    return records


def save_cache_atomic(cache_path: Path, records: dict[str, dict[str, Any]]) -> None:
    """Atomically overwrite the cache file.

    Writes to a temp file in the same directory (so ``os.replace`` is
    a same-filesystem rename) then swaps it in. If the write itself
    raises the temp file is cleaned up. On any failure the caller
    receives an OSError and the existing cache is untouched.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "records": records,
    }
    fd, tmp_str = tempfile.mkstemp(
        prefix=cache_path.name + ".tmp.", dir=str(cache_path.parent),
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, cache_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def look_up(records: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Look up a record by key. Returns None on miss."""
    record = records.get(key)
    if not isinstance(record, dict):
        return None
    if "successful_chunk_size" not in record:
        return None
    return record


def choose_initial_size_from_cache_hit(
    cached_size: int,
    free_vram_bytes: int | None,
    *,
    safety_factor: float,
    per_page_memory_estimate_mib: int,
    min_size: int,
    max_size: int,
) -> tuple[int, str]:
    """Given a cache hit for the current fingerprint, decide whether
    the cached size is still safe against currently-free VRAM.

    Returns ``(chunk_size, reason)`` where ``reason`` is one of:
        - ``"cache_hit_within_current_vram"``
        - ``"cache_hit_shrunk_by_current_vram"``
        - ``"cache_hit_ignored_no_vram_read"``  — we cannot verify

    The formula for the current-VRAM guard is the SAME as
    ``_estimate_initial_chunk_size``'s vram-based branch, kept in sync
    here to avoid a circular import.
    """
    cached_size = max(min_size, min(max_size, int(cached_size)))
    if free_vram_bytes is None or free_vram_bytes <= 0:
        # No live read → trust the cache since we have no other data.
        return cached_size, "cache_hit_ignored_no_vram_read"
    per_page_bytes = per_page_memory_estimate_mib * 1024 * 1024
    if per_page_bytes <= 0:
        return cached_size, "cache_hit_ignored_no_vram_read"
    usable_bytes = int(free_vram_bytes * safety_factor)
    live_estimate = usable_bytes // per_page_bytes
    live_estimate = max(min_size, min(max_size, int(live_estimate)))
    if live_estimate >= cached_size:
        return cached_size, "cache_hit_within_current_vram"
    # Someone else is using the GPU — shrink to what fits now.
    return live_estimate, "cache_hit_shrunk_by_current_vram"


def record_success(
    cache_path: Path,
    key: str,
    *,
    fingerprint: SoftwareFingerprint,
    successful_chunk_size: int,
    peak_reserved_mib: int | None,
    now_iso: str,
    note: str | None = None,
) -> None:
    """Merge a successful outcome into the cache and persist atomically.

    Updates ``successful_chunk_size``, keeps a running
    ``largest_known_successful_size``, preserves any earlier
    ``smallest_known_failed_size``. Never lowers
    ``largest_known_successful_size`` even if a later run at a smaller
    size also succeeded — that field is a high-water mark.
    """
    records = load_cache(cache_path)
    existing = records.get(key, {})
    largest = max(
        int(existing.get("largest_known_successful_size", 0) or 0),
        int(successful_chunk_size),
    )
    smallest_failed = existing.get("smallest_known_failed_size")
    record = CacheRecord(
        fingerprint=asdict(fingerprint),
        successful_chunk_size=int(successful_chunk_size),
        largest_known_successful_size=int(largest),
        smallest_known_failed_size=smallest_failed,
        peak_reserved_mib_observed=peak_reserved_mib,
        updated_at=now_iso,
        notes=(existing.get("notes") or []) + ([note] if note else []),
    )
    records[key] = asdict(record)
    save_cache_atomic(cache_path, records)


def record_failure(
    cache_path: Path,
    key: str,
    *,
    fingerprint: SoftwareFingerprint,
    failed_chunk_size: int,
    now_iso: str,
    note: str | None = None,
) -> None:
    """Merge a failed outcome. Records the SMALLEST known failed size
    (a lower value is worse, so it's the tightest upper bound on what
    can survive next time).

    Does NOT update ``successful_chunk_size``; a failure never claims
    a working size.
    """
    records = load_cache(cache_path)
    existing = records.get(key, {})
    current_smallest_failed = existing.get("smallest_known_failed_size")
    new_smallest_failed = int(failed_chunk_size)
    if current_smallest_failed is not None:
        new_smallest_failed = min(int(current_smallest_failed), new_smallest_failed)
    record = {
        **existing,
        "fingerprint": asdict(fingerprint),
        "smallest_known_failed_size": new_smallest_failed,
        "updated_at": now_iso,
        "schema_version": _CACHE_SCHEMA_VERSION,
        "notes": (existing.get("notes") or []) + ([note] if note else []),
    }
    # Keep the successful fields if present, else initialize to conservative values.
    record.setdefault("successful_chunk_size", 0)
    record.setdefault("largest_known_successful_size", 0)
    record.setdefault("peak_reserved_mib_observed", None)
    records[key] = record
    save_cache_atomic(cache_path, records)
