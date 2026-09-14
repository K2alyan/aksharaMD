"""Authorization A.1 smoke-rerun executor (v2).

The A-era ``smoke_run.py`` emitted a single ``PairOutcome`` per
``(doc, parser)`` pair with a stage matrix aggregated only at the batch
level. Under A.1 every stage per pair carries a ``StageStatus`` and a
reason, so the report can distinguish EXECUTED from NOT_APPLICABLE from
DEFECT from REQUIRES_REVIEW from INFRASTRUCTURE_READY_NOT_EXECUTED.

Stages executed per (doc, parser):

    1. source_ingestion             — V1CorpusAdapter.ingest_source()
    2. parser_execution             — Compiler(parser_adapter=...).compile
    3. output_capture               — persist markdown + hash
    4. normalization                — UnicodeWhitespaceNormalizer
    5. ground_truth_ingestion       — V1CorpusAdapter.ingest_ground_truth()
    6. conventional_measurement     — word_overlap + number_overlap
    7. llm_answer_judge             — infrastructure only under A.1
    8. downstream_rag               — infrastructure only under A.1
    9. aksharamd_evaluation         — Compiler ctx.manifest fields
   10. detector_diagnostics         — warning_codes + deductions
   11. adjudication                 — A.1c only (INFRASTRUCTURE_READY under A.1b)
   12. analysis_record              — A.1c only
   13. provenance_recording         — always executed at end

Under A.1b the ``adjudication`` and ``analysis_record`` stages return
``INFRASTRUCTURE_READY_NOT_EXECUTED``. A.1c replaces those stubs.
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
from .adapters import DocBenchNonV1Adapter, QasperV1Adapter, TatDqaV1Adapter
from .adjudication import SeverityMapper, prepare_reviewer_artifact
from .analysis_record import ANALYSIS_RECORD_SCHEMA_VERSION, build_analysis_record
from .conventional_metrics import (
    LLM_METRIC_NOT_EXECUTED_REASON,
    number_overlap,
    word_overlap,
)
from .corpus_adapter import V1CorpusAdapter
from .corpus_split import assign_partition
from .normalization import NORMALIZATION_VERSION, get_default_normalizer
from .smoke_manifest import SMOKE_DOCS, V1_PARSERS, SmokeDoc
from .stages import (
    StageResult,
    defect,
    executed,
    infrastructure_ready_not_executed,
    not_applicable,
)

# Doc-id → corpus-native identifier the V1 adapter expects.
_DOC_ID_TO_CORPUS_KEY: dict[str, tuple[str, str]] = {
    "D1_qasper_1503_00841": ("qasper", "1503.00841"),
    "D2_docbench_P19_1598": ("docbench", "P19-1598"),
    "D3_tatdqa_003755794b": ("tat_dqa", "003755794bbbbcffd0667b9600aeb0be"),
}


def build_adapters() -> dict[str, V1CorpusAdapter]:
    """Return {corpus_name → adapter} for the corpora the smoke set uses."""
    return {
        "qasper": QasperV1Adapter({
            "1503.00841": Path(".cache/qasper/1503.00841-29290de61c95.pdf"),
        }),
        "docbench": DocBenchNonV1Adapter({
            "P19-1598": Path(".cache/docbench/0/P19-1598.pdf"),
        }),
        "tat_dqa": TatDqaV1Adapter({
            "003755794bbbbcffd0667b9600aeb0be": Path(
                ".cache/tat_dqa/tat_docs/dev/003755794bbbbcffd0667b9600aeb0be.pdf"
            ),
        }),
    }


@dataclass
class PairRun:
    doc_id: str
    parser: str
    started_at_iso: str
    total_elapsed_s: float
    peak_rss_delta_mb: float | None
    stages: list[dict[str, Any]] = field(default_factory=list)


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _run_parser(pdf_bytes: bytes, parser: str) -> tuple[str, Any]:
    from benchmarks.parsed_vs_raw.arms.parser_arm import (
        _compile_pdf_bytes,
        _readiness_from_ctx,  # noqa: F401  (used indirectly below)
    )

    if parser == "aksharamd-reference":
        adapter = None
    elif parser == "markitdown":
        from benchmarks.parsed_vs_raw.adapters.markitdown_adapter import (
            MarkItDownAdapter,
        )
        adapter = MarkItDownAdapter()
    elif parser == "marker":
        from benchmarks.parsed_vs_raw.adapters.marker_adapter import MarkerAdapter
        adapter = MarkerAdapter()
    elif parser == "docling":
        from benchmarks.parsed_vs_raw.adapters.docling_adapter import DoclingAdapter
        adapter = DoclingAdapter()
    else:
        raise KeyError(parser)
    markdown, ctx = _compile_pdf_bytes(pdf_bytes, parser_adapter=adapter)
    return markdown, ctx


def _pair_stages(
    doc: SmokeDoc, parser: str, adapters: dict[str, V1CorpusAdapter], output_dir: Path
) -> tuple[list[StageResult], dict[str, Any]]:
    """Execute all A.1 stages for one (doc, parser) pair.

    Returns the list of StageResults plus a per-pair summary dict.
    """
    from datetime import UTC, datetime

    stages: list[StageResult] = []
    summary: dict[str, Any] = {
        "doc_id": doc.doc_id,
        "parser": parser,
        "started_at_iso": datetime.now(UTC).isoformat(),
    }
    t0 = time.monotonic()
    rss0 = resource_shim.current_rss_mb()

    corpus_name, corpus_key = _DOC_ID_TO_CORPUS_KEY[doc.doc_id]
    adapter = adapters[corpus_name]
    caps = adapter.capabilities()
    summary["corpus_name"] = corpus_name
    summary["corpus_capabilities"] = asdict(caps)

    # Stage 1: source_ingestion
    try:
        source = adapter.ingest_source(corpus_key)
        stages.append(executed("source_ingestion", payload={
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "media_type": source.media_type,
            "provenance": source.provenance,
        }))
        pdf_bytes = source.path.read_bytes()
    except Exception as exc:
        stages.append(defect("source_ingestion", reason=str(exc)))
        summary["stages"] = [s.to_dict() for s in stages]
        summary["total_elapsed_s"] = time.monotonic() - t0
        return stages, summary

    # Stage 2: parser_execution
    stage_t0 = time.monotonic()
    markdown = ""
    ctx = None
    try:
        markdown, ctx = _run_parser(pdf_bytes, parser)
        stages.append(executed("parser_execution", payload={
            "elapsed_s": time.monotonic() - stage_t0,
            "output_bytes": len(markdown.encode("utf-8")),
        }))
    except Exception as exc:
        stages.append(defect("parser_execution", reason=f"{exc.__class__.__name__}: {exc}"))
        summary["stages"] = [s.to_dict() for s in stages]
        summary["total_elapsed_s"] = time.monotonic() - t0
        return stages, summary

    # Stage 3: output_capture
    out_dir = output_dir / parser
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc.doc_id}.md"
    out_path.write_text(markdown, encoding="utf-8")
    stages.append(executed("output_capture", payload={
        "output_path": str(out_path.relative_to(output_dir)),
        "output_sha256": _sha256_bytes(markdown.encode("utf-8")),
    }))

    # Stage 4: normalization
    normalizer = get_default_normalizer()
    norm = normalizer.normalize(markdown)
    normalized_path = out_dir / f"{doc.doc_id}.normalized.md"
    normalized_path.write_text(norm.text, encoding="utf-8")
    stages.append(executed("normalization", payload={
        "normalization_version": norm.version,
        "rules_applied": list(norm.rules_applied),
        "normalized_sha256": _sha256_bytes(norm.text.encode("utf-8")),
    }))

    # Stage 5: ground_truth_ingestion
    gt = adapter.ingest_ground_truth(corpus_key)
    if gt is None:
        # NOT_APPLICABLE derived from capabilities.
        reason = caps.not_applicable_reasons.get(
            "downstream_qa_gt",
            f"corpus {corpus_name!r} does not supply V1 ground truth for this document.",
        )
        stages.append(not_applicable("ground_truth_ingestion", reason=reason))
    else:
        stages.append(executed("ground_truth_ingestion", payload={
            "gt_kind": gt.kind,
            "pair_count": gt.provenance.get("pair_count", None),
            "provenance": gt.provenance,
        }))

    # Stage 6: conventional_measurement (non-LLM word/number overlap)
    if gt is None:
        stages.append(not_applicable(
            "conventional_measurement",
            reason="no V1 ground truth available for this corpus/document.",
        ))
    else:
        qa_pairs = (gt.data or {}).get("qa_pairs", [])
        overlaps: list[dict[str, Any]] = []
        for qa in qa_pairs:
            gold = qa.get("gold_answer", "")
            wo = word_overlap(gold, norm.text)
            no = number_overlap(gold, norm.text)
            overlaps.append({
                "question": qa.get("question", "")[:200],
                "answer_type": qa.get("answer_type", ""),
                "gold_answer": gold[:200],
                "word_overlap_ratio": wo.ratio,
                "word_matched": wo.matched_token_count,
                "word_gold": wo.gold_token_count,
                "number_overlap_ratio": no.ratio,
                "number_matched": no.matched_token_count,
                "number_gold": no.gold_token_count,
            })
        stages.append(executed("conventional_measurement", payload={
            "metric": "word_overlap + number_overlap",
            "pair_count": len(overlaps),
            "per_qa": overlaps,
        }))

    # Stage 7: llm_answer_judge — infrastructure only under A.1
    stages.append(infrastructure_ready_not_executed(
        "llm_answer_judge", reason=LLM_METRIC_NOT_EXECUTED_REASON,
    ))

    # Stage 8: downstream_rag — infrastructure only under A.1
    stages.append(infrastructure_ready_not_executed(
        "downstream_rag",
        reason=(
            "the downstream-RAG demonstration (§6.2) is a separate "
            "product-utility experiment; live execution is not "
            "authorized under A.1."
        ),
    ))

    # Stage 9: aksharamd_evaluation
    manifest = getattr(ctx, "manifest", None) if ctx is not None else None
    if manifest is None:
        stages.append(defect(
            "aksharamd_evaluation",
            reason="Compiler ctx.manifest was None; parser adapter may have bypassed the Compiler.",
        ))
    else:
        stages.append(executed("aksharamd_evaluation", payload={
            "readiness_score": getattr(manifest, "readiness_score", None),
            "quality_band": getattr(manifest, "quality_band", None) or "",
            "scoring_policy_version": getattr(manifest, "scoring_policy_version", None) or "",
        }))

    # Stage 10: detector_diagnostics
    if manifest is None:
        stages.append(defect(
            "detector_diagnostics",
            reason="no Compiler manifest to read warning_codes from.",
        ))
    else:
        stages.append(executed("detector_diagnostics", payload={
            "warning_codes": list(getattr(manifest, "warning_codes", []) or []),
            "deduction_rule_ids": [
                d.get("rule_id")
                for d in (getattr(manifest, "deductions", []) or [])
                if isinstance(d, dict) and d.get("rule_id")
            ],
            "deduction_count": len(getattr(manifest, "deductions", []) or []),
        }))

    # Stage 11: adjudication — prepare the blinded reviewer artifact.
    mapping = SeverityMapper.load()
    artifact_dir = out_dir / "reviewer_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = prepare_reviewer_artifact(
        doc_id=doc.doc_id,
        parser=parser,
        source_pdf_path=str(source.path),
        extraction_markdown_path=str(out_dir / f"{doc.doc_id}.md"),
        normalized_markdown_path=str(out_dir / f"{doc.doc_id}.normalized.md"),
        mapping=mapping,
        extra_provenance={"corpus_name": corpus_name},
    )
    artifact_path = artifact_dir / f"{doc.doc_id}.reviewer_artifact.json"
    artifact_path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")
    stages.append(executed("adjudication", payload={
        "reviewer_artifact_path": str(artifact_path.relative_to(output_dir)),
        "mapping_reference": artifact.mapping_reference,
        "mapping_coverage": mapping.coverage(),
        "reviewer_response_recorded": False,
        "note": (
            "reviewer artifact prepared and stored; no reviewer has been "
            "engaged under Authorization A.1. Downstream mapping to a "
            "severity label requires reviewer answers plus a resolved "
            "(Q1,Q2,Q3) combination in the mapping."
        ),
    }))

    # Stage 13: provenance_recording
    stages.append(executed("provenance_recording", payload={
        "adapter_provenance": adapter.provenance(),
        "source_sha256": source.sha256,
        "output_sha256": _sha256_bytes(markdown.encode("utf-8")),
        "normalization_version": norm.version,
        "assigned_partition": assign_partition(doc.doc_id).value,
        "authorization": "A.1c",
    }))

    total = time.monotonic() - t0
    rss1 = resource_shim.current_rss_mb()
    summary["total_elapsed_s"] = total
    summary["peak_rss_delta_mb"] = (rss1 - rss0) if (rss0 is not None and rss1 is not None) else None
    summary["stages"] = [s.to_dict() for s in stages]

    # Stage 12: analysis_record — populated AFTER other stages so it can
    # reference their outputs. Emitted here (rather than inside the
    # stage list above) so build_analysis_record has the full pair summary.
    record = build_analysis_record(summary)
    analysis_dir = out_dir / "analysis_records"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = analysis_dir / f"{doc.doc_id}.analysis.json"
    analysis_path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    # Update the analysis_record stage in-place to EXECUTED.
    analysis_stage_result = executed("analysis_record", payload={
        "analysis_record_path": str(analysis_path.relative_to(output_dir)),
        "schema_version": ANALYSIS_RECORD_SCHEMA_VERSION,
    })
    # Replace the earlier stub (if present) with the EXECUTED entry.
    for i, s in enumerate(stages):
        if s.stage == "analysis_record":
            stages[i] = analysis_stage_result
            break
    else:
        stages.append(analysis_stage_result)
    summary["stages"] = [s.to_dict() for s in stages]
    return stages, summary


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
        "eval_v1_versions": {
            "normalization_version": NORMALIZATION_VERSION,
        },
    }
    try:
        import torch  # type: ignore
        env["torch_cuda_available"] = bool(torch.cuda.is_available())
    except Exception:
        env["torch_cuda_available"] = None
    return env


def summarize_matrix(pair_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-pair stage results into a compact status matrix."""
    matrix: dict[str, dict[str, dict[str, str]]] = {}
    # Also tally status counts per stage.
    tally: dict[str, dict[str, int]] = {}
    for ps in pair_summaries:
        doc_id = ps["doc_id"]
        parser = ps["parser"]
        for stage_dict in ps["stages"]:
            stage_name = stage_dict["stage"]
            status = stage_dict["status"]
            matrix.setdefault(stage_name, {}).setdefault(doc_id, {})[parser] = status
            tally.setdefault(stage_name, {}).setdefault(status, 0)
            tally[stage_name][status] += 1
    return {"per_stage_matrix": matrix, "tally_by_stage": tally}


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorization A.1 smoke rerun")
    parser.add_argument("--output", required=True)
    parser.add_argument("--phase", default="a1b", help="Label written into the outputs directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "manifest.json").write_text(
        json.dumps({
            "authorization": "A.1",
            "phase": args.phase,
            "documents": [
                {
                    **{k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(d).items()},
                }
                for d in SMOKE_DOCS
            ],
            "parsers": list(V1_PARSERS),
        }, indent=2),
        encoding="utf-8",
    )
    (output_dir / "environment.json").write_text(
        json.dumps(collect_environment(), indent=2), encoding="utf-8"
    )

    adapters = build_adapters()

    pair_summaries: list[dict[str, Any]] = []
    with (output_dir / "pair_summaries.jsonl").open("w", encoding="utf-8") as fh:
        for doc in SMOKE_DOCS:
            for p in V1_PARSERS:
                print(f"[eval_v1 {args.phase}] running {doc.doc_id} x {p} ...", flush=True)
                try:
                    _, summary = _pair_stages(doc, p, adapters, output_dir)
                except Exception as exc:
                    summary = {
                        "doc_id": doc.doc_id,
                        "parser": p,
                        "fatal_error": f"{exc.__class__.__name__}: {exc}",
                        "traceback": traceback.format_exc(limit=8),
                        "stages": [],
                    }
                pair_summaries.append(summary)
                fh.write(json.dumps(summary, default=str) + "\n")
                fh.flush()
                # Concise per-pair line
                statuses = ",".join(
                    s.get("status", "?")[0] for s in summary.get("stages", [])
                )
                print(f"  stages={statuses}", flush=True)

    matrix = summarize_matrix(pair_summaries)
    (output_dir / "stage_matrix.json").write_text(
        json.dumps(matrix, indent=2), encoding="utf-8"
    )

    print("\n=== A.1 SUMMARY ===")
    for stage_name, per_status in matrix["tally_by_stage"].items():
        parts = " ".join(f"{k}={v}" for k, v in per_status.items())
        print(f"{stage_name:32s} {parts}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
