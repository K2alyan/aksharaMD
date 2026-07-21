"""Adaptive-chunking tests for the Unlimited-OCR adapter.

Exercises the pure ``_process_chunks_with_reduction`` orchestrator via
mocked ``inference_fn`` and ``health_probe`` callables. No CUDA, no
torch, no model, no PDFs — just the algorithm.

Also spot-checks the chunk range helpers and the OOM classifier.
"""
from __future__ import annotations

from aksharamd.plugins.ocr_backends.unlimited_ocr.adapter import (  # type: ignore
    _CHUNK_REDUCTION_SEQUENCE,
    _DEFAULT_PER_PAGE_MEMORY_MIB,
    _DEFAULT_SAFETY_FACTOR,
    _MAX_CHUNK_SIZE,
    _MIN_CHUNK_SIZE,
    _estimate_initial_chunk_size,
    _initial_chunk_ranges,
    _is_cuda_oom,
    _next_smaller_chunk_size,
    _OOMSignal,
    _process_chunks_with_reduction,
    _query_free_vram_bytes,
    _split_range_at,
)

# ── Chunk-range helpers ─────────────────────────────────────────────────


def test_initial_ranges_smaller_than_preferred():
    """A PDF smaller than 40 pages → exactly one range covering all."""
    ranges = _initial_chunk_ranges(total_pages=25, chunk_size=40)
    assert ranges == [(0, 25)]


def test_initial_ranges_exactly_preferred():
    """40 pages at chunk_size 40 → one range."""
    assert _initial_chunk_ranges(40, 40) == [(0, 40)]


def test_initial_ranges_one_over_preferred():
    """41 pages → [0-40, 40-41]. Final chunk is 1 page."""
    assert _initial_chunk_ranges(41, 40) == [(0, 40), (40, 41)]


def test_initial_ranges_117_pages_at_40():
    """117 pages at chunk_size 40 → 40 + 40 + 37."""
    assert _initial_chunk_ranges(117, 40) == [(0, 40), (40, 80), (80, 117)]


def test_initial_ranges_zero_pages():
    assert _initial_chunk_ranges(0, 40) == []


def test_split_range_at_preserves_coverage():
    """Splitting [40, 80) at 20 → [40, 60), [60, 80)."""
    subs = _split_range_at(40, 80, 20)
    assert subs == [(40, 60), (60, 80)]
    assert subs[0][0] == 40 and subs[-1][1] == 80


def test_split_range_at_uneven_final():
    """[80, 117) split at 40 → [80, 117) (single sub since 40 > 37)."""
    subs = _split_range_at(80, 117, 40)
    assert subs == [(80, 117)]


def test_split_range_at_smaller_size():
    """[80, 117) split at 20 → [80, 100), [100, 117)."""
    subs = _split_range_at(80, 117, 20)
    assert subs == [(80, 100), (100, 117)]
    covered = sum(e - s for s, e in subs)
    assert covered == 37  # no missing, no duplicates


def test_reduction_sequence_shape():
    assert _CHUNK_REDUCTION_SEQUENCE == (40, 20, 10, 5, 2, 1)
    assert _MAX_CHUNK_SIZE == 40
    assert _MIN_CHUNK_SIZE == 1


def test_next_smaller_chunk_size():
    assert _next_smaller_chunk_size(40) == 20
    assert _next_smaller_chunk_size(20) == 10
    assert _next_smaller_chunk_size(10) == 5
    assert _next_smaller_chunk_size(5) == 2
    assert _next_smaller_chunk_size(2) == 1
    assert _next_smaller_chunk_size(1) is None
    # An off-sequence value like 37 (the final tail chunk of 117 at 40)
    # should map to the next entry below (20).
    assert _next_smaller_chunk_size(37) == 20


# ── Test doubles ────────────────────────────────────────────────────────


def _healthy_probe() -> bool:
    return True


def _unhealthy_probe() -> bool:
    return False


def _mk_success_infer(text_per_page: str = "page_"):
    """An inference_fn that always succeeds. Emits deterministic text
    per page range for merge-order verification."""
    call_log: list[tuple[int, int, int, int]] = []

    def fn(page_start, page_end, chunk_index, chunk_size):
        call_log.append((page_start, page_end, chunk_index, chunk_size))
        # Emit one line per page so we can assert order + coverage.
        text = "\n".join(f"{text_per_page}{p:03d}" for p in range(page_start, page_end))
        return text, {"runtime_seconds": 0.01,
                       "peak_vram_allocated_mib": 6500,
                       "peak_vram_reserved_mib": 7000,
                       "output_files_written": page_end - page_start}
    fn.call_log = call_log  # type: ignore[attr-defined]
    return fn


