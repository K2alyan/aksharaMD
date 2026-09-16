"""Worker CLI dispatched to by ``real_adapters.SubprocessParserAdapter``.

Contract:

- Reads PDF bytes from stdin.
- Reads ``--parser-id`` and ``--canonical-id`` from argv.
- Writes exactly one JSON object to stdout on success:
    {"status": "EXECUTED", "markdown": "...", "meta": {...}}
    or
    {"status": "DEFECT", "defect_reason": "<coded>"}
- Coded reasons come from the parser-execution contract §6 vocabulary.
- Never imports another parser than the one requested (lazy import
  inside the per-parser branch), so instantiating one worker does not
  incur the other three parsers' dependencies.

Each real-parser branch below is exercised at real-smoke time only.
The synthetic test suite uses ``sys.modules`` monkeypatching to
inject fake adapter modules so the branches' *dispatch shape* is
verified without ever importing the real parser packages.

Operational diagnostics (§7 correction B1a-7b.2e): on DEFECT, the
worker writes structured diagnostic lines to stderr prefixed with
``AKSHARAMD_SMOKE_`` tokens. These are operator-facing only; the
harness captures them in stderr.txt and writes operational_diagnostic.json
per pair. They never appear in reviewer_artifact or analysis_record.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback as _traceback
from typing import Any

_KNOWN_PARSER_IDS = {"aksharamd-reference", "marker", "docling", "markitdown"}

_MEDIA_TYPE = "application/pdf"


# --------------------------------------------------------------------------
# JSON output helpers.


def _emit_success(markdown: str, meta: dict[str, Any] | None = None) -> int:
    payload: dict[str, Any] = {"status": "EXECUTED", "markdown": markdown}
    if meta:
        payload["meta"] = meta
    sys.stdout.write(json.dumps(payload))
    return 0


def _emit_defect(coded_reason: str, exc: BaseException | None = None) -> int:
    payload = {"status": "DEFECT", "defect_reason": coded_reason}
    sys.stdout.write(json.dumps(payload))
    sys.stderr.write(f"AKSHARAMD_SMOKE_DEFECT_REASON: {coded_reason}\n")
    if exc is not None:
        sys.stderr.write(f"AKSHARAMD_SMOKE_EXCEPTION_CLASS: {type(exc).__name__}\n")
        sys.stderr.write(f"AKSHARAMD_SMOKE_EXCEPTION_MESSAGE: {str(exc)[:2000]!r}\n")
        sys.stderr.write("AKSHARAMD_SMOKE_TRACEBACK_BEGIN\n")
        sys.stderr.write(_traceback.format_exc())
        sys.stderr.write("AKSHARAMD_SMOKE_TRACEBACK_END\n")
    return 3


def _coded_exception_reason(parser_id: str, class_name: str) -> str:
    if parser_id == "aksharamd-reference":
        return f"reference_parser_exception:{class_name}"
    return f"{parser_id}_exception:{class_name}"


# --------------------------------------------------------------------------
# CUDA-error classification.
#
# torch may raise ``torch.cuda.OutOfMemoryError`` (newer versions) or a
# ``RuntimeError`` whose message contains "CUDA out of memory". Similarly
# for "no CUDA-capable device is detected" / "CUDA driver initialization
# failed". We classify by class-name + message substring so the worker
# does not depend on ``torch`` at import time.


_CUDA_OOM_SUBSTRINGS = ("cuda out of memory", "cudnn_status_alloc_failed")
_CUDA_UNAVAILABLE_SUBSTRINGS = (
    "no cuda-capable device",
    "cuda driver initialization failed",
    "cuda unavailable",
    "torch not compiled with cuda enabled",
    "no cuda gpus are available",
)


def _classify_cuda_error(parser_id: str, exc: BaseException) -> str | None:
    """Return a coded CUDA-specific defect_reason if the exception is
    a recognizable CUDA problem, otherwise None.

    Only applies to VLM parsers (marker, docling)."""
    if parser_id not in ("marker", "docling"):
        return None
    class_name = type(exc).__name__
    message = str(exc).lower()
    if class_name == "OutOfMemoryError" or any(s in message for s in _CUDA_OOM_SUBSTRINGS):
        return f"{parser_id}_cuda_oom"
    if any(s in message for s in _CUDA_UNAVAILABLE_SUBSTRINGS):
        return f"{parser_id}_cuda_unavailable"
    return None


# --------------------------------------------------------------------------
# ParserInput construction (used by three of the four workers).


def _build_parser_input(pdf_bytes: bytes, canonical_id: str):
    """Build a ``ParserInput`` for the adapter. Imported lazily so a
    worker for a parser that does not use ParserInput (aksharamd-
    reference calls ``_compile_pdf_bytes`` directly) does not import
    this module."""
    from aksharamd.parser_contract import ParserInput
    return ParserInput(
        source_id=canonical_id,
        source_hash=hashlib.sha256(pdf_bytes).hexdigest(),
        media_type=_MEDIA_TYPE,
        data=pdf_bytes,
    )


# --------------------------------------------------------------------------
# Per-parser dispatch.


def _run_aksharamd_reference(pdf_bytes: bytes, canonical_id: str) -> int:
    """Reference parser goes through the AksharaMD compilation path
    ``benchmarks.parsed_vs_raw.arms.parser_arm._compile_pdf_bytes``,
    which is the same entry point used by
    ``benchmarks.eval_v1.smoke_run_v2``."""
    from benchmarks.parsed_vs_raw.arms.parser_arm import _compile_pdf_bytes
    md, _ctx = _compile_pdf_bytes(pdf_bytes)
    return _emit_success(md)


def _run_marker(pdf_bytes: bytes, canonical_id: str) -> int:
    from benchmarks.parsed_vs_raw.adapters.marker_adapter import MarkerAdapter
    adapter = MarkerAdapter()
    src = _build_parser_input(pdf_bytes, canonical_id)
    artifact = adapter.parse(src)
    markdown = artifact.content.decode("utf-8")
    return _emit_success(markdown)


def _run_docling(pdf_bytes: bytes, canonical_id: str) -> int:
    from benchmarks.parsed_vs_raw.adapters.docling_adapter import DoclingAdapter
    adapter = DoclingAdapter()
    src = _build_parser_input(pdf_bytes, canonical_id)
    artifact = adapter.parse(src)
    markdown = artifact.content.decode("utf-8")
    return _emit_success(markdown)


def _run_markitdown(pdf_bytes: bytes, canonical_id: str) -> int:
    from benchmarks.parsed_vs_raw.adapters.markitdown_adapter import MarkItDownAdapter
    adapter = MarkItDownAdapter()
    src = _build_parser_input(pdf_bytes, canonical_id)
    artifact = adapter.parse(src)
    markdown = artifact.content.decode("utf-8")
    return _emit_success(markdown)


_DISPATCH = {
    "aksharamd-reference": _run_aksharamd_reference,
    "marker": _run_marker,
    "docling": _run_docling,
    "markitdown": _run_markitdown,
}


# --------------------------------------------------------------------------
# CLI + top-level exception handling.


def _handle_call(parser_id: str, pdf_bytes: bytes, canonical_id: str) -> int:
    """Run the requested parser with uniform exception handling."""
    fn = _DISPATCH[parser_id]
    try:
        return fn(pdf_bytes, canonical_id)
    except BaseException as exc:  # noqa: BLE001 — deliberate broad catch at the boundary
        # CUDA-specific classification takes precedence for VLM parsers.
        coded = _classify_cuda_error(parser_id, exc)
        if coded is None:
            coded = _coded_exception_reason(parser_id, type(exc).__name__)
        return _emit_defect(coded, exc=exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke-parser-worker")
    parser.add_argument("--parser-id", required=True, choices=sorted(_KNOWN_PARSER_IDS))
    parser.add_argument("--canonical-id", required=True)
    args = parser.parse_args(argv)

    pdf_bytes = sys.stdin.buffer.read()

    return _handle_call(args.parser_id, pdf_bytes, args.canonical_id)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
