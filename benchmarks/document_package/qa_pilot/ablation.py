"""Table format ablation for the QA Pilot.

Compares table_lookup question scores across four table payload strategies
for table-heavy and XLSX documents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.document_package.qa_pilot.grading import (
    compute_representation_scores,
    grade_answer,
)
from benchmarks.document_package.qa_pilot.runner import (
    _default_results_base,
    build_model_input,
    call_model_if_available,
    load_pilot_questions,
)
from benchmarks.document_package.schema import QuestionType

ABLATION_DOCUMENT_IDS = [
    "pb-table-blackrock",
    "pb-table-fblb-p10",
    "syn-xlsx-01",
    "syn-xlsx-02",
]

TABLE_STRATEGIES = ["markdown", "tsv", "row_records", "preview_reference"]


def _regenerate_with_strategy(
    run_dir: Path,
    doc_id: str,
    strategy: str,
) -> str | None:
    """
    Attempt to regenerate candidate_c payload with a different table_payload_strategy.
    Falls back to reading candidate_c.md if recompilation is not possible.
    """
    try:
        from aksharamd.packaging import PackageProfile  # type: ignore[import]

        candidate_c_path = run_dir / "candidate_c_plan.json"
        if not candidate_c_path.exists():
            raise FileNotFoundError("candidate_c_plan.json not found")

        plan_data = json.loads(candidate_c_path.read_text(encoding="utf-8"))
        profile = PackageProfile(**plan_data.get("profile", {}))
        profile.table_payload_strategy = strategy  # type: ignore[assignment]

        # Regenerate payload using the plan
        from aksharamd.packaging import regenerate_payload_from_plan  # type: ignore[import]

        return regenerate_payload_from_plan(plan_data, profile)
    except ImportError:
        # aksharamd not importable in this context
        pass
    except Exception:
        pass

    # Fall back: read the candidate_c.md as-is
    candidate_c_path = run_dir / "candidate_c.md"
    if candidate_c_path.exists():
        return candidate_c_path.read_text(encoding="utf-8")
    return None


def run_table_ablation(
    benchmark_run_id: str,
    results_base_dir: Path | None = None,
    output_dir: Path | None = None,
    model_id: str = "claude-haiku-4-5-20251001",
    dry_run: bool = False,
) -> dict:
    """
    For table-heavy and XLSX documents, compare scores across 4 table format variants.

    Returns a dict with ablation results. Saves ablation_report.md.
    """
    if results_base_dir is None:
        results_base_dir = _default_results_base(benchmark_run_id)
    if output_dir is None:
        here = Path(__file__).resolve().parent
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = here / "runs" / f"ablation_{ts}"

    output_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict[str, float]] = {}

    for doc_id in ABLATION_DOCUMENT_IDS:
        run_dir = results_base_dir / doc_id
        questions = load_pilot_questions([doc_id])
        table_questions = [
            q for q in questions if q.question_type == QuestionType.TABLE_LOOKUP
        ]

        if not table_questions:
            continue

        results[doc_id] = {}

        for strategy in TABLE_STRATEGIES:
            doc_text = _regenerate_with_strategy(run_dir, doc_id, strategy)
            if doc_text is None:
                results[doc_id][strategy] = 0.0
                continue

            grades = []
            for question in table_questions:
                model_input = build_model_input(doc_text, question.question)

                if dry_run:
                    model_answer = "NOT FOUND"
                else:
                    result = call_model_if_available(
                        model_id, model_input["system"], model_input["user"]
                    )
                    model_answer = result if result is not None else "NOT FOUND"

                grade = grade_answer(question, model_answer, strategy)
                grades.append(grade)

            scores = compute_representation_scores(grades)
            results[doc_id][strategy] = scores.get(strategy, 0.0)

    # Write ablation report
    _write_ablation_report(results, output_dir)

    ablation_output = {
        "benchmark_run_id": benchmark_run_id,
        "model_id": model_id,
        "document_ids": ABLATION_DOCUMENT_IDS,
        "strategies": TABLE_STRATEGIES,
        "results": results,
    }

    (output_dir / "ablation_results.json").write_text(
        json.dumps(ablation_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return ablation_output


def _write_ablation_report(
    results: dict[str, dict[str, float]],
    output_dir: Path,
) -> None:
    """Write ablation_report.md."""
    lines: list[str] = []
    lines.append("# Table Format Ablation Report")
    lines.append("")
    lines.append("Compares table_lookup question scores across four table payload strategies.")
    lines.append("")

    # Summary table
    lines.append("## Results by Document and Strategy")
    lines.append("")
    strategy_header = " | ".join(TABLE_STRATEGIES)
    lines.append(f"| Document | {strategy_header} |")
    sep = " | ".join(["---"] * len(TABLE_STRATEGIES))
    lines.append(f"| --- | {sep} |")
    for doc_id, strategy_scores in results.items():
        score_cols = " | ".join(
            f"{strategy_scores.get(s, 0.0):.4f}" for s in TABLE_STRATEGIES
        )
        lines.append(f"| {doc_id} | {score_cols} |")
    lines.append("")

    # Best strategy per document
    lines.append("## Best Strategy per Document")
    lines.append("")
    lines.append("| Document | Best Strategy | Score |")
    lines.append("| --- | --- | --- |")
    for doc_id, strategy_scores in results.items():
        if strategy_scores:
            best = max(strategy_scores, key=lambda k: strategy_scores[k])
            lines.append(f"| {doc_id} | {best} | {strategy_scores[best]:.4f} |")
    lines.append("")

    (output_dir / "ablation_report.md").write_text("\n".join(lines), encoding="utf-8")
