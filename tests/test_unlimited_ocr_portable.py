"""Tests for the portable Unlimited-OCR entrypoint (PR 3).

Mocked orchestrator, mocked torch — no CUDA, no real subprocess, no
network. Covers the sizing-resolution precedence, cache write on
success, cache write on failure, live-VRAM re-check on cache hits.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_portable import (  # type: ignore
    _extract_peak_reserved_mib,
    _resolve_initial_size,
    infer_pdf_portable,
)
from benchmarks.pdf_benchmark_adapters.unlimited_ocr_safe_size_cache import (  # type: ignore
    SoftwareFingerprint,
    build_cache_key,
    load_cache,
    record_success,
)


def _sample_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake\n")
    return p


def _fake_torch(free_bytes: int | None, total_bytes: int, name: str = "TestGPU"):
    class _Cuda:
        @staticmethod
        def is_available():
            return free_bytes is not None

        @staticmethod
        def mem_get_info(_):
            if free_bytes is None:
                raise RuntimeError("no cuda")
            return (free_bytes, total_bytes)

        @staticmethod
        def get_device_name(_):
            return name

        @staticmethod
        def get_device_properties(_):
            class _Props:
                total_memory = total_bytes
            return _Props()

    class _Version:
        cuda = "12.6"

    class _Torch:
        cuda = _Cuda
        version = _Version
        __version__ = "2.12.0+cu126"

    return _Torch


def _make_orchestrator(script: list[dict[str, Any]]):
    """Fake orchestrator that returns pre-scripted results."""
    calls: list[dict[str, Any]] = []

    def _fn(pdf, workdir, *, initial_chunk_size, **kwargs):
        step = script.pop(0)
        calls.append({
            "initial_chunk_size": initial_chunk_size,
            "pdf": pdf,
            "workdir": workdir,
        })
        return step["text"], step["exc"], step["signals"]

    _fn.calls = calls  # type: ignore[attr-defined]
    return _fn


def _gib(n: float) -> int:
    return int(n * 1024 * 1024 * 1024)


# ── _resolve_initial_size precedence ────────────────────────────────────


def _fp() -> SoftwareFingerprint:
    return SoftwareFingerprint(
        gpu_identity="test-gpu-uuid",
        total_vram_gib_bucket=12,
        model_revision="rev1",
        precision="bf16",
        torch_version="2.12.0",
        cuda_version="12.6",
        render_policy_version="render_v1",
        chunking_policy_version="chunk_v1",
    )


def test_resolve_no_cache_falls_back_to_formula(tmp_path):
    probe = {"free_vram_bytes": _gib(5.5)}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=_fp(),
        cache_path=None, env_override=None,
    )
    assert size == 6  # PR 1 formula: 5.5 GiB * 0.60 / 500 MiB ≈ 6
    assert portable["resolution_source"] == "formula"


def test_resolve_env_override_beats_cache(tmp_path):
    p = tmp_path / "cache.json"
    fp = _fp()
    record_success(p, build_cache_key(fp), fingerprint=fp,
                   successful_chunk_size=16, peak_reserved_mib=7000, now_iso="t")
    probe = {"free_vram_bytes": _gib(5.5)}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=fp, cache_path=p, env_override="10",
    )
    assert size == 10
    assert portable["resolution_source"] == "env_override"


def test_resolve_cache_hit_within_current_vram(tmp_path):
    """Cache says 6, current free VRAM supports 6 → use 6."""
    p = tmp_path / "cache.json"
    fp = _fp()
    record_success(p, build_cache_key(fp), fingerprint=fp,
                   successful_chunk_size=6, peak_reserved_mib=7000, now_iso="t")
    probe = {"free_vram_bytes": _gib(5.5)}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=fp, cache_path=p, env_override=None,
    )
    assert size == 6
    assert portable["resolution_source"] == "cache"
    assert portable["resolution_reason"] == "cache_hit_within_current_vram"
    assert portable["cache_hit"] is True


def test_resolve_cache_hit_shrunk_by_current_vram(tmp_path):
    """Cache says 20 but only 500 MiB free → shrink."""
    p = tmp_path / "cache.json"
    fp = _fp()
    record_success(p, build_cache_key(fp), fingerprint=fp,
                   successful_chunk_size=20, peak_reserved_mib=10000, now_iso="t")
    probe = {"free_vram_bytes": 500 * 1024 * 1024}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=fp, cache_path=p, env_override=None,
    )
    assert size < 20
    assert portable["resolution_source"] == "cache"
    assert portable["resolution_reason"] == "cache_hit_shrunk_by_current_vram"


def test_resolve_corrupt_cache_falls_back_to_formula(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text("not json", encoding="utf-8")
    probe = {"free_vram_bytes": _gib(5.5)}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=_fp(), cache_path=p, env_override=None,
    )
    assert size == 6  # formula
    assert portable["resolution_source"] == "formula"


def test_resolve_cache_miss_falls_back_to_formula(tmp_path):
    p = tmp_path / "cache.json"
    fp_a = _fp()
    fp_b = SoftwareFingerprint(**{**fp_a.__dict__, "gpu_identity": "different"})
    record_success(p, build_cache_key(fp_a), fingerprint=fp_a,
                   successful_chunk_size=20, peak_reserved_mib=7000, now_iso="t")
    # Look up with the other fingerprint — cache misses.
    probe = {"free_vram_bytes": _gib(5.5)}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=fp_b, cache_path=p, env_override=None,
    )
    assert size == 6
    assert portable["resolution_source"] == "formula"
    assert portable["cache_hit"] is False


def test_resolve_cache_hit_shrinks_below_known_failure(tmp_path):
    """Reviewer's concern: if size 16 succeeded once and later failed,
    the next run must not start at 16. The portable resolver must
    pass smallest_known_failed_size through to the cache helper."""
    from benchmarks.pdf_benchmark_adapters.unlimited_ocr_safe_size_cache import (  # type: ignore
        record_failure,
    )
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_success(p, key, fingerprint=fp,
                   successful_chunk_size=16, peak_reserved_mib=8000, now_iso="t1")
    record_failure(p, key, fingerprint=fp, failed_chunk_size=16, now_iso="t2")
    # Even on a system with plenty of VRAM, we must NOT try 16 again.
    probe = {"free_vram_bytes": _gib(74.0)}
    size, portable = _resolve_initial_size(
        probe=probe, fingerprint=fp, cache_path=p, env_override=None,
    )
    assert size == 8  # halved from the failed size
    assert portable["resolution_source"] == "cache"
    assert portable["resolution_reason"] == "cache_hit_shrunk_by_known_failure"


def test_resolve_records_formula_estimate_even_on_cache_hit(tmp_path):
    """We always record what the formula WOULD HAVE said, for audit."""
    p = tmp_path / "cache.json"
    fp = _fp()
    record_success(p, build_cache_key(fp), fingerprint=fp,
                   successful_chunk_size=6, peak_reserved_mib=7000, now_iso="t")
    probe = {"free_vram_bytes": _gib(74.0)}  # 80 GiB card
    _size, portable = _resolve_initial_size(
        probe=probe, fingerprint=fp, cache_path=p, env_override=None,
    )
    assert "formula_estimate" in portable
    # Formula would have returned max size (40) on the 80 GiB card.
    assert portable["formula_estimate"]["chunk_size"] == 40


# ── infer_pdf_portable end-to-end ───────────────────────────────────────


def test_portable_success_writes_success_cache(tmp_path):
    pdf = _sample_pdf(tmp_path)
    cache = tmp_path / "cache.json"
    orch = _make_orchestrator([{
        "text": "done", "exc": "",
        "signals": {
            "isolation_mode": "subprocess",
            "final_chunk_size_used": 6,
            "worker_signals": {"peak_gpu_memory_mib": 7100},
            "attempts": [{"chunk_size": 6, "outcome": "success"}],
            "restart_count": 0,
        },
    }])
    text, exc, signals = infer_pdf_portable(
        pdf, tmp_path / "workdir",
        cache_path=cache,
        torch_mod=_fake_torch(free_bytes=_gib(5.5), total_bytes=_gib(12.0)),
        orchestrator_fn=orch,
    )
    assert exc == ""
    assert text == "done"
    assert signals["portable_signals"]["resolution_source"] == "formula"
    records = load_cache(cache)
    assert len(records) == 1
    rec = next(iter(records.values()))
    assert rec["successful_chunk_size"] == 6
    assert rec["peak_reserved_mib_observed"] == 7100


def test_portable_failure_does_not_record_success(tmp_path):
    pdf = _sample_pdf(tmp_path)
    cache = tmp_path / "cache.json"
    orch = _make_orchestrator([{
        "text": "", "exc": "isolated_infer_failed: single_page_oom",
        "signals": {
            "final_chunk_size_used": 1,
            "attempts": [
                {"chunk_size": 6, "outcome": "oom_retry"},
                {"chunk_size": 3, "outcome": "oom_retry"},
                {"chunk_size": 1, "outcome": "oom_retry"},
            ],
            "restart_count": 2,
        },
    }])
    text, exc, signals = infer_pdf_portable(
        pdf, tmp_path / "workdir",
        cache_path=cache,
        torch_mod=_fake_torch(free_bytes=_gib(5.5), total_bytes=_gib(12.0)),
        orchestrator_fn=orch,
    )
    assert text == ""
    assert "single_page_oom" in exc
    # A failure record was written — but NOT a successful_chunk_size.
    records = load_cache(cache)
    rec = next(iter(records.values()))
    assert rec["smallest_known_failed_size"] == 1
    assert rec["successful_chunk_size"] == 0  # never claimed a working size


def test_portable_cache_hit_used_on_second_run(tmp_path):
    pdf = _sample_pdf(tmp_path)
    cache = tmp_path / "cache.json"
    # First run: formula picks 6, success, cache updated.
    orch1 = _make_orchestrator([{
        "text": "one", "exc": "",
        "signals": {"final_chunk_size_used": 6, "worker_signals": {"peak_gpu_memory_mib": 7000}},
    }])
    infer_pdf_portable(
        pdf, tmp_path / "workdir1",
        cache_path=cache,
        torch_mod=_fake_torch(free_bytes=_gib(5.5), total_bytes=_gib(12.0)),
        orchestrator_fn=orch1,
    )
    # Second run: cache says 6 → orchestrator called with 6, not the
    # formula's default. Prove it by giving the orchestrator ONE
    # scripted step and asserting the call args.
    orch2 = _make_orchestrator([{
        "text": "two", "exc": "",
        "signals": {"final_chunk_size_used": 6, "worker_signals": {"peak_gpu_memory_mib": 6900}},
    }])
    _, _, signals = infer_pdf_portable(
        pdf, tmp_path / "workdir2",
        cache_path=cache,
        torch_mod=_fake_torch(free_bytes=_gib(5.5), total_bytes=_gib(12.0)),
        orchestrator_fn=orch2,
    )
    assert signals["portable_signals"]["resolution_source"] == "cache"
    assert orch2.calls[0]["initial_chunk_size"] == 6


def test_portable_no_cache_path_disables_persistence(tmp_path):
    pdf = _sample_pdf(tmp_path)
    orch = _make_orchestrator([{
        "text": "done", "exc": "",
        "signals": {"final_chunk_size_used": 6, "worker_signals": {}},
    }])
    _, exc, signals = infer_pdf_portable(
        pdf, tmp_path / "workdir",
        cache_path=None,
        torch_mod=_fake_torch(free_bytes=_gib(5.5), total_bytes=_gib(12.0)),
        orchestrator_fn=orch,
    )
    assert exc == ""
    # No cache file was created anywhere in tmp_path.
    cache_files = list(tmp_path.rglob("*.json"))
    assert cache_files == []
    assert signals["portable_signals"]["cache_path"] is None


def test_portable_signals_are_json_serializable(tmp_path):
    pdf = _sample_pdf(tmp_path)
    orch = _make_orchestrator([{
        "text": "done", "exc": "",
        "signals": {"final_chunk_size_used": 6, "worker_signals": {}},
    }])
    _, _, signals = infer_pdf_portable(
        pdf, tmp_path / "workdir",
        cache_path=tmp_path / "cache.json",
        torch_mod=_fake_torch(free_bytes=_gib(5.5), total_bytes=_gib(12.0)),
        orchestrator_fn=orch,
    )
    json.dumps(signals)  # must not raise


# ── _extract_peak_reserved_mib ──────────────────────────────────────────


def test_extract_peak_reserved_from_small_doc_path():
    signals = {"worker_signals": {"peak_gpu_memory_mib": 7500}}
    assert _extract_peak_reserved_mib(signals) == 7500


def test_extract_peak_reserved_from_chunked_path():
    signals = {
        "worker_signals": {
            "chunks": [
                {"status": "PASS", "peak_vram_reserved_mib": 6000},
                {"status": "PASS", "peak_vram_reserved_mib": 7200},
                {"status": "OOM_RETRY", "peak_vram_reserved_mib": None},
            ],
        }
    }
    assert _extract_peak_reserved_mib(signals) == 7200


def test_extract_peak_reserved_returns_none_when_absent():
    assert _extract_peak_reserved_mib({}) is None
    assert _extract_peak_reserved_mib({"worker_signals": {}}) is None
