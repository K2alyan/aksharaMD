"""Portable production entrypoint for Unlimited-OCR.

This is the single function callers should invoke when they want
Unlimited-OCR to work correctly on any GPU without prior calibration.
It stitches together the three PRs of the large-document strategy:

  * PR 1: pick an initial chunk size from live free VRAM if we have
    no better data.
  * PR 2: run the document inside a disposable child process so a
    bad initial guess cannot poison the parent.
  * PR 3: persist a successful size in a cache keyed on the exact
    hardware + software combination that produced it, and re-check
    live free VRAM before trusting the cached value on the next run.

The public API mirrors ``_UnlimitedOcrRunner.infer_pdf`` in return
shape so downstream code can migrate with a one-line change.

The signals dict returned includes ``portable_signals`` recording
which sizing source was used (formula / cache-hit / cache-shrunk /
cache-ignored), the fingerprint, and the resolution path — so a
reader can always see WHY the initial size was chosen.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path
from typing import Any

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter import (
    _DEFAULT_PER_PAGE_MEMORY_MIB,
    _DEFAULT_SAFETY_FACTOR,
    _MAX_CHUNK_SIZE,
    _MIN_CHUNK_SIZE,
    _UNLIMITED_OCR_MODEL_REVISION,
    _estimate_initial_chunk_size,
    _query_free_vram_bytes,
)
from benchmarks.pdf_benchmark_adapters.unlimited_ocr_orchestrator import (
    _DEFAULT_LOG_MAX_BYTES,
    _DEFAULT_MAX_RESTARTS,
    run_infer_pdf_isolated,
)
from benchmarks.pdf_benchmark_adapters.unlimited_ocr_safe_size_cache import (
    SoftwareFingerprint,
    build_cache_key,
    choose_initial_size_from_cache_hit,
    load_cache,
    look_up,
    record_failure,
    record_success,
)

# Versions used in the cache key. Bump these when the associated
# behaviour changes so old records become invalid.
_RENDER_POLICY_VERSION = "render_v1_dpi300_png_rgb"
_CHUNKING_POLICY_VERSION = "chunk_v1_halving"

# Precision the runner uses at inference. If it changes, invalidate
# cached values (bumping this string is enough).
_INFERENCE_PRECISION = "bf16"


def _nvidia_gpu_uuid() -> str | None:
    try:
        raw = subprocess.check_output(  # nosec B603 B607 — constant argv, no shell
            ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.DEVNULL,
        )
        line = raw.decode("utf-8", errors="replace").strip().splitlines()[0]
        return line.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError, IndexError):
        return None


def _torch_hardware_probe(torch_mod: Any) -> dict[str, Any]:
    """Read GPU identity, VRAM bucket, torch and CUDA versions from
    the live process. Falls back to None-ish values on any error so
    the caller can decide whether to proceed with degraded caching."""
    result: dict[str, Any] = {
        "gpu_identity": None,
        "total_vram_gib_bucket": 0,
        "torch_version": "",
        "cuda_version": "",
        "free_vram_bytes": None,
    }
    try:
        result["torch_version"] = str(torch_mod.__version__)
    except Exception:  # noqa: BLE001
        pass
    try:
        result["cuda_version"] = str(torch_mod.version.cuda)
    except Exception:  # noqa: BLE001
        pass
    try:
        if torch_mod.cuda.is_available():
            uuid = _nvidia_gpu_uuid()
            if uuid:
                result["gpu_identity"] = uuid
            else:
                result["gpu_identity"] = str(torch_mod.cuda.get_device_name(0))
            total = int(torch_mod.cuda.get_device_properties(0).total_memory)
            # Round to nearest GiB. 12.00 GiB and 11.99 GiB share a bucket.
            result["total_vram_gib_bucket"] = int(round(total / (1024 ** 3)))
    except Exception:  # noqa: BLE001
        pass
    result["free_vram_bytes"] = _query_free_vram_bytes(torch_mod)
    return result


def _build_fingerprint(probe: dict[str, Any]) -> SoftwareFingerprint:
    return SoftwareFingerprint(
        gpu_identity=probe.get("gpu_identity") or "unknown_gpu",
        total_vram_gib_bucket=int(probe.get("total_vram_gib_bucket") or 0),
        model_revision=_UNLIMITED_OCR_MODEL_REVISION or "unpinned",
        precision=_INFERENCE_PRECISION,
        torch_version=probe.get("torch_version") or "",
        cuda_version=probe.get("cuda_version") or "",
        render_policy_version=_RENDER_POLICY_VERSION,
        chunking_policy_version=_CHUNKING_POLICY_VERSION,
    )


def _resolve_initial_size(
    probe: dict[str, Any],
    fingerprint: SoftwareFingerprint,
    cache_path: Path | None,
    env_override: str | None,
) -> tuple[int, dict[str, Any]]:
    """Return ``(chunk_size, portable_signals)``.

    Precedence:
      1. env_override (if valid positive int)
      2. cache hit filtered by live free VRAM
      3. PR 1 formula (free-VRAM based)
    """
    portable: dict[str, Any] = {
        "fingerprint": {
            "gpu_identity": fingerprint.gpu_identity,
            "total_vram_gib_bucket": fingerprint.total_vram_gib_bucket,
            "model_revision": fingerprint.model_revision,
            "precision": fingerprint.precision,
            "torch_version": fingerprint.torch_version,
            "cuda_version": fingerprint.cuda_version,
            "render_policy_version": fingerprint.render_policy_version,
            "chunking_policy_version": fingerprint.chunking_policy_version,
        },
        "cache_path": str(cache_path) if cache_path else None,
        "cache_key": None,
        "cache_hit": False,
        "cache_record_snapshot": None,
        "resolution_source": None,
        "resolution_reason": None,
    }

    # Fall through to formula-based estimate first — always run so we
    # capture the "what the formula would have said" number even when
    # a cache hit wins. That value is visible in the returned signals.
    formula_size, formula_signals = _estimate_initial_chunk_size(
        free_vram_bytes=probe.get("free_vram_bytes"),
        env_override=env_override,
    )
    portable["formula_estimate"] = {
        "chunk_size": formula_size,
        "signals": formula_signals,
    }

    if env_override and formula_signals.get("source") == "env":
        # Operator override wins — cache does not enter.
        portable["resolution_source"] = "env_override"
        portable["resolution_reason"] = "env_override_applied"
        return formula_size, portable

    if cache_path is not None:
        cache_key = build_cache_key(fingerprint)
        portable["cache_key"] = cache_key
        records = load_cache(cache_path)
        hit = look_up(records, cache_key)
        if hit is not None and hit.get("successful_chunk_size", 0) > 0:
            portable["cache_hit"] = True
            portable["cache_record_snapshot"] = hit
            chosen, reason = choose_initial_size_from_cache_hit(
                cached_size=int(hit["successful_chunk_size"]),
                free_vram_bytes=probe.get("free_vram_bytes"),
                safety_factor=_DEFAULT_SAFETY_FACTOR,
                per_page_memory_estimate_mib=_DEFAULT_PER_PAGE_MEMORY_MIB,
                min_size=_MIN_CHUNK_SIZE,
                max_size=_MAX_CHUNK_SIZE,
                smallest_known_failed_size=hit.get("smallest_known_failed_size"),
            )
            portable["resolution_source"] = "cache"
            portable["resolution_reason"] = reason
            return chosen, portable

    portable["resolution_source"] = "formula"
    portable["resolution_reason"] = formula_signals.get("source")
    return formula_size, portable


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def infer_pdf_portable(
    pdf: Path,
    workdir: Path,
    *,
    cache_path: Path | None = None,
    env_override: str | None = None,
    max_restarts: int = _DEFAULT_MAX_RESTARTS,
    per_run_timeout_seconds: int | None = None,
    log_max_bytes: int = _DEFAULT_LOG_MAX_BYTES,
    torch_mod: Any = None,
    # Injection seams for tests — same interface as the orchestrator.
    orchestrator_fn: Any = None,
) -> tuple[str, str, dict[str, Any]]:
    """Run one Unlimited-OCR document inference end-to-end with:
      * hardware-aware initial sizing (PR 1),
      * subprocess isolation + kill-and-restart (PR 2),
      * safe-size cache with live-VRAM verification (PR 3).

    Returns ``(text, exception_or_empty, signals)``. ``signals`` is a
    superset of the orchestrator's signals with an added
    ``portable_signals`` key describing the sizing decision.

    ``cache_path=None`` disables persistence entirely — useful for
    tests and for callers who deliberately want no state on disk.
    """
    if torch_mod is None:
        import torch as torch_mod  # noqa: WPS433 — deliberate lazy import
    probe = _torch_hardware_probe(torch_mod)
    fingerprint = _build_fingerprint(probe)
    initial_size, portable_signals = _resolve_initial_size(
        probe=probe,
        fingerprint=fingerprint,
        cache_path=cache_path,
        env_override=env_override,
    )

    runner = orchestrator_fn or run_infer_pdf_isolated
    text, exc, signals = runner(
        pdf, workdir, initial_chunk_size=initial_size,
        max_restarts=max_restarts,
        per_run_timeout_seconds=per_run_timeout_seconds,
        log_max_bytes=log_max_bytes,
    )
    # Merge portable_signals into the returned signals.
    merged: dict[str, Any] = dict(signals) if isinstance(signals, dict) else {}
    merged["portable_signals"] = portable_signals

    if cache_path is not None:
        if not exc:
            # Success: record the final chunk size (which may equal
            # the initial size on a clean first attempt, or a smaller
            # value if the orchestrator shrank it).
            try:
                record_success(
                    cache_path=cache_path,
                    key=build_cache_key(fingerprint),
                    fingerprint=fingerprint,
                    successful_chunk_size=int(merged.get("final_chunk_size_used") or initial_size),
                    peak_reserved_mib=_extract_peak_reserved_mib(merged),
                    now_iso=_now_iso(),
                )
            except OSError:
                merged.setdefault("cache_write_error", "record_success_ioerror")
        else:
            # Failure: record the failed size so future runs can
            # start below it. Do NOT record success.
            try:
                record_failure(
                    cache_path=cache_path,
                    key=build_cache_key(fingerprint),
                    fingerprint=fingerprint,
                    failed_chunk_size=int(merged.get("final_chunk_size_used") or initial_size),
                    now_iso=_now_iso(),
                    note=exc,
                )
            except OSError:
                merged.setdefault("cache_write_error", "record_failure_ioerror")

    return text, exc, merged


def _extract_peak_reserved_mib(signals: dict[str, Any]) -> int | None:
    """Fish the peak reserved VRAM out of the orchestrator's signals.

    The worker's ``signals`` dict from the successful attempt carries
    ``peak_gpu_memory_mib`` for the small-doc path and per-chunk
    ``peak_vram_reserved_mib`` values inside ``chunks`` for the chunked
    path. Try both.
    """
    worker_signals = signals.get("worker_signals") or {}
    peak = worker_signals.get("peak_gpu_memory_mib")
    if isinstance(peak, int) and peak > 0:
        return peak
    chunks = worker_signals.get("chunks") or []
    reserved = [
        c.get("peak_vram_reserved_mib") for c in chunks
        if isinstance(c, dict) and c.get("status") == "PASS"
    ]
    reserved_ints = [r for r in reserved if isinstance(r, int) and r > 0]
    if reserved_ints:
        return max(reserved_ints)
    return None
