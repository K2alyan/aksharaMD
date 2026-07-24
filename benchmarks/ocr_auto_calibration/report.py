"""Markdown report generation for the OCR Auto Policy v1 calibration harness.

The report is intentionally conservative: with dry-run or empty data, the
Recommendations section explicitly states that the report reflects code
correctness only and that the RTX 3060 empirical pass is required before any
policy change.
"""
from __future__ import annotations

import io
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .schema import DocumentSummary, RunReport, RunResult


def _fmt_int(x: int | None) -> str:
    return "-" if x is None else str(x)


def _fmt_float(x: float | None, digits: int = 2) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def _fmt_str(x: str | None) -> str:
    return "-" if x is None else x


def _has_real_data(report: RunReport) -> bool:
    """Heuristic: any non-zero readiness across the three treatments."""
    for doc in report.documents:
        for run in (doc.tesseract, doc.unlimited_ocr, doc.auto):
            if run.readiness_score not in (None, 0):
                return True
    return False


def _executive_summary_rows(report: RunReport) -> dict[str, str]:
    total = len(report.documents)
    if total == 0:
        return {
            "documents": "0",
            "auto_correct_rate": "-",
            "auto_vs_uoc_runtime_mult": "-",
            "fallback_count": "-",
            "hallucination_failures": "-",
        }
    matches = sum(
        1 for d in report.documents if d.auto_matched_final_preference is True
    )
    determinable = sum(
        1 for d in report.documents if d.auto_matched_final_preference is not None
    )
    rate = (matches / determinable) if determinable else 0.0
    fallback = sum(
        1
        for d in report.documents
        if d.auto.fallback_reason and d.auto.fallback_reason != "-"
    )
    hallucinations = sum(
        1
        for d in report.documents
        for r in (d.tesseract, d.unlimited_ocr, d.auto)
        if r.repetition_flag
    )
    # Runtime multipliers
    mults: list[float] = []
    for d in report.documents:
        base = d.tesseract.runtime_seconds
        auto_rt = d.auto.runtime_seconds
        if base > 0:
            mults.append(auto_rt / base)
    avg_mult = (sum(mults) / len(mults)) if mults else 0.0
    return {
        "documents": str(total),
        "auto_correct_rate": f"{rate:.2%} ({matches}/{determinable})",
        "auto_vs_tess_runtime_mult": f"{avg_mult:.2f}x mean",
        "fallback_count": str(fallback),
        "hallucination_failures": str(hallucinations),
    }


def _confusion_table(report: RunReport) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for d in report.documents:
        auto_sel = d.auto.auto_selected_backend or "unknown"
        final = d.final_preference
        counter[f"auto={auto_sel} | final={final}"] += 1
    return sorted(counter.items())


def _per_doc_row(doc: DocumentSummary) -> list[str]:
    def _run_summary(run: RunResult) -> str:
        return (
            f"r={_fmt_int(run.readiness_score)}"
            f" t={_fmt_float(run.runtime_seconds)}s"
            f" rep={'Y' if run.repetition_flag else 'N'}"
        )

    return [
        doc.document_id,
        doc.profile_class,
        _run_summary(doc.tesseract),
        _run_summary(doc.unlimited_ocr),
        _run_summary(doc.auto),
        doc.automatic_preference,
        _fmt_str(doc.human_preference),
        doc.final_preference,
        "Y" if doc.auto_matched_final_preference else (
            "-" if doc.auto_matched_final_preference is None else "N"
        ),
    ]


def render_markdown(report: RunReport) -> str:
    """Produce the plain Markdown report body."""
    buf = io.StringIO()

    def _write(line: str = "") -> None:
        buf.write(line)
        buf.write("\n")

    generated_at = datetime.now(UTC).isoformat()
    real_data = _has_real_data(report)

    _write("# OCR Auto Policy v1 — Calibration Report")
    _write()
    _write(f"Generated at: {generated_at}")
    _write(f"Harness schema version: {report.harness_schema_version}")
    _write(f"Run started: {report.run_started_at}")
    _write(f"Run completed: {report.run_completed_at}")
    _write()

    _write("## Environment")
    _write()
    _write(f"- aksharamd commit: `{report.aksharamd_commit}`")
    _write(f"- Unlimited-OCR model revision: `{report.model_revision}`")
    _write(f"- GPU: {_fmt_str(report.machine.get('gpu_name'))}")
    _write(f"- VRAM total (MiB): {_fmt_int(report.machine.get('vram_total_mib'))}")
    _write(f"- OS: {_fmt_str(report.machine.get('os'))}")
    _write(f"- Python: {_fmt_str(report.machine.get('python_version'))}")
    _write(f"- Corpus size: {report.corpus_size}")
    _write()

    _write("## Executive Summary")
    _write()
    for k, v in _executive_summary_rows(report).items():
        _write(f"- {k}: {v}")
    _write()

    _write("## Auto Choice vs Final Preference (confusion table)")
    _write()
    if not report.documents:
        _write("_No documents in this run._")
    else:
        _write("| Combination | Count |")
        _write("| --- | ---: |")
        for row, count in _confusion_table(report):
            _write(f"| {row} | {count} |")
    _write()

    _write("## Per-document detail")
    _write()
    if not report.documents:
        _write("_No documents in this run._")
    else:
        _write(
            "| doc_id | profile | tesseract | uoc | auto | auto_pref | human_pref | final | matched |"
        )
        _write("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for doc in report.documents:
            cells = _per_doc_row(doc)
            _write("| " + " | ".join(cells) + " |")
    _write()

    _write("## Recommendations")
    _write()
    _write(
        "Readiness alone is not the sole quality signal. The layered "
        "preference labelling in this harness combines readiness delta, "
        "the repetition detector, exit status, and runtime multiplier — a "
        "readiness win is discarded when any hard-fail signal fires."
    )
    _write()
    if real_data:
        _write(
            "Real telemetry is present but this Markdown does not, on its "
            "own, justify a policy change. Review the JSON report and the "
            "review queue for material findings before proposing threshold "
            "adjustments."
        )
    else:
        _write(
            "**Recommendation: undetermined.** This report captures code "
            "correctness; the RTX 3060 empirical pass populates the "
            "recommendation section. Do NOT change Auto Policy v1 "
            "thresholds based on this report alone."
        )
    _write()
    _write(
        "The harness is deliberately conservative: any change to the "
        "3-page floor or 30% fraction requires empirical evidence from a "
        "full-corpus run on real hardware, and separate detection-vs-"
        "scoring PRs per project convention."
    )
    return buf.getvalue()


def write_markdown(report: RunReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(report), encoding="utf-8")
