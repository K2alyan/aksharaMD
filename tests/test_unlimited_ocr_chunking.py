"""Adaptive-chunking tests for the Unlimited-OCR adapter.

Exercises the pure ``_process_chunks_with_reduction`` orchestrator via
mocked ``inference_fn`` and ``health_probe`` callables. No CUDA, no
torch, no model, no PDFs — just the algorithm.

Also spot-checks the chunk range helpers and the OOM classifier.
"""
from __future__ import annotations

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter import (  # type: ignore
    _CHUNK_REDUCTION_SEQUENCE,
    _PREFERRED_CHUNK_SIZE,
    _initial_chunk_ranges,
    _is_cuda_oom,
    _next_smaller_chunk_size,
    _OOMSignal,
    _process_chunks_with_reduction,
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
    assert _PREFERRED_CHUNK_SIZE == 40


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
