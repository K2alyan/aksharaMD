"""Tests for the subprocess-isolated Unlimited-OCR orchestrator.

No CUDA, no torch, no real subprocess — every worker invocation is
faked via the ``worker_runner`` injection seam.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_orchestrator import (  # type: ignore
    EXIT_CUDA_CONTEXT_UNHEALTHY,
    EXIT_CUDA_OOM,
    EXIT_INFRASTRUCTURE,
    EXIT_NON_OOM_INFER_FAILURE,
    EXIT_OK,
    _classify_outcome,
    _next_chunk_size,
    _truncate_log_if_over_cap,
    _WorkerResult,
    run_infer_pdf_isolated,
)

# ── Halving policy ──────────────────────────────────────────────────────


def test_next_chunk_size_halves():
    assert _next_chunk_size(40) == 20
    assert _next_chunk_size(20) == 10
    assert _next_chunk_size(10) == 5
    assert _next_chunk_size(5) == 2  # 5 // 2 == 2
    assert _next_chunk_size(4) == 2
    assert _next_chunk_size(3) == 1


def test_next_chunk_size_bottoms_at_1():
    assert _next_chunk_size(2) == 1
    assert _next_chunk_size(1) is None
    assert _next_chunk_size(0) is None


# ── Outcome classifier ──────────────────────────────────────────────────


def test_classify_outcome_maps_exit_codes():
    assert _classify_outcome(EXIT_OK, False) == "success"
    assert _classify_outcome(EXIT_CUDA_OOM, False) == "oom_retry"
    assert _classify_outcome(EXIT_CUDA_CONTEXT_UNHEALTHY, False) == "oom_retry"
    assert _classify_outcome(EXIT_NON_OOM_INFER_FAILURE, False) == "non_oom_failure"
    assert _classify_outcome(EXIT_INFRASTRUCTURE, False) == "infrastructure_error"
    assert _classify_outcome(0, True) == "timeout"
    assert _classify_outcome(99, False).startswith("unknown_exit_")


# ── Test doubles ────────────────────────────────────────────────────────


class _FakeWorker:
    """Scripted worker: on each call, yields the next scripted outcome
    from a list.  Also writes canned output-text and output-json files
    that the orchestrator will read after a success."""

    def __init__(self, script: list[dict[str, Any]]):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], timeout: int | None, log_path: Path) -> _WorkerResult:
        step = self.script.pop(0)
        # Extract the paths the worker was told to write. Their layout
        # matches the CLI in ``unlimited_ocr_orchestrator.run_infer_pdf_
        # isolated``.
        out_text = _cli_value(cmd, "--output-text")
        out_json = _cli_value(cmd, "--output-json")
        chunk_size = int(_cli_value(cmd, "--chunk-size"))
        # Log the invocation for inspection.
        self.calls.append({
            "chunk_size": chunk_size,
            "out_text": out_text,
            "out_json": out_json,
        })
        # Write outputs matching the scripted outcome.
        Path(out_text).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_text).write_text(step.get("text", ""), encoding="utf-8")
        Path(out_json).write_text(
            json.dumps(step.get("json", {}), default=str),
            encoding="utf-8",
        )
        # Write an empty log file so the log_path attribute is real.
        Path(log_path).write_text(step.get("log", ""), encoding="utf-8")
        return _WorkerResult(
            exit_code=step["exit_code"],
            wall_seconds=step.get("wall", 1.0),
            timed_out=step.get("timed_out", False),
            log_path=log_path,
        )


def _cli_value(cmd: list[str], flag: str) -> str:
    idx = cmd.index(flag)
    return cmd[idx + 1]


def _sample_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake\n")
    return p


# ── Happy path ──────────────────────────────────────────────────────────


def test_first_attempt_success(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_OK, "text": "hello world",
         "json": {"signals": {"page_count": 3},
                  "output_char_count": 11, "output_sha256": "abc"}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert exc == ""
    assert text == "hello world"
    assert signals["isolation_mode"] == "subprocess"
    assert signals["initial_chunk_size"] == 8
    assert signals["final_chunk_size_used"] == 8
    assert signals["restart_count"] == 0
    assert len(signals["attempts"]) == 1
    assert signals["attempts"][0]["chunk_size"] == 8
    assert signals["attempts"][0]["outcome"] == "success"
    assert signals["worker_signals"] == {"page_count": 3}


# ── OOM retry path ──────────────────────────────────────────────────────


def test_one_oom_then_success_halves_size(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {"failure_stage": "oom"}},
        {"exit_code": EXIT_OK, "text": "done",
         "json": {"signals": {"page_count": 10},
                  "output_char_count": 4, "output_sha256": "def"}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake,
    )
    assert exc == ""
    assert text == "done"
    assert signals["restart_count"] == 1
    sizes = [a["chunk_size"] for a in signals["attempts"]]
    assert sizes == [40, 20]  # halved
    assert signals["final_chunk_size_used"] == 20


def test_context_unhealthy_treated_same_as_oom(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_CONTEXT_UNHEALTHY, "text": "",
         "json": {"failure_stage": "cuda_context_unhealthy"}},
        {"exit_code": EXIT_OK, "text": "ok",
         "json": {"signals": {}, "output_char_count": 2, "output_sha256": "x"}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert exc == ""
    assert signals["restart_count"] == 1
    assert signals["attempts"][0]["outcome"] == "oom_retry"


def test_three_ooms_then_success_full_sequence(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "yes",
         "json": {"signals": {}, "output_char_count": 3, "output_sha256": "y"}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake,
    )
    assert exc == ""
    assert text == "yes"
    sizes = [a["chunk_size"] for a in signals["attempts"]]
    assert sizes == [40, 20, 10, 5]
    assert signals["restart_count"] == 3


def test_always_oom_exhausts_and_reports_single_page_fail(tmp_path):
    """Six OOMs cover 40 → 20 → 10 → 5 → 2 → 1 → (next is None).
    The 1-page attempt itself failing triggers ``single_page_oom``.
    restart_count is 5 (the transitions between sizes), NOT 6 —
    the final OOM at size 1 does not lead to another restart."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([{"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}}] * 6)
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake, max_restarts=6,
    )
    assert text == ""
    assert exc == "isolated_infer_failed: single_page_oom"
    sizes = [a["chunk_size"] for a in signals["attempts"]]
    assert sizes == [40, 20, 10, 5, 2, 1]
    assert len(signals["attempts"]) == 6
    assert signals["restart_count"] == 5  # 5 transitions between 6 attempts
    assert signals["final_chunk_size_used"] == 1


