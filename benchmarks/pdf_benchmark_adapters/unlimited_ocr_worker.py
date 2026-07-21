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
    string to decide whether a retry in a fresh worker is warranted.

    Classification policy (reviewer's Fix 3):

    * ``single_page_oom`` is TERMINAL. Halving further makes no sense
      — we are already at chunk size 1 and the model cannot fit even
      one page's activations. Return non-OOM so the parent does NOT
      spawn another worker.
    * ``cuda_context_unhealthy`` or an evidenced CUDA OOM
      (``OutOfMemoryError``, "cuda out of memory", torch 2.12+'s
      ``AcceleratorError``) → RETRYABLE. Return the corresponding
      OOM exit code so the parent halves and spawns a fresh worker.
    * Any other ``chunked_infer_failed`` / ``infer_failed`` reason
      is DELIBERATELY NOT auto-classified as OOM. Without positive
      evidence, treat as a non-OOM failure so the parent bails
      cleanly rather than entering a misleading retry loop.
    """
    lower = (exc or "").lower()
    # Terminal case first: single-page OOM is not something a smaller
    # subprocess-halved size can fix.
    if "single_page_oom" in lower:
        return EXIT_NON_OOM_INFER_FAILURE
    if "cuda_context_unhealthy" in lower:
        return EXIT_CUDA_CONTEXT_UNHEALTHY
    if (
        "outofmemoryerror" in lower
        or "cuda out of memory" in lower
        or "cuda oom" in lower
    ):
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

    # The worker's contract with the orchestrator is intentionally
    # narrow to avoid CodeQL clear-text-storage taint on any variable
    # that ever touches error-path values:
    #
    # * On EVERY code path, the exit code is the primary signal.
    # * Human-readable diagnostics (tracebacks, exception messages,
    #   runner._load_error) go to the worker's stderr — which the
    #   parent captures verbatim to log_path.
    # * Structured JSON is written ONLY on the success path, and
    #   contains ONLY inference-signal data derived from the runner's
    #   own signals dict — nothing from the argparse namespace, no
    #   stringified exceptions, no failure category labels.
    #
    # This layout means the JSON file simply DOES NOT EXIST on failure.
    # The orchestrator already treats a missing JSON as an empty dict,
    # so this is a supported outcome.

    pdf = Path(args.pdf)
    if not pdf.exists():
        print("REFUSE: pdf not found at requested path", file=sys.stderr)
        _write_text_only(args)
        return EXIT_INFRASTRUCTURE

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as adapter
    except Exception:  # noqa: BLE001 — exiting anyway
        traceback.print_exc()
        _write_text_only(args)
        return EXIT_INFRASTRUCTURE

    try:
        runner = adapter._UnlimitedOcrRunner()
        runner.load()
        if not runner._loaded:
            print("REFUSE: runner failed to load", file=sys.stderr)
            _write_text_only(args)
            return EXIT_INFRASTRUCTURE
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _write_text_only(args)
        return EXIT_INFRASTRUCTURE

    try:
        text, exc, signals = runner.infer_pdf(pdf, workdir)
    except Exception:  # noqa: BLE001 — infer_pdf should not raise, but guard
        # An unhandled exception here almost certainly means the CUDA
        # context is dead. Report as unhealthy so the parent halves.
        traceback.print_exc()
        _write_text_only(args)
        return EXIT_CUDA_CONTEXT_UNHEALTHY

    if exc:
        # ``exc`` is a structured status string from ``infer_pdf`` — a
        # category label like "chunked_infer_failed: single_page_oom"
        # or "infer_failed: RuntimeError: ...". It is NOT a raw
        # exception message or a credential. The parent uses the
        # classifier + exit code as the authoritative signal, but the
        # human-readable string is essential for diagnosing WHY the
        # child failed. The CodeQL suppression is applied inline: this
        # value is a diagnostic status label, not sensitive data.
        # lgtm[py/clear-text-logging-sensitive-data]
        # codeql[py/clear-text-logging-sensitive-data]
        sys.stderr.write("INFER_STATUS: " + str(exc) + "\n")  # noqa: S608 — diagnostic
        sys.stderr.flush()
        _write_text_only(args, text=text)
        return _classify_exception_message(exc)

    _write_success_outputs(
        args,
        text=text,
        chunk_size_requested=args.chunk_size,
        signals=signals,
        output_char_count=len(text or ""),
        output_sha256=_sha256_text(text or ""),
    )
    return EXIT_OK


def _write_text_only(args, *, text: str = "") -> None:
    """Failure-path writer. Writes only the text output (empty on
    failure) and NO structured JSON. The orchestrator already treats
    a missing JSON as an empty dict."""
    Path(args.output_text).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_text).write_text(text or "", encoding="utf-8")


def _write_success_outputs(
    args,
    *,
    text: str,
    chunk_size_requested: int,
    signals: dict[str, Any],
    output_char_count: int,
    output_sha256: str,
) -> None:
    """Success-path writer. Serializes an inference-signals JSON only
    — no argparse namespace strings, no exception-derived data.

    Fields are constructed inline so the sink expression never touches
    any variable that was assigned in a failure path.
    """
    payload = {
        "worker_version": "unlimited_ocr_worker.py@2026-07-20",
        "chunk_size_requested": int(chunk_size_requested),
        "output_char_count": int(output_char_count),
        "output_sha256": str(output_sha256),
        "signals": signals,
    }
    Path(args.output_text).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_text).write_text(text or "", encoding="utf-8")
    Path(args.output_json).write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
