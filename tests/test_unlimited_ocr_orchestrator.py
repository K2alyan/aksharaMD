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
        {"exit_code": EXIT_CUDA_OOM, "text": "", "json": {"error": "oom"}},
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
         "json": {"error": "cuda_context_unhealthy"}},
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
    The 1-page attempt itself failing triggers ``single_page_oom``."""
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
    assert signals["restart_count"] == 6
    assert signals["final_chunk_size_used"] == 1


def test_max_restarts_cap_enforced(tmp_path):
    """If we set max_restarts=2 and every attempt OOMs, we stop after 3
    attempts (the initial + 2 retries)."""
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([{"exit_code": EXIT_CUDA_OOM, "text": "", "json": {}}] * 5)
    text, exc, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=40,
        worker_runner=fake, max_restarts=2,
    )
    assert text == ""
    assert "max_restarts_reached" in exc
    assert len(signals["attempts"]) == 3  # 1 initial + 2 retries
    sizes = [a["chunk_size"] for a in signals["attempts"]]
    assert sizes == [40, 20, 10]


# ── Non-OOM failure paths ───────────────────────────────────────────────


def test_non_oom_failure_does_not_retry(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_NON_OOM_INFER_FAILURE, "text": "",
         "json": {"error": "something else"}},
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
         "json": {"error": "import broken"}},
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


def test_worker_reported_error_captured_in_attempt(tmp_path):
    pdf = _sample_pdf(tmp_path)
    fake = _FakeWorker([
        {"exit_code": EXIT_CUDA_OOM, "text": "",
         "json": {"error": "detailed_oom_message"}},
        {"exit_code": EXIT_OK, "text": "y",
         "json": {"signals": {}, "output_char_count": 1, "output_sha256": "y"}},
    ])
    _, _, signals = run_infer_pdf_isolated(
        pdf, tmp_path / "workdir", initial_chunk_size=8,
        worker_runner=fake,
    )
    assert signals["attempts"][0]["worker_reported_error"] == "detailed_oom_message"


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