def test_max_restarts_cap_enforced(tmp_path):
    """If we set max_restarts=2 and every attempt OOMs, we stop after 3
    attempts (the initial + 2 restarts). restart_count is exactly 2:
    the terminal OOM does not lead to another restart."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([{"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}}] * 5)
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake, max_restarts=2,
    )
    assert text == ""
    assert "max_restarts_reached" in exc
    assert len(signals["attempts"]) == 3  # 1 initial + 2 restarts
    assert signals["restart_count"] == 2  # exactly max_restarts
    sizes = [a["chunk_size"] for a in signals["attempts"]]
    assert sizes == [40, 20, 10]


# ── Non-OOM failure paths ───────────────────────────────────────────────


def test_non_oom_failure_does_not_retry(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_NON_OOM_INFER_FAILURE, "text": "",
         "json": {"failure_stage": "something else"}},
        # More scripted steps would raise if consumed — assert they aren't.
        {"exit_code": EXIT_OK, "text": "should never run", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert text == ""
    assert "non_oom_error" in exc
    assert len(signals["attempts"]) == 1  # no retry
    assert len(fake.script) == 1  # second scripted step untouched


def test_infrastructure_error_does_not_retry(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_INFRASTRUCTURE, "text": "",
         "json": {"failure_stage": "import broken"}},
        {"exit_code": EXIT_OK, "text": "unreached", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert text == ""
    assert "infrastructure_error" in exc
    assert len(signals["attempts"]) == 1


def test_timeout_does_not_retry(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": -1, "text": "", "json": {}, "timed_out": True},
        {"exit_code": EXIT_OK, "text": "unreached", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake, per_run_timeout_seconds=60,
    )
    assert text == ""
    assert "worker_timeout" in exc
    assert signals["attempts"][0]["timed_out"] is True


def test_unknown_exit_code_does_not_retry(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": 99, "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "unreached", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=4,
        worker_runner=fake,
    )
    assert text == ""
    assert "unknown_exit_99" in exc
    assert len(signals["attempts"]) == 1


# ── Signal integrity ────────────────────────────────────────────────────


def test_signals_are_json_serializable(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_OK, "text": "x",
         "json": {"signals": {"page_count": 1},
                  "output_char_count": 1, "output_sha256": "z"}},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    json.dumps(signals)  # must not raise


def test_total_wall_seconds_accumulates_across_attempts(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}, "wall": 30.0},
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}, "wall": 45.0},
        {"exit_code": EXIT_OK, "text": "x",
         "json": {"signals": {}, "output_char_count": 1, "output_sha256": "s"},
         "wall": 120.0},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake,
    )
    assert signals["total_wall_seconds"] == pytest.approx(195.0)


def test_worker_reported_stage_captured_in_attempt(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "",
         "json": {"failure_stage": "detailed_oom_message"}},
        {"exit_code": EXIT_OK, "text": "y",
         "json": {"signals": {}, "output_char_count": 1, "output_sha256": "y"}},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert signals["attempts"][0]["worker_reported_stage"] == "detailed_oom_message"


# ── Input validation ────────────────────────────────────────────────────


def test_zero_initial_chunk_size_rejected(tmp_path):
    pdf = _sample_pdf(tmp_path)
    with pytest.raises(ValueError, match="initial_chunk_size must be positive"):
        run_infer_pdf_isolated(
            pdf, tmp_path / "workdir", initial_chunk_size=0,
            worker_runner=_FakeWorker([]),
        )


def test_negative_initial_chunk_size_rejected(tmp_path):
    pdf = _sample_pdf(tmp_path)
    with pytest.raises(ValueError, match="initial_chunk_size must be positive"):
        run_infer_pdf_isolated(
            pdf, tmp_path / "workdir", initial_chunk_size=-1,
            worker_runner=_FakeWorker([]),
        )


def test_negative_max_restarts_rejected(tmp_path):
    pdf = _sample_pdf(tmp_path)
    with pytest.raises(ValueError, match="max_restarts must be >= 0"):
        run_infer_pdf_isolated(
            pdf, tmp_path / "workdir", initial_chunk_size=8, max_restarts=-1,
            worker_runner=_FakeWorker([]),
        )


def test_zero_max_restarts_runs_exactly_one_attempt(tmp_path):
    """max_restarts=0 means: run once, no retries."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "unreached", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8, max_restarts=0,
        worker_runner=fake,
    )
    assert text == ""
    assert "max_restarts_reached" in exc
    assert len(signals["attempts"]) == 1
    assert signals["restart_count"] == 0