def _mk_oom_at_size(oom_sizes: set[int]):
    """An inference_fn that raises ``_OOMSignal`` iff the current
    chunk_size is in ``oom_sizes``. Otherwise succeeds."""
    call_log: list[tuple[int, int, int, int]] = []

    def fn(page_start, page_end, chunk_index, chunk_size):
        call_log.append((page_start, page_end, chunk_index, chunk_size))
        if chunk_size in oom_sizes:
            raise _OOMSignal(f"simulated OOM at size {chunk_size}")
        text = "\n".join(f"p{p:03d}" for p in range(page_start, page_end))
        return text, {"runtime_seconds": 0.02,
                       "peak_vram_allocated_mib": 6500,
                       "peak_vram_reserved_mib": 7000,
                       "output_files_written": page_end - page_start}
    fn.call_log = call_log  # type: ignore[attr-defined]
    return fn


# ── End-to-end orchestrator tests ───────────────────────────────────────


def test_small_pdf_processed_as_single_chunk():
    """25-page PDF → one call, one chunk, no retries."""
    infer = _mk_success_infer()
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=25, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert len(chunks) == 1
    assert chunks[0]["page_start"] == 0
    assert chunks[0]["page_end"] == 24  # inclusive display end
    assert chunks[0]["page_count"] == 25
    assert chunks[0]["status"] == "PASS"
    assert chunks[0]["retry_count"] == 0
    assert infer.call_log == [(0, 25, 0, 40)]  # type: ignore[attr-defined]


def test_exactly_40_pages_single_chunk():
    infer = _mk_success_infer()
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=40, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert len(chunks) == 1
    assert chunks[0]["page_count"] == 40


def test_41_pages_splits_into_40_plus_1():
    infer = _mk_success_infer()
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=41, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert [(c["page_start"], c["page_end"] + 1) for c in chunks] == [(0, 40), (40, 41)]


def test_117_pages_splits_into_40_40_37():
    infer = _mk_success_infer()
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=117, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert [c["page_count"] for c in chunks] == [40, 40, 37]
    assert [(c["page_start"], c["page_end"] + 1) for c in chunks] == [
        (0, 40), (40, 80), (80, 117),
    ]
    # All PASS, no retries.
    assert all(c["status"] == "PASS" for c in chunks)
    assert all(c["retry_count"] == 0 for c in chunks)


def test_page_ordering_and_coverage_across_chunks():
    """Merged text must contain page markers in ascending order with no
    duplicates or gaps."""
    infer = _mk_success_infer(text_per_page="page_")
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=117, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    # Extract page numbers from the merged text.
    import re
    nums = [int(m.group(1)) for m in re.finditer(r"page_(\d+)", merged)]
    assert nums == list(range(117))  # exactly 0..116 in order, no repeats


def test_oom_at_40_falls_back_to_20():
    """40-page chunk OOMs → retry same range as 2×20-page chunks."""
    infer = _mk_oom_at_size({40})
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=40, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    # One OOM_RETRY entry for the initial 40-page attempt, then two
    # successful 20-page sub-chunks.
    kinds = [c["status"] for c in chunks]
    assert kinds == ["OOM_RETRY", "PASS", "PASS"]
    assert [c["attempted_chunk_size"] for c in chunks] == [40, 20, 20]
    assert [c["page_count"] for c in chunks[1:]] == [20, 20]
    # retry_count reflects the number of prior retries for this range.
    assert chunks[0]["retry_count"] == 0
    assert chunks[1]["retry_count"] == 1
    assert chunks[2]["retry_count"] == 1


def test_repeated_oom_down_to_one_page():
    """OOM at every size EXCEPT 1 → retries until each page is
    processed individually."""
    infer = _mk_oom_at_size({40, 20, 10, 5, 2})
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=2, inference_fn=infer, health_probe=_healthy_probe,
    )
    # Initial chunk 40 for 2 pages → 2-page effective, sees OOM at
    # attempted_size 40 → next 20 → OOM → 10 → OOM → 5 → OOM → 2 →
    # OOM → 1 → PASS on each page.
    assert err == ""
    # Verify the last two entries are PASS at chunk_size 1.
    assert chunks[-2]["status"] == "PASS"
    assert chunks[-1]["status"] == "PASS"
    assert chunks[-2]["attempted_chunk_size"] == 1
    assert chunks[-1]["attempted_chunk_size"] == 1
    # And the total number of retries at some point reached 5 (40→20→10→5→2→1).
    max_retry = max(c["retry_count"] for c in chunks)
    assert max_retry >= 5


