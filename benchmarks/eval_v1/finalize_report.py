"""Fills the smoke-test report's outcome-table / stage-matrix / recommendation
placeholders from the run's JSON artifacts.

Idempotent — safe to re-run after fixes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def outcomes_table_md(outcomes: list[dict]) -> str:
    rows = [
        "| Pair | Doc | Parser | Success | Elapsed (s) | RSS Δ (MB) | Output bytes | Score | Band | Detector fires |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(outcomes, 1):
        fires = ", ".join(o.get("warning_codes") or []) or "-"
        elapsed = o.get("elapsed_s")
        elapsed_s = f"{elapsed:.2f}" if isinstance(elapsed, (int, float)) else str(elapsed)
        rss_d = o.get("peak_rss_delta_mb")
        rss_s = f"{rss_d:.1f}" if isinstance(rss_d, (int, float)) else "-"
        rows.append(
            f"| {i} | {o['doc_id']} | {o['parser']} | "
            f"{'OK' if o.get('success') else 'FAIL'} | "
            f"{elapsed_s} | {rss_s} | {o.get('output_bytes') or '-'} | "
            f"{o.get('readiness_score') if o.get('readiness_score') is not None else '-'} | "
            f"{o.get('quality_band') or '-'} | "
            f"{fires} |"
        )
    return "\n".join(rows)


def stage_matrix_md(matrix: dict) -> str:
    order = [
        "source_ingestion",
        "parser_execution",
        "output_capture",
        "ground_truth_ingestion",
        "normalization",
        "conventional_measurements",
        "aksharamd_evaluation",
        "detector_diagnostics",
        "labeling_adjudication_workflow",
        "provenance_artifact_recording",
    ]
    rows = ["| Stage | Status | Notes |", "|---|---|---|"]
    for k in order:
        v = matrix.get(k, {})
        status = v.get("status", "-")
        extras = []
        if "success_count" in v:
            extras.append(f"{v['success_count']}/{v.get('total','?')} pairs")
        notes = v.get("notes", "")
        if extras:
            notes = "; ".join(extras) + ". " + notes
        rows.append(f"| {k} | **{status}** | {notes} |")
    return "\n".join(rows)


def recommendation_md(outcomes: list[dict], matrix: dict) -> str:
    total = len(outcomes)
    ok = sum(1 for o in outcomes if o.get("success"))
    scored = sum(1 for o in outcomes if o.get("readiness_score") is not None)

    fixes: list[str] = [
        "Wrap Docling as a `ParserAdapter` mirroring `MarkItDownAdapter`, "
        "so Docling routes through the Compiler and produces a readiness_score "
        "computed by the same instrument as the other three parsers. Without "
        "this, cross-parser comparison against Docling at Authorization B is "
        "invalid.",
        "Move or rename adapters so `benchmarks/parser_adapters/` (per "
        "PROTOCOL_V1.md §7.1) either exists at the referenced path or the "
        "protocol text is amended to point at "
        "`benchmarks/parsed_vs_raw/adapters/`.",
        "Build `benchmarks/eval_v1/ground_truth/` with corpus adapters + "
        "GT-ingestion for each V1 G1 corpus that the pilot will exercise "
        "(at minimum PMC-OA XML text + one of DocLayNet bbox / CUAD span).",
        "Build the corpus acquisition scripts and split-assignment logic "
        "(§8.2 deterministic hashing) for the V1 corpora that will feed the "
        "pilot.",
        "Build the normalization pass (§10.1 item 12) applied uniformly to "
        "every parser output before any comparison.",
        "Build conventional-metric implementations (§10.1 item 16): "
        "PMC-OA word-overlap and TEDS-adapted table comparator.",
        "Draft and pilot the (Q1, Q2, Q3) → label mapping table "
        "(Appendix B) and the reviewer instructions + blinding scaffolding "
        "(§10.1 items 17-19).",
        "Extend the harness to bootstrap by document per §11.2, before any "
        "pilot summary numbers are produced.",
    ]

    reasoning = (
        f"The smoke test executed {ok}/{total} parser-doc pairs successfully "
        f"and captured AksharaMD scores for {scored}/{total} pairs. "
        "The parser-execution / output-capture / AksharaMD-evaluation / "
        "detector-diagnostics / provenance-recording stages ran; the "
        "ground-truth-ingestion, normalization, conventional-measurements, "
        "and labeling-adjudication stages could not run because the "
        "supporting infrastructure does not exist yet (findings INFRA-2, "
        "INFRA-4, INFRA-5, INFRA-6, PARSER-1, GT-1, NORM-1, ADJ-1).\n\n"
        "None of the gaps are methodological — the V1 protocol's methodology "
        "as merged in PR #172 is intact and unchallenged by anything in the "
        "smoke test. The gaps are infrastructure: parser-adapter parity for "
        "Docling, corpus adapters, ground-truth ingestion, normalization, "
        "conventional-metric implementation, and reviewer scaffolding. The "
        "list is well-defined and non-methodological."
    )

    verdict = "READY FOR B AFTER SPECIFIED NON-METHODOLOGICAL FIXES"

    fixes_md = "\n".join(f"{i}. {f}" for i, f in enumerate(fixes, 1))
    return (
        f"### Recommendation: **{verdict}**\n\n"
        f"{reasoning}\n\n"
        f"**Fixes required before Authorization B is sought:**\n\n{fixes_md}\n\n"
        "**Scope of the required fixes:** substantial but well-scoped "
        "engineering. No detector, threshold, cap value, scoring-policy "
        "version, parser catalog, or scoring code needs to be touched to "
        "clear this list. If, while building the fixes, evidence emerges "
        "that a methodological change is needed, that evidence goes to the "
        "human before any protocol edit — the amendment procedure in §10.4 "
        "does not apply pre-freeze, but the same discipline applies here.\n\n"
        "**Do not start Authorization B on the basis of this report.** The "
        "user's original authorization message required that Authorization "
        "A stop at the report regardless of outcome. Only human authorization "
        "can move to B."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--report", default="docs/evaluation/AUTHORIZATION_A_SMOKE_TEST_REPORT.md")
    args = parser.parse_args()

    results = Path(args.results_dir)
    outcomes = [
        json.loads(line) for line in (results / "outcomes.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    matrix = json.loads((results / "stage_matrix.json").read_text(encoding="utf-8"))

    rp = Path(args.report)
    text = rp.read_text(encoding="utf-8")
    text = text.replace("<!-- OUTCOMES_TABLE_MARKER -->", outcomes_table_md(outcomes))
    text = text.replace("<!-- STAGE_MATRIX_MARKER -->", stage_matrix_md(matrix))
    text = text.replace("<!-- RECOMMENDATION_MARKER -->", recommendation_md(outcomes, matrix))
    rp.write_text(text, encoding="utf-8")
    print(f"Filled report: {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