# ── Verification-point coverage (per reviewer's PR 2 checklist) ─────────


def test_verify_1_each_retry_uses_fresh_worker_invocation(tmp_path):
    """After an OOM, the parent MUST call the worker runner again with
    a fresh cmd list (i.e. a new subprocess). Verified by counting
    distinct calls to the runner across the retry chain."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "ok",
         "json": {"signals": {}, "output_char_count": 2, "output_sha256": "z"}},
    ])
    run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40, worker_runner=fake,
    )
    # Distinct invocations, each with a distinct output-json path.
    out_jsons = [c["out_json"] for c in fake.calls]
    assert len(out_jsons) == 3
    assert len(set(out_jsons)) == 3  # all fresh child working directories


def test_verify_2_no_infinite_loop_at_size_1(tmp_path):
    """Even with a very high max_restarts, size 1 is the terminal
    stop — no infinite loop, no retry at size 1 after failure."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker(
        [{"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}}] * 20,
    )
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8, max_restarts=100,
        worker_runner=fake,
    )
    assert text == ""
    assert exc == "isolated_infer_failed: single_page_oom"
    # 8 -> 4 -> 2 -> 1 -> None. Exactly 4 attempts, no more.
    assert len(signals["attempts"]) == 4
    sizes = [a["chunk_size"] for a in signals["attempts"]]
    assert sizes == [8, 4, 2, 1]


