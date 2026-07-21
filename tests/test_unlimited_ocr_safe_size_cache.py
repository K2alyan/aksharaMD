"""Tests for the Unlimited-OCR safe-size cache.

Pure-python only — no CUDA, no torch, no subprocesses.
"""
from __future__ import annotations

import json

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_safe_size_cache import (  # type: ignore
    SoftwareFingerprint,
    build_cache_key,
    choose_initial_size_from_cache_hit,
    load_cache,
    look_up,
    record_failure,
    record_success,
    save_cache_atomic,
)


def _fp(**overrides) -> SoftwareFingerprint:
    defaults = {
        "gpu_identity": "GPU-12345678-1234-1234-1234-000000000000",
        "total_vram_gib_bucket": 12,
        "model_revision": "abcdef1234567890" * 3 + "abcd",  # 40 char hex
        "precision": "bf16",
        "torch_version": "2.12.0+cu126",
        "cuda_version": "12.6",
        "render_policy_version": "render_v1_dpi300_png_rgb",
        "chunking_policy_version": "chunk_v1_halving",
    }
    defaults.update(overrides)
    return SoftwareFingerprint(**defaults)


# ── Key derivation ──────────────────────────────────────────────────────


def test_cache_key_is_deterministic():
    assert build_cache_key(_fp()) == build_cache_key(_fp())


def test_cache_key_changes_when_gpu_changes():
    a = build_cache_key(_fp(gpu_identity="A"))
    b = build_cache_key(_fp(gpu_identity="B"))
    assert a != b


def test_cache_key_changes_when_vram_bucket_changes():
    a = build_cache_key(_fp(total_vram_gib_bucket=12))
    b = build_cache_key(_fp(total_vram_gib_bucket=24))
    assert a != b


def test_cache_key_changes_when_model_revision_changes():
    a = build_cache_key(_fp(model_revision="rev-a"))
    b = build_cache_key(_fp(model_revision="rev-b"))
    assert a != b


def test_cache_key_changes_when_precision_changes():
    a = build_cache_key(_fp(precision="bf16"))
    b = build_cache_key(_fp(precision="fp16"))
    assert a != b


def test_cache_key_changes_when_torch_version_changes():
    a = build_cache_key(_fp(torch_version="2.12.0"))
    b = build_cache_key(_fp(torch_version="2.13.0"))
    assert a != b


def test_cache_key_changes_when_cuda_version_changes():
    a = build_cache_key(_fp(cuda_version="12.6"))
    b = build_cache_key(_fp(cuda_version="12.7"))
    assert a != b


def test_cache_key_changes_when_render_policy_version_changes():
    a = build_cache_key(_fp(render_policy_version="v1"))
    b = build_cache_key(_fp(render_policy_version="v2"))
    assert a != b


def test_cache_key_changes_when_chunking_policy_version_changes():
    a = build_cache_key(_fp(chunking_policy_version="v1"))
    b = build_cache_key(_fp(chunking_policy_version="v2"))
    assert a != b


def test_cache_key_length_and_shape():
    """Key is a 32-char hex string (SHA-256 truncated) — stable and
    filesystem-friendly."""
    key = build_cache_key(_fp())
    assert len(key) == 32
    assert all(c in "0123456789abcdef" for c in key)


# ── load_cache / save_cache_atomic ─────────────────────────────────────


def test_load_missing_file_returns_empty(tmp_path):
    assert load_cache(tmp_path / "does_not_exist.json") == {}


def test_load_empty_file_returns_empty(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text("", encoding="utf-8")
    assert load_cache(p) == {}


def test_load_malformed_json_returns_empty(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_cache(p) == {}


def test_load_wrong_schema_version_returns_empty(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"schema_version": "99", "records": {}}), encoding="utf-8")
    assert load_cache(p) == {}


