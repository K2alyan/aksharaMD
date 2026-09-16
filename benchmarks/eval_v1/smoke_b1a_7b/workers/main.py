"""Worker CLI dispatched to by ``real_adapters.SubprocessParserAdapter``.

Contract:

- Reads PDF bytes from stdin.
- Reads ``--parser-id`` and ``--canonical-id`` from argv.
- Writes exactly one JSON object to stdout on success:
    {"status": "EXECUTED", "markdown": "...", "meta": {...}}
    or
    {"status": "DEFECT", "defect_reason": "<coded>"}
- On a Python exception, emits the exception class name to stderr on
  a line prefixed ``AKSHARAMD_SMOKE_DEFECT_REASON: <parser>_exception:<Class>``
  and exits with a non-zero status so the parent adapter records DEFECT.
- Never imports another parser than the one requested (lazy import),
  so instantiating one worker does not incur the other three parsers'
  dependencies.

This file is NOT exercised by the synthetic test suite; imports of
``marker`` / ``docling`` / ``markitdown`` are blocked by the smoke
test conftest, and this worker is designed to be invoked as a
subprocess. Coverage happens at real-smoke authorization
(B1a-7b.2b real-execution, not this PR).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

_KNOWN_PARSER_IDS = {"aksharamd-reference", "marker", "docling", "markitdown"}


def _emit_success(markdown: str, meta: dict[str, Any] | None = None) -> int:
    payload: dict[str, Any] = {"status": "EXECUTED", "markdown": markdown}
    if meta:
        payload["meta"] = meta
    sys.stdout.write(json.dumps(payload))
    return 0


def _emit_defect(coded_reason: str) -> int:
    payload = {"status": "DEFECT", "defect_reason": coded_reason}
    sys.stdout.write(json.dumps(payload))
    sys.stderr.write(f"AKSHARAMD_SMOKE_DEFECT_REASON: {coded_reason}\n")
    return 3


def _coded_exception_reason(parser_id: str, class_name: str) -> str:
    if parser_id == "aksharamd-reference":
        return f"reference_parser_exception:{class_name}"
    return f"{parser_id}_exception:{class_name}"


def _run_aksharamd_reference(pdf_bytes: bytes) -> int:  # pragma: no cover
    # Real coverage happens at B1a-7b.2b real-smoke time. Kept as an
    # explicit branch so the CLI dispatch shape is fixed.
    from benchmarks.eval_v1.smoke_run_v2 import _compile_pdf_bytes  # type: ignore
    md = _compile_pdf_bytes(pdf_bytes)
    return _emit_success(md)


def _run_marker(pdf_bytes: bytes) -> int:  # pragma: no cover
    from benchmarks.eval_v1.adjudication import ReviewerArtifact  # noqa: F401
    # The specific call shape depends on MarkerAdapter's public API,
    # which is not exercised in synthetic tests. B1a-7b.2b real-smoke
    # authorization is when this branch first runs.
    raise NotImplementedError(
        "marker worker body pending real-smoke authorization (B1a-7b.2b real)"
    )


def _run_docling(pdf_bytes: bytes) -> int:  # pragma: no cover
    raise NotImplementedError(
        "docling worker body pending real-smoke authorization (B1a-7b.2b real)"
    )


def _run_markitdown(pdf_bytes: bytes) -> int:  # pragma: no cover
    raise NotImplementedError(
        "markitdown worker body pending real-smoke authorization (B1a-7b.2b real)"
    )


_DISPATCH = {
    "aksharamd-reference": _run_aksharamd_reference,
    "marker": _run_marker,
    "docling": _run_docling,
    "markitdown": _run_markitdown,
}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(prog="smoke-parser-worker")
    parser.add_argument("--parser-id", required=True, choices=sorted(_KNOWN_PARSER_IDS))
    parser.add_argument("--canonical-id", required=True)
    args = parser.parse_args(argv)

    pdf_bytes = sys.stdin.buffer.read()

    fn = _DISPATCH[args.parser_id]
    try:
        return fn(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - deliberate broad catch at the boundary
        coded = _coded_exception_reason(args.parser_id, type(exc).__name__)
        return _emit_defect(coded)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
