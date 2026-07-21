"""Parent-side orchestrator for subprocess-isolated Unlimited-OCR
inference.

Purpose
-------
Run one Unlimited-OCR document inference inside a disposable child
process. If that child exits with a CUDA-OOM signal, halve the chunk
size and spawn a fresh child. Repeat until success, exhaustion, or a
non-OOM error.

This is PR 2 of 3 in the portable large-document strategy:
  * PR 1 (shipped): hardware-aware initial chunk sizing.
  * PR 2 (this):    subprocess-per-document with kill-and-restart.
  * PR 3 (next):    persistent safe-size cache.

Deliberate constraints
----------------------
* One subprocess per DOCUMENT, not per chunk. The child cold-loads the
  model once and processes the entire document, so the common (no-OOM)
  path pays exactly one model-load overhead.
* On OOM the parent CANNOT reuse the child's process — a poisoned CUDA
  context is sticky. It kills the child and spawns a new one.
* The halving policy is ``next = max(1, current // 2)``. Any positive
  starting size is legal; we do not restrict to the ``40 → 20 → 10``
  sequence.
* ``max_restarts`` caps the retry chain. Default is 6 which covers
  ``40 → 20 → 10 → 5 → 2 → 1``. A one-page OOM returns a structured
  failure — the model itself cannot fit.
* Restart re-runs the WHOLE document from page 0. No resumable
  protocol yet; adding one is a separate design.
* Never skip or duplicate pages: since the child either completes the
  full document or fails cleanly, page-level integrity is guaranteed
  by the successful child's own chunk merging.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Exit codes must match unlimited_ocr_worker.
EXIT_OK = 0
EXIT_CUDA_CONTEXT_UNHEALTHY = 10
EXIT_CUDA_OOM = 11
EXIT_NON_OOM_INFER_FAILURE = 20
EXIT_INFRASTRUCTURE = 30

_OOM_EXIT_CODES = frozenset({EXIT_CUDA_CONTEXT_UNHEALTHY, EXIT_CUDA_OOM})
_DEFAULT_MAX_RESTARTS = 6

# Per-worker stdout+stderr cap. Pathological model output (a runaway
# generation loop, a stuck-in-loop tokenizer) could otherwise fill the
# disk. 10 MiB is well above normal transformers warnings/info volume
# for a 117-page document but small enough that a mistake is bounded.
_DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_TRUNCATION_MARKER = (
    b"\n\n[...worker log truncated by orchestrator: exceeded log_max_bytes...]\n\n"
)

# CI / test hooks — the runner is injected so tests can substitute a
# pure-python stub in place of ``subprocess.run``.
_WorkerRunner = Callable[[list[str], int | None, Path], "_WorkerResult"]


class _WorkerResult:
    """Result of one worker invocation.

    Attributes
    ----------
    exit_code:
        Integer exit code. -1 on timeout.
    wall_seconds:
        Wall-clock time for the invocation.
    timed_out:
        True if the subprocess timed out.
    log_path:
        Path to the captured worker stdout+stderr log.
    """
    __slots__ = ("exit_code", "wall_seconds", "timed_out", "log_path")

    def __init__(
        self,
        exit_code: int,
        wall_seconds: float,
        timed_out: bool,
        log_path: Path,
    ) -> None:
        self.exit_code = exit_code
        self.wall_seconds = wall_seconds
        self.timed_out = timed_out
        self.log_path = log_path


def _default_worker_runner(
    cmd: list[str], timeout: int | None, log_path: Path,
) -> _WorkerResult:
    t0 = time.perf_counter()
    with open(log_path, "wb") as logf:
        try:
            proc = subprocess.run(  # nosec B603 — constant argv, no shell
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            # subprocess.run's timeout branch kills the child before
            # raising. On Windows this is TerminateProcess; on POSIX
            # it is SIGKILL. In both cases the child is gone before
            # we return, so the parent cannot leak an orphaned worker.
            exit_code = -1
            timed_out = True
    wall = round(time.perf_counter() - t0, 2)
    return _WorkerResult(
        exit_code=exit_code, wall_seconds=wall,
        timed_out=timed_out, log_path=log_path,
    )


def _truncate_log_if_over_cap(log_path: Path, cap_bytes: int) -> None:
    """Cap the log file to ``cap_bytes`` by keeping the first and last
    ``cap_bytes // 2`` bytes with a marker in between. This preserves
    both the model-load / config info at the start AND the failure
    signature at the end, which is what a reader needs from a runaway
    worker."""
    try:
        size = log_path.stat().st_size
    except OSError:
        return
    if size <= cap_bytes:
        return
    half = max(1, cap_bytes // 2)
    try:
        with open(log_path, "rb") as f:
            head = f.read(half)
            f.seek(-half, 2)  # from end
            tail = f.read(half)
        with open(log_path, "wb") as f:
            f.write(head)
            f.write(_LOG_TRUNCATION_MARKER)
            f.write(tail)
    except OSError:
        # If truncation fails we leave the log alone rather than lose it.
        return


def _next_chunk_size(current: int) -> int | None:
    """Halving reduction. Returns None if current is already 1 (no
    smaller size to try)."""
    if current <= 1:
        return None
    return max(1, current // 2)


def _read_worker_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — corrupt JSON must not crash the parent
        return {}


def _read_worker_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _read_log_tail(log_path: Path, cap_bytes: int = 4096) -> str:
    """Read up to the last ``cap_bytes`` of the worker log so callers
    can inspect the failure without needing filesystem access to the
    temp directory. Never raises."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size <= cap_bytes:
                f.seek(0)
                return f.read().decode("utf-8", errors="replace")
            f.seek(-cap_bytes, 2)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _classify_outcome(exit_code: int, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if exit_code == EXIT_OK:
        return "success"
    if exit_code in _OOM_EXIT_CODES:
        return "oom_retry"
    if exit_code == EXIT_NON_OOM_INFER_FAILURE:
        return "non_oom_failure"
    if exit_code == EXIT_INFRASTRUCTURE:
        return "infrastructure_error"
    return f"unknown_exit_{exit_code}"


def run_infer_pdf_isolated(
    pdf: Path,
    workdir: Path,
    initial_chunk_size: int,
    *,
    max_restarts: int = _DEFAULT_MAX_RESTARTS,
    per_run_timeout_seconds: int | None = None,
    log_max_bytes: int = _DEFAULT_LOG_MAX_BYTES,
    worker_runner: _WorkerRunner | None = None,
    python_executable: str | None = None,
    worker_module: str = "aksharamd.plugins.ocr_backends.unlimited_ocr.worker",
) -> tuple[str, str, dict[str, Any]]:
    """Run one document through a disposable worker subprocess, halving
    the chunk size and retrying on CUDA-OOM signals.

    Returns
    -------
    ``(text, exception_or_empty, signals)`` — identical shape to
    ``_UnlimitedOcrRunner.infer_pdf`` so callers can swap implementations.

    Parameters
    ----------
    pdf:
        Path to the PDF to infer.
    workdir:
        Scratch directory used by the child.
    initial_chunk_size:
        First attempt's chunk size. Typically the output of
        ``_estimate_initial_chunk_size`` in the adapter.
    max_restarts:
        Maximum number of shrink-and-retry attempts *after* the first.
        Attempt 1 always runs; up to ``max_restarts`` further attempts
        may follow on OOM.
    per_run_timeout_seconds:
        Wall-clock cap per subprocess. ``None`` disables.
    log_max_bytes:
        Cap on the per-attempt worker stdout+stderr log file. If a
        run exceeds it, the log is truncated to head+tail with a
        marker. Prevents pathological model output from filling the
        disk. Default is 10 MiB.
    worker_runner:
        Injection seam for tests. Defaults to real subprocess.run.
    python_executable:
        Interpreter to launch. Defaults to ``sys.executable`` (so the
        child inherits the parent's Python).
    worker_module:
        ``-m`` target for the child. Overridable for tests.
    """
    if initial_chunk_size <= 0:
        raise ValueError(f"initial_chunk_size must be positive, got {initial_chunk_size}")
    if max_restarts < 0:
        raise ValueError(f"max_restarts must be >= 0, got {max_restarts}")
    runner = worker_runner or _default_worker_runner
    python = python_executable or sys.executable

    workdir.mkdir(parents=True, exist_ok=True)
    # Short tmpdir prefix — every directory layer under the parent
    # workdir costs path budget on Windows (MAX_PATH 260). See
    # benchmarks/a2_geotopo_portable_validation.py for the rationale.
    with tempfile.TemporaryDirectory(prefix="orch_", dir=str(workdir)) as tmp_str:
        tmp = Path(tmp_str)
        attempts: list[dict[str, Any]] = []
        current_size = initial_chunk_size
        final_text = ""
        final_exception: str
        final_worker_signals: dict[str, Any] = {}
        total_wall = 0.0
        # restart_count counts the number of times the parent actually
        # spawned a fresh child AFTER an OOM (i.e. transitions between
        # attempts). It is NOT the number of OOMs observed — the final
        # OOM that exhausts the retry budget does not lead to another
        # restart, so it is not counted.
        restart_count = 0
        current_final_size = initial_chunk_size

        for attempt_index in range(1, max_restarts + 2):
            # Shortened from "attempt_NN_size_NNNN" to "a<N>s<N>"
            # to save Windows MAX_PATH budget. Concrete values
            # remain in each attempt row's ``chunk_size`` field.
            attempt_dir = tmp / f"a{attempt_index}s{current_size}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            out_text = attempt_dir / "output.md"
            out_json = attempt_dir / "output.json"
            log_path = attempt_dir / "worker.log"

            cmd = [
                python, "-m", worker_module,
                "--pdf", str(pdf),
                "--workdir", str(attempt_dir / "workdir"),
                "--chunk-size", str(current_size),
                "--output-text", str(out_text),
                "--output-json", str(out_json),
            ]
            wr = runner(cmd, per_run_timeout_seconds, log_path)
            total_wall += wr.wall_seconds

            # Cap the worker log AFTER the child has exited (its file
            # handle is closed by _default_worker_runner) so a runaway
            # process cannot fill the disk.
            _truncate_log_if_over_cap(log_path, log_max_bytes)

            outcome = _classify_outcome(wr.exit_code, wr.timed_out)
            worker_json = _read_worker_json(out_json)

            # Precompute retryability + next size so the row is
            # self-describing without needing to correlate with the
            # subsequent loop iteration. Only ``oom_retry`` is
            # retryable, and only if we have retries left AND a
            # smaller size to try.
            retryable = False
            next_chunk_size_row: int | None = None
            if outcome == "oom_retry" and attempt_index <= max_restarts:
                candidate = _next_chunk_size(current_size)
                if candidate is not None:
                    retryable = True
                    next_chunk_size_row = candidate

            attempts.append({
                "attempt": attempt_index,
                "chunk_size": current_size,
                "exit_code": wr.exit_code,
                "timed_out": wr.timed_out,
                "wall_seconds": wr.wall_seconds,
                "outcome": outcome,
                "log_path": str(log_path),
                "output_json_path": str(out_json),
                "output_text_path": str(out_text),
                # Coarse category label emitted by the worker's
                # narrow JSON contract; deliberately not a raw
                # exception message. Full diagnostic text is in
                # the captured worker log at ``log_path`` and its
                # last ~4 KB is duplicated below.
                "worker_reported_stage": worker_json.get("failure_stage"),
                # Reviewer's Fix 4 diagnostics — enough to explain
                # what happened without inspecting tmpdir files.
                "retryable": retryable,
                "next_chunk_size": next_chunk_size_row,
                "worker_stdout_tail": _read_log_tail(log_path),
            })
            current_final_size = current_size

            if outcome == "success":
                # ONLY read the text on a clean success. Partial text
                # from a killed child is never presented as a document.
                final_text = _read_worker_text(out_text)
                final_exception = ""
                final_worker_signals = worker_json.get("signals") or {}
                break

            if outcome == "oom_retry":
                if not retryable:
                    # Either we consumed the last permitted attempt or
                    # we cannot halve further (already at size 1).
                    # Both are terminal. Do NOT increment restart_count.
                    if attempt_index > max_restarts:
                        final_exception = (
                            "isolated_infer_failed: max_restarts_reached_at_size_"
                            f"{current_size}"
                        )
                    else:
                        final_exception = "isolated_infer_failed: single_page_oom"
                    break
                # Retryable: next_chunk_size_row is non-None by definition.
                assert next_chunk_size_row is not None  # nosec B101 — invariant
                current_size = next_chunk_size_row
                restart_count += 1  # About to spawn a fresh child.
                continue

            if outcome == "timeout":
                final_exception = (
                    f"isolated_infer_failed: worker_timeout_at_size_{current_size}"
                )
                break
            if outcome == "non_oom_failure":
                final_exception = (
                    f"isolated_infer_failed: non_oom_error_at_size_{current_size}"
                )
                break
            if outcome == "infrastructure_error":
                final_exception = (
                    f"isolated_infer_failed: infrastructure_error_at_size_{current_size}"
                )
                break
            # Unknown exit code — do NOT retry, this is not classified
            # as OOM. Rules out spurious retries from segfaults, Windows
            # access violations, OS OOM-killer, etc.
            final_exception = f"isolated_infer_failed: unknown_exit_{wr.exit_code}"
            break
        else:
            # Loop completed without hitting a break — defensive path
            # only reached if the range yielded no iterations (i.e.
            # max_restarts < 0, but that is rejected at entry).
            final_exception = "isolated_infer_failed: retry_loop_exhausted_without_result"

        signals: dict[str, Any] = {
            "isolation_mode": "subprocess",
            "initial_chunk_size": initial_chunk_size,
            "final_chunk_size_used": current_final_size,
            "attempts": attempts,
            "restart_count": restart_count,
            "max_restarts": max_restarts,
            "total_wall_seconds": round(total_wall, 2),
            "worker_signals": final_worker_signals,
        }
        return final_text, final_exception, signals