def test_unhealthy_context_after_oom_halts_immediately():
    """OOM → health probe fails → return cuda_context_unhealthy_after_oom."""
    infer = _mk_oom_at_size({40})
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=40, inference_fn=infer, health_probe=_unhealthy_probe,
    )
    assert merged == ""
    assert "cuda_context_unhealthy_after_oom" in err
    # Exactly one OOM_RETRY entry recorded, then halt — no PASS chunks.
    assert len(chunks) == 1
    assert chunks[0]["status"] == "OOM_RETRY"


def test_non_oom_error_fails_fast():
    """A non-OOM exception must NOT trigger chunk reduction. Fail the
    document immediately."""
    def infer(page_start, page_end, chunk_index, chunk_size):
        raise ValueError("something else broke")

    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=20, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert merged == ""
    assert "chunk_failed_non_oom" in err
    assert len(chunks) == 1
    assert chunks[0]["status"] == "FAIL"
    assert chunks[0]["failure_category"] == "non_oom_error"


def test_diagnostics_contain_all_required_fields():
    """Every chunk row must contain the fields the reviewer specified."""
    infer = _mk_oom_at_size({40})
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=40, inference_fn=infer, health_probe=_healthy_probe,
    )
    required = {"chunk_index", "page_start", "page_end", "page_count",
                "attempted_chunk_size", "effective_chunk_size", "retry_count",
                "runtime_seconds", "peak_vram_allocated_mib",
                "peak_vram_reserved_mib", "status", "failure_category"}
    for row in chunks:
        missing = required - set(row.keys())
        assert not missing, f"chunk {row['chunk_index']} missing fields: {missing}"


def test_no_chunk_markers_in_merged_output():
    """The merged text must be one continuous document — the internal
    ``chunk_0000_p0000_0040/`` naming must NOT leak into the user-facing
    string."""
    infer = _mk_success_infer(text_per_page="page_")
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=117, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert "chunk_" not in merged  # no chunk sub-dir naming
    assert "OOM_RETRY" not in merged
    assert "page_start" not in merged
    assert "attempted_chunk_size" not in merged


def test_merged_output_deterministic_on_repeat():
    """Same inputs → same merged text. Basic determinism sanity check."""
    infer1 = _mk_success_infer()
    infer2 = _mk_success_infer()
    m1, _, _ = _process_chunks_with_reduction(117, infer1, _healthy_probe)
    m2, _, _ = _process_chunks_with_reduction(117, infer2, _healthy_probe)
    assert m1 == m2


def test_no_missing_or_duplicated_pages_after_partial_oom():
    """When some chunks OOM at 40 and succeed at 20, every source page
    must appear exactly once in the merged output."""
    infer = _mk_oom_at_size({40})
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=117, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    import re
    nums = [int(m.group(1)) for m in re.finditer(r"p(\d+)", merged)]
    assert nums == list(range(117))


# ── OOM classifier spot-check ───────────────────────────────────────────


def test_is_cuda_oom_detects_error_class():
    """The classifier must recognize an exception carrying an out-of-memory
    message even without torch installed."""
    assert _is_cuda_oom(RuntimeError("CUDA out of memory. Tried to allocate ..."))
    assert _is_cuda_oom(RuntimeError("Some outofmemoryerror text"))
    assert not _is_cuda_oom(ValueError("unrelated failure"))


# ── Backwards-compat: small-doc behaviour untouched ─────────────────────


def test_25_page_doc_result_shape_matches_pre_chunking_expectation():
    """For a small doc the diagnostics list has exactly one entry with
    no OOM and no retries — establishing that no chunking overhead
    contaminates the small-document path's shape."""
    infer = _mk_success_infer()
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=25, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert len(chunks) == 1
    c = chunks[0]
    assert c["status"] == "PASS"
    assert c["retry_count"] == 0
    assert c["attempted_chunk_size"] == 40
    assert c["effective_chunk_size"] == 25
    assert c["page_start"] == 0
    assert c["page_end"] == 24


# ── Retry-count monotonicity ────────────────────────────────────────────


def test_oom_at_size_40_records_retry_count_correctly():
    """The OOM row itself carries retry_count 0 (the first attempt).
    The successful retry rows carry retry_count 1."""
    infer = _mk_oom_at_size({40})
    merged, err, chunks = _process_chunks_with_reduction(
        total_pages=40, inference_fn=infer, health_probe=_healthy_probe,
    )
    assert err == ""
    assert chunks[0]["status"] == "OOM_RETRY"
    assert chunks[0]["retry_count"] == 0
    assert all(c["retry_count"] == 1 for c in chunks[1:])