def test_load_missing_records_returns_empty(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")
    assert load_cache(p) == {}


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "cache.json"
    records = {"k1": {"successful_chunk_size": 8, "misc": "value"}}
    save_cache_atomic(p, records)
    assert load_cache(p) == records


def test_save_atomic_leaves_no_temp_file(tmp_path):
    p = tmp_path / "cache.json"
    save_cache_atomic(p, {"k": {"successful_chunk_size": 4}})
    # The atomic write's temp file must not survive. Check by looking
    # for anything with our temp prefix in the parent dir.
    stragglers = [
        entry for entry in tmp_path.iterdir()
        if entry.name.startswith("cache.json.tmp.")
    ]
    assert stragglers == []
    assert p.exists()


def test_save_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "dir" / "cache.json"
    save_cache_atomic(p, {})
    assert p.exists()


# ── look_up ─────────────────────────────────────────────────────────────


def test_look_up_missing_key_returns_none():
    assert look_up({}, "missing") is None


def test_look_up_returns_record_when_present():
    records = {"key1": {"successful_chunk_size": 8, "other": "value"}}
    assert look_up(records, "key1") == records["key1"]


def test_look_up_ignores_record_without_successful_chunk_size():
    records = {"key1": {"only_failure_data": True}}
    assert look_up(records, "key1") is None


def test_look_up_ignores_non_dict_record():
    records = {"key1": "not-a-dict"}
    assert look_up(records, "key1") is None


# ── choose_initial_size_from_cache_hit ──────────────────────────────────


def _gib(n: float) -> int:
    return int(n * 1024 * 1024 * 1024)


def test_cache_hit_within_current_vram(tmp_path):
    """Cached size 6 on a 12 GiB card with plenty of free VRAM →
    use cached size."""
    chosen, reason = choose_initial_size_from_cache_hit(
        cached_size=6, free_vram_bytes=_gib(6.0),
        safety_factor=0.60, per_page_memory_estimate_mib=500,
        min_size=1, max_size=40,
    )
    assert chosen == 6
    assert reason == "cache_hit_within_current_vram"


def test_cache_hit_shrunk_by_current_vram():
    """Someone else is using the GPU. Cached size 20 but only 512 MiB
    free — must shrink."""
    chosen, reason = choose_initial_size_from_cache_hit(
        cached_size=20, free_vram_bytes=512 * 1024 * 1024,  # 512 MiB
        safety_factor=0.60, per_page_memory_estimate_mib=500,
        min_size=1, max_size=40,
    )
    assert chosen < 20
    assert reason == "cache_hit_shrunk_by_current_vram"


def test_cache_hit_shrunk_never_below_min():
    chosen, reason = choose_initial_size_from_cache_hit(
        cached_size=20, free_vram_bytes=1,
        safety_factor=0.60, per_page_memory_estimate_mib=500,
        min_size=1, max_size=40,
    )
    assert chosen == 1
    assert reason == "cache_hit_shrunk_by_current_vram"


def test_cache_hit_ignored_no_vram_read():
    chosen, reason = choose_initial_size_from_cache_hit(
        cached_size=8, free_vram_bytes=None,
        safety_factor=0.60, per_page_memory_estimate_mib=500,
        min_size=1, max_size=40,
    )
    assert chosen == 8
    assert reason == "cache_hit_ignored_no_vram_read"


def test_cache_hit_ignored_zero_free_vram():
    chosen, reason = choose_initial_size_from_cache_hit(
        cached_size=8, free_vram_bytes=0,
        safety_factor=0.60, per_page_memory_estimate_mib=500,
        min_size=1, max_size=40,
    )
    assert chosen == 8
    assert reason == "cache_hit_ignored_no_vram_read"


def test_cache_hit_clamped_to_max():
    chosen, reason = choose_initial_size_from_cache_hit(
        cached_size=9999, free_vram_bytes=_gib(74.0),
        safety_factor=0.60, per_page_memory_estimate_mib=500,
        min_size=1, max_size=40,
    )
    assert chosen == 40


# ── record_success / record_failure ────────────────────────────────────


def test_record_success_writes_new_entry(tmp_path):
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_success(
        p, key, fingerprint=fp, successful_chunk_size=8,
        peak_reserved_mib=7500, now_iso="2026-07-20T05:00:00+00:00",
    )
    records = load_cache(p)
    rec = records[key]
    assert rec["successful_chunk_size"] == 8
    assert rec["largest_known_successful_size"] == 8
    assert rec["peak_reserved_mib_observed"] == 7500
    assert rec["updated_at"] == "2026-07-20T05:00:00+00:00"
    assert rec["fingerprint"]["gpu_identity"] == fp.gpu_identity


def test_record_success_updates_largest_known(tmp_path):
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_success(p, key, fingerprint=fp, successful_chunk_size=8,
                   peak_reserved_mib=6000, now_iso="t1")
    record_success(p, key, fingerprint=fp, successful_chunk_size=12,
                   peak_reserved_mib=7000, now_iso="t2")
    rec = load_cache(p)[key]
    assert rec["successful_chunk_size"] == 12
    assert rec["largest_known_successful_size"] == 12


def test_record_success_does_not_lower_largest_known(tmp_path):
    """After a success at 12, a later success at 8 must NOT overwrite
    the largest-known field — it's a high-water mark."""
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_success(p, key, fingerprint=fp, successful_chunk_size=12,
                   peak_reserved_mib=7000, now_iso="t1")
    record_success(p, key, fingerprint=fp, successful_chunk_size=8,
                   peak_reserved_mib=6500, now_iso="t2")
    rec = load_cache(p)[key]
    assert rec["successful_chunk_size"] == 8  # latest success is 8
    assert rec["largest_known_successful_size"] == 12  # high-water preserved


def test_record_failure_writes_smallest_known_failed(tmp_path):
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_failure(p, key, fingerprint=fp, failed_chunk_size=40, now_iso="t1")
    rec = load_cache(p)[key]
    assert rec["smallest_known_failed_size"] == 40


def test_record_failure_keeps_smallest(tmp_path):
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_failure(p, key, fingerprint=fp, failed_chunk_size=40, now_iso="t1")
    record_failure(p, key, fingerprint=fp, failed_chunk_size=20, now_iso="t2")
    record_failure(p, key, fingerprint=fp, failed_chunk_size=30, now_iso="t3")
    rec = load_cache(p)[key]
    assert rec["smallest_known_failed_size"] == 20


def test_record_failure_does_not_touch_successful_size(tmp_path):
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_success(p, key, fingerprint=fp, successful_chunk_size=8,
                   peak_reserved_mib=6000, now_iso="t1")
    record_failure(p, key, fingerprint=fp, failed_chunk_size=40, now_iso="t2")
    rec = load_cache(p)[key]
    assert rec["successful_chunk_size"] == 8
    assert rec["largest_known_successful_size"] == 8
    assert rec["smallest_known_failed_size"] == 40


def test_record_success_after_failure_preserves_smallest_failed(tmp_path):
    """A later success at a size BELOW the smallest known failed must
    not clear the failed record — that upper bound is still useful."""
    p = tmp_path / "cache.json"
    fp = _fp()
    key = build_cache_key(fp)
    record_failure(p, key, fingerprint=fp, failed_chunk_size=40, now_iso="t1")
    record_success(p, key, fingerprint=fp, successful_chunk_size=8,
                   peak_reserved_mib=6000, now_iso="t2")
    rec = load_cache(p)[key]
    assert rec["successful_chunk_size"] == 8
    assert rec["smallest_known_failed_size"] == 40


def test_record_survives_previously_corrupted_cache(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text("garbage", encoding="utf-8")
    fp = _fp()
    key = build_cache_key(fp)
    # Must not raise — treats the corrupted file as empty.
    record_success(p, key, fingerprint=fp, successful_chunk_size=8,
                   peak_reserved_mib=6000, now_iso="t1")
    assert load_cache(p)[key]["successful_chunk_size"] == 8


def test_two_different_fingerprints_do_not_collide(tmp_path):
    """Runs on two different GPUs must not overwrite each other."""
    p = tmp_path / "cache.json"
    fp_a = _fp(gpu_identity="GPU-A")
    fp_b = _fp(gpu_identity="GPU-B")
    record_success(p, build_cache_key(fp_a), fingerprint=fp_a,
                   successful_chunk_size=8, peak_reserved_mib=6000, now_iso="t1")
    record_success(p, build_cache_key(fp_b), fingerprint=fp_b,
                   successful_chunk_size=32, peak_reserved_mib=15000, now_iso="t2")
    records = load_cache(p)
    assert records[build_cache_key(fp_a)]["successful_chunk_size"] == 8
    assert records[build_cache_key(fp_b)]["successful_chunk_size"] == 32
