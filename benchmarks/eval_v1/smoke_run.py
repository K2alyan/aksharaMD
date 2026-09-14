"""Authorization A smoke-test executor.

Runs the 3 development documents through each of the 4 V1 parsers,
capturing runtime / peak-RSS / provenance / AksharaMD score /
detector fires per (document, parser) pair. Does NOT invoke any LLM
judge or reviewer workflow — those are downstream of the V1
infrastructure question. Does NOT tune anything based on the results.

Usage:

    python -m benchmarks.eval_v1.smoke_run \\
        --output benchmarks/results/authorization-a-smoke-YYYY-MM-DD

Writes:
    <output>/manifest.json      3-doc manifest with hashes
    <output>/environment.json   parser versions + host info
    <output>/outcomes.jsonl     one JSON line per (doc, parser)
    <output>/stage_matrix.json  protocol-stage completion matrix
    <output>/<parser>/<doc_id>.md   captured markdown per pair
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as _im
import json
import platform
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import resource_shim
from .smoke_manifest import SMOKE_DOCS, V1_PARSERS, SmokeDoc


@dataclass
class PairOutcome:
    doc_id: str
    parser: str
    started_at_iso: str
    elapsed_s: float | None
    peak_rss_delta_mb: float | None
    success: bool
    error_class: str | None
    error_msg: str | None
    output_path: str | None
    output_sha256: str | None
    output_bytes: int | None
    readiness_score: int | None
    quality_band: str | None
    scoring_policy_version: str | None
    warning_codes: list[str] = field(default_factory=list)
    deduction_rule_ids: list[str] = field(default_factory=list)
    detector_diagnostic_count: int = 0


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def collect_environment() -> dict[str, Any]:
    def _v(pkg: str) -> str | None:
        try:
            return _im.version(pkg)
        except Exception:
            return None

    env: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version,
        "packages": {
            "aksharamd": _v("aksharamd"),
            "marker-pdf": _v("marker-pdf"),
            "docling": _v("docling"),
            "markitdown": _v("markitdown"),
            "pymupdf": _v("pymupdf"),
            "pymupdf4llm": _v("pymupdf4llm"),
            "torch": _v("torch"),
        },
    }
    try:
        import torch  # type: ignore
        env["torch_cuda_available"] = bool(torch.cuda.is_available())
        env["torch_cuda_device_count"] = torch.cuda.device_count() if torch.cuda.is_available() else 0
    except Exception:
        env["torch_cuda_available"] = None
    return env


def run_one(doc: SmokeDoc, parser: str, output_dir: Path) -> PairOutcome:
    """Run a single (doc, parser) pair. Never raises; captures failures."""
    from datetime import UTC, datetime

    # Import extractors lazily to isolate import failures per parser.
    from benchmarks.parsed_vs_raw.arms.parser_arm import (
        _EXTRACTORS,
        ExtractionOutput,
        ParserUnavailable,
    )

    started = datetime.now(UTC).isoformat()
    t0 = time.monotonic()
    rss0 = resource_shim.current_rss_mb()

    outcome_kwargs: dict[str, Any] = dict(
        doc_id=doc.doc_id,
        parser=parser,
        started_at_iso=started,
        elapsed_s=None,
        peak_rss_delta_mb=None,
        success=False,
        error_class=None,
        error_msg=None,
        output_path=None,
        output_sha256=None,
        output_bytes=None,
        readiness_score=None,
        quality_band=None,
        scoring_policy_version=None,
        warning_codes=[],
        deduction_rule_ids=[],
        detector_diagnostic_count=0,
    )

    if parser not in _EXTRACTORS:
        outcome_kwargs["elapsed_s"] = time.monotonic() - t0
        outcome_kwargs["error_class"] = "UnknownParser"
        outcome_kwargs["error_msg"] = f"parser {parser!r} not in _EXTRACTORS"
        return PairOutcome(**outcome_kwargs)

    try:
        pdf_bytes = doc.path.read_bytes()
    except Exception as exc:
        outcome_kwargs["elapsed_s"] = time.monotonic() - t0
        outcome_kwargs["error_class"] = exc.__class__.__name__
        outcome_kwargs["error_msg"] = f"failed to read source PDF: {exc}"
        return PairOutcome(**outcome_kwargs)

    result: ExtractionOutput | None = None
    ctx = None
    try:
        # aksharamd-reference / markitdown / marker run via Compiler and
        # give us the ctx. We reach into the extractor by inlining the
        # per-parser calls so we can capture ctx too.
        from benchmarks.parsed_vs_raw.arms.parser_arm import _compile_pdf_bytes, _readiness_from_ctx
        if parser == "aksharamd-reference":
            adapter = None
        elif parser == "markitdown":
            from benchmarks.parsed_vs_raw.adapters.markitdown_adapter import MarkItDownAdapter
            adapter = MarkItDownAdapter()
        elif parser == "marker":
            from benchmarks.parsed_vs_raw.adapters.marker_adapter import MarkerAdapter
            adapter = MarkerAdapter()
        elif parser == "docling":
            # Post-A.1a: Docling routes through the Compiler like every
            # other parser, so ctx.manifest.readiness_score is captured
            # by the same instrument as the other three arms.
            from benchmarks.parsed_vs_raw.adapters.docling_adapter import DoclingAdapter
            adapter = DoclingAdapter()
        else:
            raise KeyError(parser)
        markdown, ctx = _compile_pdf_bytes(pdf_bytes, parser_adapter=adapter)
        result = ExtractionOutput(markdown=markdown, readiness_score=_readiness_from_ctx(ctx), parser_name=parser)
    except ParserUnavailable as exc:
        outcome_kwargs["elapsed_s"] = time.monotonic() - t0
        outcome_kwargs["error_class"] = "ParserUnavailable"
        outcome_kwargs["error_msg"] = str(exc)
        return PairOutcome(**outcome_kwargs)
    except Exception as exc:
        outcome_kwargs["elapsed_s"] = time.monotonic() - t0
        outcome_kwargs["error_class"] = exc.__class__.__name__
        outcome_kwargs["error_msg"] = f"{exc}\n{traceback.format_exc(limit=6)}"
        return PairOutcome(**outcome_kwargs)

    elapsed = time.monotonic() - t0
    rss1 = resource_shim.current_rss_mb()
    outcome_kwargs["elapsed_s"] = elapsed
    outcome_kwargs["peak_rss_delta_mb"] = (rss1 - rss0) if (rss0 is not None and rss1 is not None) else None

    md = result.markdown if result is not None else ""
    out_dir = output_dir / parser
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc.doc_id}.md"
    out_path.write_text(md, encoding="utf-8")
    outcome_kwargs["output_path"] = str(out_path.relative_to(output_dir))
    outcome_kwargs["output_sha256"] = sha256_bytes(md.encode("utf-8"))
    outcome_kwargs["output_bytes"] = len(md.encode("utf-8"))
    outcome_kwargs["success"] = True
    outcome_kwargs["readiness_score"] = result.readiness_score if result else None

    if ctx is not None:
        manifest = getattr(ctx, "manifest", None)
        if manifest is not None:
            outcome_kwargs["quality_band"] = getattr(manifest, "quality_band", None) or None
            outcome_kwargs["scoring_policy_version"] = getattr(manifest, "scoring_policy_version", None) or None
            outcome_kwargs["warning_codes"] = list(getattr(manifest, "warning_codes", []) or [])
            deductions = getattr(manifest, "deductions", None) or []
            outcome_kwargs["deduction_rule_ids"] = [
                d.get("rule_id") for d in deductions if isinstance(d, dict) and d.get("rule_id")
            ]
            outcome_kwargs["detector_diagnostic_count"] = len(deductions)

    return PairOutcome(**outcome_kwargs)


def stage_matrix(outcomes: list[PairOutcome]) -> dict[str, Any]:
    """Protocol-stage completion matrix per authorization message.

    Records ATTEMPTED / SKIPPED / DEFECT per stage with explanation.
    """
    total_pairs = len(outcomes)
    parser_success = sum(1 for o in outcomes if o.success)
    scored_pairs = sum(1 for o in outcomes if o.readiness_score is not None)

    return {
        "source_ingestion": {
            "status": "ATTEMPTED",
            "notes": (
                "Source PDFs loaded from local .cache/ paths. No corpus-manifest "
                "or corpus-adapter for V1 G1-GT corpora (PMC-OA, DocLayNet, "
                "Federal Register/SEC, CUAD) exists. The 3-doc smoke chose "
                "already-staged PDFs, one of which (D2 DocBench) is not on "
                "the V1 corpus list."
            ),
        },
        "parser_execution": {
            "status": "ATTEMPTED",
            "success_count": parser_success,
            "total": total_pairs,
        },
        "output_capture": {
            "status": "ATTEMPTED",
            "notes": (
                "Per-pair markdown written under <output>/<parser>/<doc_id>.md "
                "and SHA-256 recorded."
            ),
        },
        "ground_truth_ingestion": {
            "status": "SKIPPED",
            "notes": (
                "No GT-ingestion pipeline exists for any V1 corpus. "
                "PROTOCOL_V1.md §10.1 item 11 requires "
                "benchmarks/eval_v1/ground_truth/ with code for PMC-OA XML, "
                "DocLayNet bboxes, CUAD spans; none present."
            ),
        },
        "normalization": {
            "status": "SKIPPED",
            "notes": (
                "PROTOCOL_V1.md §10.1 item 12 requires a normalization "
                "implementation applied uniformly across parsers before "
                "comparison. Not implemented for V1; only per-adapter "
                "post-processing exists in benchmarks/parsed_vs_raw/adapters/."
            ),
        },
        "conventional_measurements": {
            "status": "SKIPPED",
            "notes": (
                "PMC-OA word-overlap script and TEDS-adapted table script "
                "(PROTOCOL_V1.md §10.1 item 16) do not exist. Existing "
                "parsed_vs_raw uses an LLM judge for answer quality, which "
                "is not the same instrument."
            ),
        },
        "aksharamd_evaluation": {
            "status": "ATTEMPTED",
            "notes": (
                f"AksharaMD score captured for {scored_pairs}/{total_pairs} "
                "pairs. Docling pairs lack a score because Docling has no "
                "ParserAdapter wrapper (readiness_score None by design of "
                "the current benchmarks/parsed_vs_raw path)."
            ),
        },
        "detector_diagnostics": {
            "status": "ATTEMPTED",
            "notes": (
                "warning_codes and deduction rule_ids captured for "
                "Compiler-routed parsers. Docling: not captured (no adapter)."
            ),
        },
        "labeling_adjudication_workflow": {
            "status": "SKIPPED",
            "notes": (
                "No reviewer workflow, no (Q1,Q2,Q3)→label mapping table "
                "(PROTOCOL_V1.md §10.1 item 17, Appendix B), no reviewer "
                "instructions or presentation format. Authorization A did "
                "not attempt to build these; smoke test only exercises "
                "infrastructure that already exists or is minimal plumbing."
            ),
        },
        "provenance_artifact_recording": {
            "status": "ATTEMPTED",
            "notes": (
                "Per-pair provenance (started_at, elapsed_s, RSS delta, "
                "output SHA-256, byte count) recorded. Full study-freeze "
                "manifest (§10.1) NOT created — that is Authorization C, "
                "prohibited under A."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorization A smoke-test runner")
    parser.add_argument("--output", required=True, help="Output directory for results")
    parser.add_argument("--only-parser", default=None, help="Optional: restrict to a single parser")
    parser.add_argument("--only-doc", default=None, help="Optional: restrict to a single doc_id")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Manifest
    manifest = {
        "authorization": "A",
        "protocol": "docs/evaluation/PROTOCOL_V1.md",
        "purpose": "3-document evaluation-infrastructure smoke test",
        "documents": [
            {
                **{k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(d).items()},
            }
            for d in SMOKE_DOCS
        ],
        "parsers": list(V1_PARSERS),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Environment
    env = collect_environment()
    (output_dir / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")

    # Runs
    outcomes: list[PairOutcome] = []
    outcomes_path = output_dir / "outcomes.jsonl"
    with outcomes_path.open("w", encoding="utf-8") as fh:
        for doc in SMOKE_DOCS:
            if args.only_doc and doc.doc_id != args.only_doc:
                continue
            for p in V1_PARSERS:
                if args.only_parser and p != args.only_parser:
                    continue
                print(f"[eval_v1 smoke] running {doc.doc_id} x {p} ...", flush=True)
                oc = run_one(doc, p, output_dir)
                outcomes.append(oc)
                fh.write(json.dumps(asdict(oc), default=str) + "\n")
                fh.flush()
                status = "OK" if oc.success else f"FAIL:{oc.error_class}"
                print(
                    f"  -> {status}  elapsed={oc.elapsed_s:.2f}s  score={oc.readiness_score}",
                    flush=True,
                )

    # Stage matrix
    matrix = stage_matrix(outcomes)
    (output_dir / "stage_matrix.json").write_text(json.dumps(matrix, indent=2), encoding="utf-8")

    # Terse summary to stdout
    print("\n=== SMOKE SUMMARY ===")
    for oc in outcomes:
        print(
            f"{oc.doc_id:32s} {oc.parser:20s} "
            f"success={oc.success} score={oc.readiness_score} "
            f"warn={len(oc.warning_codes)} dedu={oc.detector_diagnostic_count} "
            f"elapsed={oc.elapsed_s}s"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
