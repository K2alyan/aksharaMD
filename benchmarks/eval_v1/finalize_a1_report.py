"""Fill the AUTHORIZATION_A_1_COMPLETION_REPORT.md placeholders.

Idempotent — safe to re-run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.eval_v1.adjudication import SeverityMapper

STAGE_ORDER = [
    "source_ingestion",
    "parser_execution",
    "output_capture",
    "normalization",
    "ground_truth_ingestion",
    "conventional_measurement",
    "llm_answer_judge",
    "downstream_rag",
    "aksharamd_evaluation",
    "detector_diagnostics",
    "adjudication",
    "provenance_recording",
    "analysis_record",
]

STATUS_ABBREV = {
    "EXECUTED": "EXEC",
    "NOT_APPLICABLE": "N/A",
    "DEFECT": "DEFECT",
    "REQUIRES_REVIEW": "REVIEW",
    "INFRASTRUCTURE_READY_NOT_EXECUTED": "IR-NE",
}


def matrix_table(pair_summaries: list[dict]) -> str:
    """Cross-tab of stage × (doc, parser)."""
    parsers = ["aksharamd-reference", "marker", "docling", "markitdown"]
    docs = sorted({p["doc_id"] for p in pair_summaries})
    # doc_id → parser → stage → status
    by_key: dict[str, dict[str, dict[str, str]]] = {}
    for p in pair_summaries:
        d = p["doc_id"]
        pa = p["parser"]
        stage_status: dict[str, str] = {}
        for s in p.get("stages", []):
            stage_status[s["stage"]] = s["status"]
        by_key.setdefault(d, {})[pa] = stage_status

    lines = []
    # Header
    hdr = "| Stage | " + " | ".join(f"{d}/{pa}" for d in docs for pa in parsers) + " |"
    sep = "|---|" + "---|" * (len(docs) * len(parsers))
    lines.append(hdr)
    lines.append(sep)
    for stage in STAGE_ORDER:
        row = [stage]
        for d in docs:
            for pa in parsers:
                status = by_key.get(d, {}).get(pa, {}).get(stage, "MISSING")
                row.append(STATUS_ABBREV.get(status, status))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("Legend: EXEC = EXECUTED · N/A = NOT_APPLICABLE · DEFECT = DEFECT · REVIEW = REQUIRES_REVIEW · IR-NE = INFRASTRUCTURE_READY_NOT_EXECUTED.")
    return "\n".join(lines)


def per_stage_detail(pair_summaries: list[dict]) -> str:
    """For each stage that has any non-EXECUTED cell, enumerate the reasons."""
    lines = []
    # Tally per stage
    tally: dict[str, dict[str, int]] = {}
    reason_by_stage_status: dict[str, dict[str, list[str]]] = {}
    for p in pair_summaries:
        for s in p.get("stages", []):
            stage = s["stage"]
            status = s["status"]
            reason = s.get("reason", "") or ""
            tally.setdefault(stage, {}).setdefault(status, 0)
            tally[stage][status] += 1
            if status != "EXECUTED" and reason:
                key = f"{stage}::{status}"
                reason_by_stage_status.setdefault(key, [])
                # Deduplicate identical reasons
                if reason not in reason_by_stage_status[key]:
                    reason_by_stage_status[key].append(reason)

    lines.append("| Stage | Status counts | Non-EXECUTED reasons (unique) |")
    lines.append("|---|---|---|")
    for stage in STAGE_ORDER:
        per_status = tally.get(stage, {})
        counts = ", ".join(f"{k}={v}" for k, v in per_status.items())
        reasons_bits: list[str] = []
        for status in ["DEFECT", "REQUIRES_REVIEW", "NOT_APPLICABLE", "INFRASTRUCTURE_READY_NOT_EXECUTED"]:
            for r in reason_by_stage_status.get(f"{stage}::{status}", []):
                reasons_bits.append(f"**{STATUS_ABBREV[status]}**: {r}")
        joined = "<br>".join(reasons_bits) if reasons_bits else "—"
        lines.append(f"| {stage} | {counts} | {joined} |")

    defect_count = sum(t.get("DEFECT", 0) for t in tally.values())
    if defect_count == 0:
        lines.append("")
        lines.append("**Zero `DEFECT` cells — A.1's acceptance criterion is met.**")
    else:
        lines.append("")
        lines.append(f"**{defect_count} `DEFECT` cells present — A.1 acceptance NOT met.**")
    return "\n".join(lines)


def appendix_b_coverage() -> str:
    mapping = SeverityMapper.load()
    cov = mapping.coverage()
    unresolved = mapping.unresolved_combinations()
    lines: list[str] = []
    lines.append(
        f"Appendix B loads **{cov['mapped_combinations']} of "
        f"{cov['total_combinations']}** possible (Q1, Q2, Q3) combinations "
        f"({cov['unresolved_combinations']} unresolved). `mapping_frozen: false` in "
        f"`benchmarks/eval_v1/mapping.v0.json`. Any unresolved combination that "
        f"a future reviewer submits will cause the adjudication mapping step "
        f"to return `REQUIRES_REVIEW`, not a fabricated label."
    )
    lines.append("")
    lines.append("**Mapped (from Appendix B verbatim):**")
    lines.append("")
    lines.append("| Q1 (Coverage) | Q2 (Fidelity) | Q3 (Usability) | Label |")
    lines.append("|---|---|---|---|")
    for (q1, q2, q3), label in sorted(mapping.rows.items()):
        lines.append(f"| {q1} | {q2} | {q3} | {label.value} |")
    lines.append("")
    lines.append(
        f"**Unresolved combinations ({len(unresolved)} of {cov['total_combinations']}):** "
        f"listed in full below; each will return `REQUIRES_REVIEW` if a reviewer "
        f"submits it. Filling any of these requires methodological authorization "
        f"outside A.1."
    )
    lines.append("")
    lines.append("<details><summary>Full unresolved list</summary>")
    lines.append("")
    lines.append("| Q1 | Q2 | Q3 |")
    lines.append("|---|---|---|")
    for (q1, q2, q3) in unresolved:
        lines.append(f"| {q1} | {q2} | {q3} |")
    lines.append("")
    lines.append("</details>")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument(
        "--report", default="docs/evaluation/AUTHORIZATION_A_1_COMPLETION_REPORT.md"
    )
    args = parser.parse_args()

    results = Path(args.results_dir)
    pair_summaries = [
        json.loads(line)
        for line in (results / "pair_summaries.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    rp = Path(args.report)
    text = rp.read_text(encoding="utf-8")
    text = text.replace("<!-- FINAL_STAGE_MATRIX_MARKER -->", matrix_table(pair_summaries))
    text = text.replace("<!-- PER_STAGE_DETAIL_MARKER -->", per_stage_detail(pair_summaries))
    text = text.replace("<!-- APPENDIX_B_COVERAGE_MARKER -->", appendix_b_coverage())
    rp.write_text(text, encoding="utf-8")
    print(f"Filled report: {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