# ── Hardware-aware initial chunk sizing ─────────────────────────────────


def _gib(n: float) -> int:
    return int(n * 1024 * 1024 * 1024)


def test_estimate_defaults_are_stable():
    """Guard the named constants so a silent value drift is caught."""
    assert _DEFAULT_SAFETY_FACTOR == 0.60
    assert _DEFAULT_PER_PAGE_MEMORY_MIB == 500
    assert _MAX_CHUNK_SIZE == 40
    assert _MIN_CHUNK_SIZE == 1


# Inputs to the sizing formula are WHOLE-DEVICE free VRAM before the
# model is loaded. The formula subtracts the model footprint
# (~6.5 GiB by default) before applying the safety factor. Tests below
# use realistic whole-device readings for cards of various sizes.


def test_estimate_6gib_card_almost_full_free():
    """6 GiB card with nothing else on it → ~5.5 GiB free. Subtract
    the ~6.5 GiB model footprint → 0 available for pages → min_size."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(5.5))
    assert size == _MIN_CHUNK_SIZE == 1
    assert signals["source"] == "vram-based"
    assert signals["available_for_pages_mib"] == 0


def test_estimate_8gib_card_full_free():
    """8 GiB card, ~7.5 GiB free. Subtract 6500 MiB → ~1180 MiB
    available. * 0.60 = 708 MiB / 500 MiB per page = 1."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(7.5))
    assert size == 1
    assert signals["source"] == "vram-based"
    assert signals["available_for_pages_mib"] > 0


def test_estimate_12gib_card_almost_full_free():
    """12 GiB RTX 3060, ~11.7 GiB whole-device free (nothing loaded).
    Subtract 6500 MiB → ~5480 MiB. * 0.60 = 3288 MiB / 500 = 6 pages.
    This is the reviewer's canonical case."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(11.7))
    assert size == 6
    assert signals["source"] == "vram-based"


def test_estimate_24gib_card_almost_full_free():
    """24 GiB RTX 4090, ~22 GiB free. Subtract 6500 → ~16028 MiB.
    * 0.60 = ~9616 MiB / 500 = 19 pages."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(22.0))
    assert size == 19
    assert signals["source"] == "vram-based"


def test_estimate_80gib_card_clamps_to_max():
    """80 GiB H100, ~78 GiB free. Formula would produce a large
    number; must clamp to _MAX_CHUNK_SIZE."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(78.0))
    assert size == _MAX_CHUNK_SIZE == 40
    assert signals["source"] == "vram-based"
    assert signals["raw_estimate"] >= _MAX_CHUNK_SIZE
    assert signals["clamped_to"] == _MAX_CHUNK_SIZE


def test_estimate_12gib_card_but_only_1gib_free():
    """12 GiB card with something else eating 10 GiB — model can't
    even load, let alone infer. Return min_size and let the caller
    (or the child, on its own OOM) decide what to do."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(1.0))
    assert size == _MIN_CHUNK_SIZE == 1
    assert signals["source"] == "vram-based"
    assert signals["available_for_pages_mib"] == 0


def test_estimate_model_footprint_override_lower():
    """Callers with a lighter model can override the footprint to
    unlock more pages on the same card."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(11.7),
        model_footprint_estimate_mib=2000,  # hypothetical smaller model
    )
    # Available = 11980 - 2000 = 9980 MiB. * 0.60 = 5988 / 500 = 11.
    assert size == 11
    assert signals["model_footprint_estimate_mib"] == 2000


def test_estimate_model_footprint_override_higher():
    """A heavier model reduces available memory."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(11.7),
        model_footprint_estimate_mib=10000,  # hypothetical larger model
    )
    # Available = 11980 - 10000 = 1980 MiB. * 0.60 = 1188 / 500 = 2.
    assert size == 2
    assert signals["model_footprint_estimate_mib"] == 10000


def test_estimate_cpu_only_returns_min_size():
    """No GPU / driver refused → return the safest size and a
    diagnostic source string, not a made-up number."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=None)
    assert size == _MIN_CHUNK_SIZE == 1
    assert signals["source"] == "cpu-or-unknown-vram-fallback"


def test_estimate_zero_free_vram_returns_min_size():
    """Zero-byte free reading should be treated the same as unknown."""
    size, signals = _estimate_initial_chunk_size(free_vram_bytes=0)
    assert size == _MIN_CHUNK_SIZE == 1
    assert signals["source"] == "cpu-or-unknown-vram-fallback"


def test_estimate_env_override_beats_vram_estimate():
    """Env override applies even when VRAM would produce a larger or
    smaller number. Operator judgment > formula."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(74.0),
        env_override="12",
    )
    assert size == 12
    assert signals["source"] == "env"
    assert signals["env_override_applied"] is True