def test_verify_4_windows_style_status_code_treated_as_unknown(tmp_path):
    """Windows access violation (0xC0000005 as unsigned int) or a
    Linux SIGSEGV (-11) must not be mistaken for OOM."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": 0xC0000005 - (1 << 32),  # -1073741819, Python's sign
         "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "unreached", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert text == ""
    assert "unknown_exit_" in exc
    assert len(signals["attempts"]) == 1


def test_verify_4_sigkill_style_status_treated_as_unknown(tmp_path):
    """Linux OS OOM-killer usually leaves a -9 exit code. Do NOT retry."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": -9, "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "unreached", "json": {}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert text == ""
    assert "unknown_exit_-9" in exc
    assert len(signals["attempts"]) == 1


def test_verify_5_partial_text_from_failed_worker_never_returned(tmp_path):
    """A killed child may have written partial output to disk before
    dying. If exit_code != OK, the parent MUST NOT return that text."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        # Worker crashed with EXIT_CUDA_OOM but managed to write half
        # a document before dying. The parent must ignore this text.
        {"exit_code": EXIT_CUDA_OOM,
         "text": "partial document that should NEVER surface",
         "json": {"failure_stage": "oom"}},
        {"exit_code": EXIT_OK, "text": "complete document",
         "json": {"signals": {}, "output_char_count": 18, "output_sha256": "aa"}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert exc == ""
    # The parent must return the second attempt's text, not the first.
    assert text == "complete document"
    assert "partial" not in text


def test_verify_5_partial_text_ignored_on_terminal_failure(tmp_path):
    """When the last permitted attempt OOMs (exhausting max_restarts),
    the partial text from that attempt must not be returned either."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM,
         "text": "please do not use this",
         "json": {"failure_stage": "oom"}},
    ])
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8, max_restarts=0,
        worker_runner=fake,
    )
    assert text == ""  # empty, not the partial
    assert "max_restarts_reached" in exc


def test_verify_6_tmpdir_removed_after_success(tmp_path):
    """The orchestrator's per-invocation TemporaryDirectory must be
    cleaned up after a successful run — nothing leaks into workdir."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_OK, "text": "ok",
         "json": {"signals": {}, "output_char_count": 2, "output_sha256": "s"}},
    ])
    workdir = tmp_path / "workdir"
    run_infer_pdf_isolated(pdf, workdir, initial_chunk_size=8, worker_runner=fake)
    # Only the workdir itself should remain, no nested ocr_orchestrator_*
    remaining = list(workdir.iterdir())
    assert remaining == []


def test_verify_6_tmpdir_removed_after_failure(tmp_path):
    """Same cleanup guarantee on a failed run."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_NON_OOM_INFER_FAILURE, "text": "", "json": {}},
    ])
    workdir = tmp_path / "workdir"
    run_infer_pdf_isolated(pdf, workdir, initial_chunk_size=8, worker_runner=fake)
    assert list(workdir.iterdir()) == []


def test_verify_7_truncation_preserves_head_and_tail(tmp_path):
    """Direct unit test on the truncation helper. An oversized log
    keeps head (model-load info) and tail (failure signature) so a
    reader can still diagnose the run, with a marker in between."""
    log_path = tmp_path / "oversized.log"
    payload = (b"HEAD_MARKER cold-load starting model...\n" * 5000
               + b"MIDDLE_NOISE junk that must not survive\n" * 200000
               + b"TAIL_MARKER CUDA out of memory at chunk 3\n" * 5000)
    log_path.write_bytes(payload)
    _truncate_log_if_over_cap(log_path, cap_bytes=1024 * 1024)  # 1 MiB
    truncated = log_path.read_bytes()
    assert len(truncated) <= 1024 * 1024 + 1024  # slack for marker
    assert b"HEAD_MARKER" in truncated
    assert b"TAIL_MARKER" in truncated
    assert b"[...worker log truncated by orchestrator" in truncated


