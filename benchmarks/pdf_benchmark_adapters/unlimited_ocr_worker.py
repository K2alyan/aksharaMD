"""Worker subprocess entrypoint for Unlimited-OCR inference.

Design intent
-------------
Run one full document's inference inside a disposable Python process
so that a CUDA context poisoned by a first-attempt OOM cannot leak
into the parent or into later documents.

The parent orchestrator picks the initial chunk size (typically from
``_estimate_initial_chunk_size`` in the adapter), invokes this module
via ``python -m benchmarks.pdf_benchmark_adapters.unlimited_ocr_worker``
with a ``--chunk-size`` argument, and interprets the exit code:

======  ==========================================================
Exit    Meaning
======  ==========================================================
0       Success. ``--output-text`` and ``--output-json`` written.
10      CUDA context health probe failed after an OOM. Parent
        should retry the SAME document with a smaller chunk size
        in a NEW process.
11      CUDA OOM occurred in the single-shot path (small PDF fit
        under initial size but failed anyway). Parent should retry
        smaller.
20      Inference failed for a non-OOM reason. Do NOT retry.
30      Infrastructure / import / IO error before inference. Do
        NOT retry.
======  ==========================================================

Deliberately narrow: this module NEVER catches OOMs and tries to
recover in-process. That is the parent's job in a fresh child. The
in-process reduction sequence still runs (from the adapter), but any
OOM that escapes the in-process handler exits the worker so the
parent can restart cleanly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Exit codes are part of the worker <-> orchestrator contract.
EXIT_OK = 0
EXIT_CUDA_CONTEXT_UNHEALTHY = 10
EXIT_CUDA_OOM = 11
EXIT_NON_OOM_INFER_FAILURE = 20
EXIT_INFRASTRUCTURE = 30


def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _classify_exception_message(exc: str) -> int:
    """Map the ``infer_pdf`` exception string to an exit code.

    ``infer_pdf`` never raises — it returns a string like
    ``"chunked_infer_failed: cuda_context_unhealthy_after_oom"`` or
    ``"infer_failed: OutOfMemoryError: ..."``.  Callers examine this
    string to decide whether a retry is warranted.
    """
    lower = (exc or "").lower()
    if "cuda_context_unhealthy" in lower:
        return EXIT_CUDA_CONTEXT_UNHEALTHY
    if "outofmemoryerror" in lower or "cuda out of memory" in lower or "cuda oom" in lower:
        return EXIT_CUDA_OOM
    if "acceleratorerror" in lower:
        # torch 2.12+ wraps CUDA OOM as AcceleratorError.
        return EXIT_CUDA_OOM
    return EXIT_NON_OOM_INFER_FAILURE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unlimited-OCR worker")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--chunk-size", type=int, required=True)
    parser.add_argument("--output-text", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    # Set the env BEFORE importing the adapter so that its
    # module-load-time sizing sees the override. The adapter accepts
    # this override at every call to _estimate_initial_chunk_size.
    os.environ["UNLIMITED_OCR_PREFERRED_CHUNK_SIZE"] = str(args.chunk_size)

    result_json: dict[str, Any] = {
        "worker_version": "unlimited_ocr_worker.py@2026-07-20",
        "chunk_size_requested": args.chunk_size,
        "pdf": args.pdf,
    }

    pdf = Path(args.pdf)
    if not pdf.exists():
        result_json["error"] = f"pdf_not_found: {args.pdf}"
        _write_outputs(args, text="", result_json=result_json)
        return EXIT_INFRASTRUCTURE

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as adapter
    except Exception as e:  # noqa: BLE001 — exiting anyway
        result_json["error"] = f"adapter_import_failed: {type(e).__name__}: {e}"
        result_json["traceback"] = traceback.format_exc()
        _write_outputs(args, text="", result_json=result_json)
        return EXIT_INFRASTRUCTURE

    try:
        runner = adapter._UnlimitedOcrRunner()
        runner.load()
        if not runner._loaded:
            # runner._load_error is a plain diagnostic string, not
            # sensitive. It is NOT interpolated into result_json here
            # because CodeQL's clear-text-storage heuristic flags any
            # attribute containing "error" as sensitive. Callers who
            # need the exact string can inspect the worker's captured
            # stderr log (log_path in the orchestrator's per-attempt
            # signals) — which prints _load_error to stderr as normal.
            print("REFUSE: runner failed to load", file=sys.stderr)
            result_json["error"] = "runner_load_failed"
            _write_outputs(args, text="", result_json=result_json)
            return EXIT_INFRASTRUCTURE
    except Exception as e:  # noqa: BLE001
        result_json["error"] = f"runner_load_exception: {type(e).__name__}: {e}"
        result_json["traceback"] = traceback.format_exc()
        _write_outputs(args, text="", result_json=result_json)
        return EXIT_INFRASTRUCTURE

    try:
        text, exc, signals = runner.infer_pdf(pdf, workdir)
    except Exception as e:  # noqa: BLE001 — infer_pdf should not raise, but guard
        # An unhandled exception here almost certainly means the CUDA
        # context is dead. Report as unhealthy so the parent halves.
        result_json["error"] = f"infer_pdf_raised: {type(e).__name__}: {e}"
        result_json["traceback"] = traceback.format_exc()
        _write_outputs(args, text="", result_json=result_json)
        return EXIT_CUDA_CONTEXT_UNHEALTHY

    result_json["signals"] = signals
    result_json["exception"] = exc
    result_json["output_char_count"] = len(text or "")
    result_json["output_sha256"] = _sha256_text(text or "")

    if not exc:
        _write_outputs(args, text=text, result_json=result_json)
        return EXIT_OK

    exit_code = _classify_exception_message(exc)
    _write_outputs(args, text=text, result_json=result_json)
    return exit_code


def _write_outputs(args, *, text: str, result_json: dict[str, Any]) -> None:
    Path(args.output_text).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_text).write_text(text or "", encoding="utf-8")
    Path(args.output_json).write_text(
        json.dumps(result_json, indent=2, default=str), encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