def test_estimate_env_override_clamped_to_max():
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(74.0),
        env_override="9999",
    )
    assert size == _MAX_CHUNK_SIZE == 40
    assert signals["source"] == "env"
    assert signals["clamped_to"] == 40


def test_estimate_env_override_invalid_falls_back_to_vram():
    """A garbage env var must not override a good VRAM estimate."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(17.0),
        env_override="not-a-number",
    )
    assert size == 13  # VRAM-based, model-footprint-aware
    assert signals["source"] == "vram-based"
    assert signals["env_override_applied"] is False


def test_estimate_env_override_zero_falls_back_to_vram():
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(17.0),
        env_override="0",
    )
    assert size == 13
    assert signals["source"] == "vram-based"


def test_estimate_env_override_negative_falls_back_to_vram():
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(17.0),
        env_override="-5",
    )
    assert size == 13
    assert signals["source"] == "vram-based"


def test_estimate_env_override_empty_string_falls_back_to_vram():
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(17.0),
        env_override="",
    )
    assert size == 13
    assert signals["source"] == "vram-based"


def test_estimate_env_override_on_cpu_still_wins_when_valid():
    """If the operator explicitly sets an override, respect it even
    when we have no VRAM read (e.g. test fixture with a fake torch)."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=None,
        env_override="8",
    )
    assert size == 8
    assert signals["source"] == "env"


def test_estimate_pixel_heavy_pages_get_smaller_size():
    """A caller who knows the document renders at high DPI (heavy pages)
    should be able to override per_page_memory_estimate_mib upward and
    receive a smaller chunk size in return."""
    small_pages, _ = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(10.0),
        per_page_memory_estimate_mib=200,
    )
    heavy_pages, _ = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(10.0),
        per_page_memory_estimate_mib=1000,
    )
    assert small_pages > heavy_pages, (
        f"raising per-page cost must shrink the chunk, "
        f"got small={small_pages} heavy={heavy_pages}"
    )


def test_estimate_invalid_per_page_estimate_returns_min_size():
    """Guard against a caller passing 0 or a negative estimate — do not
    divide by zero, do not produce a giant number."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(10.0),
        per_page_memory_estimate_mib=0,
    )
    assert size == _MIN_CHUNK_SIZE == 1
    assert signals["source"] == "invalid-per-page-estimate-fallback"


def test_estimate_safety_factor_effect():
    """A lower safety_factor should produce a smaller (safer) chunk."""
    conservative, _ = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(10.0), safety_factor=0.30,
    )
    aggressive, _ = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(10.0), safety_factor=0.90,
    )
    assert aggressive > conservative


def test_estimate_min_max_bounds_can_be_overridden():
    """Callers can pass tighter bounds — e.g. to force a size of 1
    while probing, or to allow larger sizes in an internal test."""
    size, signals = _estimate_initial_chunk_size(
        free_vram_bytes=_gib(74.0),
        max_size=64,
    )
    assert size == 64
    assert signals["max_size"] == 64


def test_estimate_signals_are_json_serializable():
    """Downstream tooling logs signals as JSON — no numpy/torch types
    should sneak in."""
    import json
    _, signals = _estimate_initial_chunk_size(free_vram_bytes=_gib(12.0))
    json.dumps(signals)  # must not raise


def test_query_free_vram_returns_none_when_cuda_unavailable():
    """The thin wrapper never raises. torch stub returns
    cuda.is_available() = False → we get None back."""
    class _StubCuda:
        @staticmethod
        def is_available():
            return False

    class _StubTorch:
        cuda = _StubCuda

    assert _query_free_vram_bytes(_StubTorch) is None


def test_query_free_vram_returns_none_when_mem_get_info_raises():
    """Older torch or a hostile driver — mem_get_info may raise. The
    wrapper must swallow and return None so the sizing function goes
    to its fallback path."""
    class _StubCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def mem_get_info(_device):
            raise RuntimeError("driver said no")

    class _StubTorch:
        cuda = _StubCuda

    assert _query_free_vram_bytes(_StubTorch) is None


def test_query_free_vram_returns_int_bytes_on_normal_path():
    class _StubCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def mem_get_info(_device):
            return (8 * 1024**3, 12 * 1024**3)

    class _StubTorch:
        cuda = _StubCuda

    assert _query_free_vram_bytes(_StubTorch) == 8 * 1024**3