def test_verify_7_undersized_log_not_modified(tmp_path):
    """Logs below the cap are untouched — no every-run rewrite cost."""
    log_path = tmp_path / "small.log"
    payload = b"a small log with useful info\n" * 10
    log_path.write_bytes(payload)
    _truncate_log_if_over_cap(log_path, cap_bytes=1024 * 1024)
    assert log_path.read_bytes() == payload


def test_verify_7_truncation_handles_missing_log(tmp_path):
    """If the log file was never written (e.g. worker crashed at
    startup), truncation must not raise."""
    missing = tmp_path / "does_not_exist.log"
    _truncate_log_if_over_cap(missing, cap_bytes=1024)  # must not raise


def test_diagnostic_fix4_retryable_true_on_oom_with_headroom(tmp_path):
    """Fix 4: an OOM attempt that will be retried carries
    retryable=True and next_chunk_size = halved value."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {},
         "log": "traceback goes here"},
        {"exit_code": EXIT_OK, "text": "done",
         "json": {"signals": {}, "output_char_count": 4, "output_sha256": "x"}},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake,
    )
    first = signals["attempts"][0]
    assert first["retryable"] is True
    assert first["next_chunk_size"] == 20  # 40 // 2
    assert "traceback" in first["worker_stdout_tail"]


def test_diagnostic_fix4_retryable_false_on_last_permitted_oom(tmp_path):
    """The terminal OOM (max_restarts exhausted) carries retryable=False."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40, max_restarts=2,
        worker_runner=fake,
    )
    last = signals["attempts"][-1]
    assert last["retryable"] is False
    assert last["next_chunk_size"] is None


def test_diagnostic_fix4_retryable_false_on_single_page_oom(tmp_path):
    """Reaching size 1 and failing: retryable=False, next=None."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([{"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}}] * 4)
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=4, max_restarts=6,
        worker_runner=fake,
    )
    # 4 -> 2 -> 1 -> None. Last attempt at size 1.
    last = signals["attempts"][-1]
    assert last["chunk_size"] == 1
    assert last["retryable"] is False
    assert last["next_chunk_size"] is None


def test_diagnostic_fix4_retryable_false_on_non_oom(tmp_path):
    """Non-OOM failure: retryable=False regardless of retries remaining."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_NON_OOM_INFER_FAILURE, "text": "", "json": {}},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8, max_restarts=5,
        worker_runner=fake,
    )
    assert signals["attempts"][0]["retryable"] is False
    assert signals["attempts"][0]["next_chunk_size"] is None


def test_diagnostic_fix4_worker_stdout_tail_captured(tmp_path):
    """The last ~4 KB of the worker log is duplicated into the attempt
    row so callers can see it without accessing the tmpdir."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_OK, "text": "ok",
         "json": {"signals": {}, "output_char_count": 2, "output_sha256": "z"},
         "log": "worker startup line 1\nworker startup line 2\ninference done"},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    tail = signals["attempts"][0]["worker_stdout_tail"]
    assert "worker startup" in tail or "inference done" in tail


def test_verify_7_orchestrator_invokes_truncation(tmp_path, monkeypatch):
    """Verify the orchestrator actually CALLS the truncation function
    with the correct cap, once per attempt."""
    calls: list[tuple[str, int]] = []

    def _spy(log_path, cap_bytes):
        calls.append((str(log_path), cap_bytes))

    import benchmarks.pdf_benchmark_adapters.unlimited_ocr_orchestrator as orch
    monkeypatch.setattr(orch, "_truncate_log_if_over_cap", _spy)

    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}},
        {"exit_code": EXIT_OK, "text": "x",
         "json": {"signals": {}, "output_char_count": 1, "output_sha256": "s"}},
    ])
    run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake, log_max_bytes=42,
    )
    assert len(calls) == 2  # once per attempt
    assert all(cap == 42 for _, cap in calls)
