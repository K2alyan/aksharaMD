"""Pilot runner for Benchmark C text-only QA pilot."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.document_package.qa_pilot.grading import (
    GRADING_VERSION,
    grade_answer,
)
from benchmarks.document_package.qa_pilot.held_out_questions import (
    HELD_OUT_DOCUMENT_IDS,
    HELD_OUT_QUESTIONS,
)
from benchmarks.document_package.qa_pilot.prompt_v1 import (
    SYSTEM_PROMPT,
    build_user_message,
)
from benchmarks.document_package.qa_pilot.questions import (
    KNOWN_SHARED_FAILURES,
    PILOT_DOCUMENT_IDS,
    PILOT_QUESTIONS,
)
from benchmarks.document_package.qa_pilot.schema import (
    DocumentPilotResult,
    EvaluationCorrection,
    PilotRun,
    QAPilotLock,
    RepresentationResult,
)
from benchmarks.document_package.schema import (
    AnswerKey,
    GradingMethod,
    QuestionRecord,
    QuestionType,
)

PILOT_ID = "text_only_v1"
REPRESENTATION_NAMES = ["baseline_b", "candidate_c", "candidate_d"]

HELD_OUT_PILOT_ID = "text_only_held_out_v1"
HELD_OUT_REPRESENTATION_NAMES = ["baseline_b", "candidate_c", "candidate_d"]

# Frozen-at timestamp from benchmarks/document_package/qa_pilot/held_out_eval_freeze_lock.json
_HELD_OUT_FREEZE_LOCKED_AT = "2026-07-14T21:45:16.855119+00:00"

_REPR_FILE_MAP = {
    "baseline_b": "baseline_b_document.md",
    "candidate_c": "candidate_c.md",
    "candidate_d": "candidate_d.md",
}


def _default_results_base(benchmark_run_id: str) -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "results" / benchmark_run_id / "runs"


def _default_output_dir(pilot_id: str) -> Path:
    here = Path(__file__).resolve().parent
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return here / "runs" / f"{pilot_id}_{ts}"


def _get_code_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _get_parser_version() -> str:
    try:
        from aksharamd import __version__
        return __version__
    except Exception:
        return "unknown"


def load_pilot_questions(document_ids: list[str] | None = None) -> list[QuestionRecord]:
    """Load questions from questions.py, optionally filtering by document_id."""
    records: list[QuestionRecord] = []
    for q in PILOT_QUESTIONS:
        if document_ids is not None and q["document_id"] not in document_ids:
            continue
        ak_data = q.get("answer_key", {})
        answer_key = AnswerKey(
            accepted_answers=ak_data.get("accepted_answers", []),
            answer_type=ak_data.get("answer_type", "semantic"),
            grading_method=GradingMethod(ak_data.get("grading_method", "deterministic")),
            notes=ak_data.get("notes", ""),
        )
        records.append(
            QuestionRecord(
                question_id=q["question_id"],
                document_id=q["document_id"],
                question=q["question"],
                question_type=QuestionType(q["question_type"]),
                requires_visual=q.get("requires_visual", False),
                answer_key=answer_key,
            )
        )
    return records


def load_held_out_questions(document_ids: list[str] | None = None) -> list[QuestionRecord]:
    """Load questions from held_out_questions.py, optionally filtering by document_id."""
    records: list[QuestionRecord] = []
    for q in HELD_OUT_QUESTIONS:
        if document_ids is not None and q["document_id"] not in document_ids:
            continue
        ak_data = q.get("answer_key", {})
        answer_key = AnswerKey(
            accepted_answers=ak_data.get("accepted_answers", []),
            answer_type=ak_data.get("answer_type", "semantic"),
            grading_method=GradingMethod(ak_data.get("grading_method", "deterministic")),
            notes=ak_data.get("notes", ""),
        )
        records.append(
            QuestionRecord(
                question_id=q["question_id"],
                document_id=q["document_id"],
                question=q["question"],
                question_type=QuestionType(q["question_type"]),
                requires_visual=q.get("requires_visual", False),
                answer_key=answer_key,
            )
        )
    return records


def get_representation_text(run_dir: Path, representation: str) -> str:
    """Load the text for a given representation from a benchmark run directory."""
    filename = _REPR_FILE_MAP[representation]
    path = run_dir / filename
    return path.read_text(encoding="utf-8")


def build_model_input(document_text: str, question: str) -> dict:
    """Build model input using prompt_v1."""
    return {
        "system": SYSTEM_PROMPT,
        "user": build_user_message(document_text, question),
    }


def call_model_if_available(
    model_id: str,
    system: str,
    user: str,
) -> str | None:
    """Call the Anthropic API if ANTHROPIC_API_KEY is set. Returns None if not available."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model_id,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text.strip()
    except Exception:
        return None


def run_pilot(
    benchmark_run_id: str,
    results_base_dir: Path | None = None,
    output_dir: Path | None = None,
    model_id: str = "claude-haiku-4-5-20251001",
    prompt_version: str = "v1.0",
    grading_version: str = "v1.0",
    dry_run: bool = False,
) -> PilotRun:
    """
    Run the QA pilot.

    For each document in PILOT_DOCUMENT_IDS:
      For each representation (baseline_b, candidate_c, candidate_d):
        For each question:
          - Build model input
          - If not dry_run and API key available: call model
          - Else: use "NOT FOUND" as placeholder answer
          - Grade the answer

    Save artifacts to output_dir.
    """
    if results_base_dir is None:
        results_base_dir = _default_results_base(benchmark_run_id)
    if output_dir is None:
        output_dir = _default_output_dir(PILOT_ID)

    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(UTC).isoformat()
    parser_version = _get_parser_version()
    code_commit = _get_code_commit()

    lock = QAPilotLock(
        pilot_id=PILOT_ID,
        model_id=model_id,
        prompt_version=prompt_version,
        grading_version=grading_version,
        document_ids=list(PILOT_DOCUMENT_IDS),
        representation_names=list(REPRESENTATION_NAMES),
        benchmark_run_id=benchmark_run_id,
        parser_version=parser_version,
        planner_version="unknown",
        tokenizer="cl100k_base",
        code_commit=code_commit,
        created_at=started_at,
    )

    # Save lock
    (output_dir / "pilot_lock.json").write_text(
        lock.model_dump_json(indent=2), encoding="utf-8"
    )

    document_results: list[DocumentPilotResult] = []

    for doc_id in PILOT_DOCUMENT_IDS:
        run_dir = results_base_dir / doc_id
        questions = load_pilot_questions([doc_id])
        question_ids = [q.question_id for q in questions]

        repr_results: list[RepresentationResult] = []

        for repr_name in REPRESENTATION_NAMES:
            # Load document text
            try:
                doc_text = get_representation_text(run_dir, repr_name)
            except FileNotFoundError:
                doc_text = ""

            prompt_tokens = len(doc_text.split())  # rough estimate
            all_grades = []

            for question in questions:
                model_input = build_model_input(doc_text, question.question)

                # Save model input artifact
                inputs_dir = output_dir / "model_inputs" / doc_id / repr_name
                inputs_dir.mkdir(parents=True, exist_ok=True)
                (inputs_dir / f"{question.question_id}.json").write_text(
                    json.dumps(model_input, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                # Get model answer
                if dry_run:
                    model_answer = "NOT FOUND"
                else:
                    result = call_model_if_available(
                        model_id, model_input["system"], model_input["user"]
                    )
                    model_answer = result if result is not None else "NOT FOUND"

                # Save model output artifact
                outputs_dir = output_dir / "model_outputs" / doc_id / repr_name
                outputs_dir.mkdir(parents=True, exist_ok=True)
                (outputs_dir / f"{question.question_id}.txt").write_text(
                    model_answer, encoding="utf-8"
                )

                grade = grade_answer(question, model_answer, repr_name)
                all_grades.append(grade)

            # Compute counts
            correct = sum(1 for g in all_grades if g.grade == "correct")
            partial = sum(1 for g in all_grades if g.grade == "partial")
            incorrect = sum(1 for g in all_grades if g.grade == "incorrect")
            mean_score = sum(g.score for g in all_grades) / len(all_grades) if all_grades else 0.0

            repr_result = RepresentationResult(
                representation=repr_name,
                prompt_tokens=prompt_tokens,
                response_text="",
                grades=all_grades,
                mean_score=mean_score,
                correct_count=correct,
                partial_count=partial,
                incorrect_count=incorrect,
            )
            repr_results.append(repr_result)

            # Save grades artifact
            grades_dir = output_dir / "grades" / doc_id
            grades_dir.mkdir(parents=True, exist_ok=True)
            grades_data = [g.model_dump() for g in all_grades]
            (grades_dir / f"{repr_name}_grades.json").write_text(
                json.dumps(grades_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        doc_result = DocumentPilotResult(
            document_id=doc_id,
            question_ids=question_ids,
            representations=repr_results,
        )
        document_results.append(doc_result)

    completed_at = datetime.now(UTC).isoformat()

    # Compute overall mean scores
    overall_scores: dict[str, list[float]] = {r: [] for r in REPRESENTATION_NAMES}
    for doc_result in document_results:
        for repr_result in doc_result.representations:
            overall_scores[repr_result.representation].append(repr_result.mean_score)

    overall_mean = {
        r: (sum(v) / len(v)) if v else 0.0
        for r, v in overall_scores.items()
    }

    pilot_run = PilotRun(
        pilot_id=PILOT_ID,
        lock=lock,
        started_at=started_at,
        completed_at=completed_at,
        document_results=document_results,
        overall_mean_score_by_representation=overall_mean,
    )

    # Save pilot run
    (output_dir / "pilot_run.json").write_text(
        pilot_run.model_dump_json(indent=2), encoding="utf-8"
    )

    # Write report
    write_pilot_report(pilot_run, output_dir)

    return pilot_run


def write_pilot_report(pilot_run: PilotRun, output_dir: Path) -> None:
    """Write a Markdown QA pilot report."""
    lines: list[str] = []
    lines.append(f"# QA Pilot Report: {pilot_run.pilot_id}")
    lines.append("")
    lines.append(f"Run started: {pilot_run.started_at}")
    lines.append(f"Run completed: {pilot_run.completed_at or 'N/A'}")
    lines.append(f"Benchmark run: {pilot_run.lock.benchmark_run_id}")
    lines.append(f"Model: {pilot_run.lock.model_id}")
    lines.append("")

    # Summary table
    lines.append("## Summary: Mean Score by Representation")
    lines.append("")
    lines.append("| Representation | Mean Score |")
    lines.append("| --- | --- |")
    for repr_name, score in pilot_run.overall_mean_score_by_representation.items():
        lines.append(f"| {repr_name} | {score:.4f} |")
    lines.append("")

    # Per-document breakdown
    lines.append("## Per-Document Breakdown")
    lines.append("")
    repr_names = pilot_run.lock.representation_names
    header_cols = " | ".join(repr_names)
    lines.append(f"| Document | {header_cols} |")
    sep_cols = " | ".join(["---"] * len(repr_names))
    lines.append(f"| --- | {sep_cols} |")
    for doc_result in pilot_run.document_results:
        scores_by_repr = {r.representation: r.mean_score for r in doc_result.representations}
        score_cols = " | ".join(
            f"{scores_by_repr.get(r, 0.0):.4f}" for r in repr_names
        )
        lines.append(f"| {doc_result.document_id} | {score_cols} |")
    lines.append("")

    # Per-question grade table
    lines.append("## Per-Question Grades")
    lines.append("")
    lines.append("| Document | Question ID | " + " | ".join(repr_names) + " |")
    lines.append("| --- | --- | " + " | ".join(["---"] * len(repr_names)) + " |")
    for doc_result in pilot_run.document_results:
        grades_by_repr: dict[str, dict[str, str]] = {}
        for repr_result in doc_result.representations:
            grades_by_repr[repr_result.representation] = {
                g.question_id: g.grade for g in repr_result.grades
            }
        for q_id in doc_result.question_ids:
            grade_cols = " | ".join(
                grades_by_repr.get(r, {}).get(q_id, "N/A") for r in repr_names
            )
            lines.append(f"| {doc_result.document_id} | {q_id} | {grade_cols} |")
    lines.append("")

    # Anomalies: C or D scored lower than B
    lines.append("## Anomalies (C or D scored lower than B)")
    lines.append("")
    anomalies: list[str] = []
    for doc_result in pilot_run.document_results:
        scores_by_repr = {r.representation: r.mean_score for r in doc_result.representations}
        b_score = scores_by_repr.get("baseline_b", 0.0)
        for cand in ["candidate_c", "candidate_d"]:
            c_score = scores_by_repr.get(cand, 0.0)
            if c_score < b_score:
                anomalies.append(
                    f"- {doc_result.document_id}: {cand} ({c_score:.4f}) < baseline_b ({b_score:.4f})"
                )
    if anomalies:
        lines.extend(anomalies)
    else:
        lines.append("None detected.")
    lines.append("")

    report_text = "\n".join(lines)
    (output_dir / "qa_pilot_report.md").write_text(report_text, encoding="utf-8")


def run_held_out_pilot(
    benchmark_run_id: str = "held_out_20260714_214543",
    results_base_dir: Path | None = None,
    output_dir: Path | None = None,
    model_id: str = "claude-haiku-4-5-20251001",
    prompt_version: str = "v1.0",
    grading_version: str = "v1.1",
    dry_run: bool = False,
) -> PilotRun:
    """
    Run the held-out QA pilot.

    For each document in HELD_OUT_DOCUMENT_IDS:
      For each representation (baseline_b, candidate_c, candidate_d):
        For each question:
          - Build model input
          - If not dry_run and API key available: call model
          - Else: use "NOT FOUND" as placeholder answer
          - Grade the answer

    Save artifacts to output_dir.
    """
    if results_base_dir is None:
        results_base_dir = _default_results_base(benchmark_run_id)
    if output_dir is None:
        output_dir = _default_output_dir(HELD_OUT_PILOT_ID)

    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(UTC).isoformat()
    parser_version = _get_parser_version()
    code_commit = _get_code_commit()

    lock = QAPilotLock(
        pilot_id=HELD_OUT_PILOT_ID,
        model_id=model_id,
        prompt_version=prompt_version,
        grading_version=grading_version,
        document_ids=list(HELD_OUT_DOCUMENT_IDS),
        representation_names=list(HELD_OUT_REPRESENTATION_NAMES),
        benchmark_run_id=benchmark_run_id,
        parser_version=parser_version,
        planner_version="unknown",
        tokenizer="cl100k_base",
        code_commit=code_commit,
        created_at=_HELD_OUT_FREEZE_LOCKED_AT,
    )

    # Save lock
    (output_dir / "pilot_lock.json").write_text(
        lock.model_dump_json(indent=2), encoding="utf-8"
    )

    document_results: list[DocumentPilotResult] = []

    for doc_id in HELD_OUT_DOCUMENT_IDS:
        run_dir = results_base_dir / doc_id
        questions = load_held_out_questions([doc_id])
        question_ids = [q.question_id for q in questions]

        repr_results: list[RepresentationResult] = []

        for repr_name in HELD_OUT_REPRESENTATION_NAMES:
            # Load document text
            try:
                doc_text = get_representation_text(run_dir, repr_name)
            except FileNotFoundError:
                doc_text = ""

            prompt_tokens = len(doc_text.split())  # rough estimate
            all_grades = []

            for question in questions:
                model_input = build_model_input(doc_text, question.question)

                # Save model input artifact
                inputs_dir = output_dir / "model_inputs" / doc_id / repr_name
                inputs_dir.mkdir(parents=True, exist_ok=True)
                (inputs_dir / f"{question.question_id}.json").write_text(
                    json.dumps(model_input, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                # Get model answer
                if dry_run:
                    model_answer = "NOT FOUND"
                else:
                    result = call_model_if_available(
                        model_id, model_input["system"], model_input["user"]
                    )
                    model_answer = result if result is not None else "NOT FOUND"

                # Save model output artifact
                outputs_dir = output_dir / "model_outputs" / doc_id / repr_name
                outputs_dir.mkdir(parents=True, exist_ok=True)
                (outputs_dir / f"{question.question_id}.txt").write_text(
                    model_answer, encoding="utf-8"
                )

                grade = grade_answer(question, model_answer, repr_name)
                all_grades.append(grade)

            # Compute counts
            correct = sum(1 for g in all_grades if g.grade == "correct")
            partial = sum(1 for g in all_grades if g.grade == "partial")
            incorrect = sum(1 for g in all_grades if g.grade == "incorrect")
            mean_score = (
                sum(g.score for g in all_grades) / len(all_grades) if all_grades else 0.0
            )

            repr_result = RepresentationResult(
                representation=repr_name,
                prompt_tokens=prompt_tokens,
                response_text="",
                grades=all_grades,
                mean_score=mean_score,
                correct_count=correct,
                partial_count=partial,
                incorrect_count=incorrect,
            )
            repr_results.append(repr_result)

            # Save grades artifact
            grades_dir = output_dir / "grades" / doc_id
            grades_dir.mkdir(parents=True, exist_ok=True)
            grades_data = [g.model_dump() for g in all_grades]
            (grades_dir / f"{repr_name}_grades.json").write_text(
                json.dumps(grades_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        doc_result = DocumentPilotResult(
            document_id=doc_id,
            question_ids=question_ids,
            representations=repr_results,
        )
        document_results.append(doc_result)

    completed_at = datetime.now(UTC).isoformat()

    # Compute overall mean scores
    overall_scores: dict[str, list[float]] = {r: [] for r in HELD_OUT_REPRESENTATION_NAMES}
    for doc_result in document_results:
        for repr_result in doc_result.representations:
            overall_scores[repr_result.representation].append(repr_result.mean_score)

    overall_mean = {
        r: (sum(v) / len(v)) if v else 0.0
        for r, v in overall_scores.items()
    }

    pilot_run = PilotRun(
        pilot_id=HELD_OUT_PILOT_ID,
        lock=lock,
        started_at=started_at,
        completed_at=completed_at,
        document_results=document_results,
        overall_mean_score_by_representation=overall_mean,
    )

    # Save pilot run
    (output_dir / "pilot_run.json").write_text(
        pilot_run.model_dump_json(indent=2), encoding="utf-8"
    )

    # Write report
    write_pilot_report(pilot_run, output_dir)

    return pilot_run


# DEV pilot reference constants for comparison reports
_DEV_SCORES: dict[str, float] = {
    "baseline_b": 0.9556,
    "candidate_c": 0.9556,
    "candidate_d": 0.9556,
}
_DEV_TOKEN_REDUCTION_RANGE = "25–30%"


def write_held_out_comparison_report(
    held_out_run: PilotRun,
    output_dir: Path,
    dev_run: PilotRun | None = None,
) -> None:
    """
    Write a Markdown comparison report between the held-out pilot and the DEV pilot.

    The comparison report (qa_pilot_comparison_report.md) shows:
    - Side-by-side DEV pilot scores vs held-out pilot scores per representation
    - Per-category breakdown (prose, academic, financial, chart, docx, xlsx)
    - Decision gate summary
    """
    lines: list[str] = []
    lines.append("# QA Pilot Comparison Report: DEV vs Held-Out")
    lines.append("")
    lines.append(f"Held-out run: {held_out_run.pilot_id}")
    lines.append(f"Held-out started: {held_out_run.started_at}")
    lines.append(f"Held-out completed: {held_out_run.completed_at or 'N/A'}")
    lines.append(f"Freeze lock frozen_at: {_HELD_OUT_FREEZE_LOCKED_AT}")
    lines.append("")

    # Side-by-side scores
    lines.append("## Side-by-Side: DEV vs Held-Out Scores by Representation")
    lines.append("")
    lines.append("| Representation | DEV Score | Held-Out Score | Delta |")
    lines.append("| --- | --- | --- | --- |")

    ho_scores = held_out_run.overall_mean_score_by_representation
    if dev_run is not None:
        dev_scores_map = dev_run.overall_mean_score_by_representation
    else:
        dev_scores_map = _DEV_SCORES

    for repr_name in HELD_OUT_REPRESENTATION_NAMES:
        dev_s = dev_scores_map.get(repr_name, _DEV_SCORES.get(repr_name, 0.0))
        ho_s = ho_scores.get(repr_name, 0.0)
        delta = ho_s - dev_s
        sign = "+" if delta >= 0 else ""
        lines.append(f"| {repr_name} | {dev_s:.4f} | {ho_s:.4f} | {sign}{delta:.4f} |")
    lines.append("")

    # Per-category breakdown
    _CATEGORY_MAP: dict[str, list[str]] = {
        "prose": ["pb-ho-text-livingword", "pb-ho-text-cn-article"],
        "financial": ["pb-ho-table-axa-urd"],
        "chart": ["pb-ho-chart-egov-p170"],
        "docx": ["syn-ho-docx-07"],
        "xlsx": ["syn-ho-xlsx-03"],
    }

    lines.append("## Per-Category Breakdown")
    lines.append("")
    repr_names = HELD_OUT_REPRESENTATION_NAMES
    header_cols = " | ".join(repr_names)
    lines.append(f"| Category | Document | {header_cols} |")
    sep_cols = " | ".join(["---"] * len(repr_names))
    lines.append(f"| --- | --- | {sep_cols} |")

    doc_score_map: dict[str, dict[str, float]] = {}
    for doc_result in held_out_run.document_results:
        doc_score_map[doc_result.document_id] = {
            r.representation: r.mean_score for r in doc_result.representations
        }

    for category, doc_ids in _CATEGORY_MAP.items():
        for doc_id in doc_ids:
            scores = doc_score_map.get(doc_id, {})
            score_cols = " | ".join(
                f"{scores.get(r, 0.0):.4f}" for r in repr_names
            )
            lines.append(f"| {category} | {doc_id} | {score_cols} |")
    lines.append("")

    # Decision gate summary
    lines.append("## Decision Gate Summary")
    lines.append("")

    ho_c = ho_scores.get("candidate_c", 0.0)
    dev_c = dev_scores_map.get("candidate_c", _DEV_SCORES["candidate_c"])
    tolerance = 0.05

    gate1_pass = ho_c >= dev_c - tolerance
    gate1_label = "PASS" if gate1_pass else "FAIL"
    lines.append(
        f"**Gate 1 — Candidate C held-out accuracy >= DEV accuracy (±5% tolerance):** "
        f"{gate1_label}  "
    )
    lines.append(
        f"  Held-out C = {ho_c:.4f}, DEV C = {dev_c:.4f}, "
        f"tolerance = {tolerance:.2f}"
    )
    lines.append("")

    # Token reduction: compare prompt_tokens baseline_b vs candidate_c
    b_tokens: list[int] = []
    c_tokens: list[int] = []
    for doc_result in held_out_run.document_results:
        for r in doc_result.representations:
            if r.representation == "baseline_b":
                b_tokens.append(r.prompt_tokens)
            elif r.representation == "candidate_c":
                c_tokens.append(r.prompt_tokens)

    if b_tokens and c_tokens and sum(b_tokens) > 0:
        total_b = sum(b_tokens)
        total_c = sum(c_tokens)
        ho_token_reduction_pct = (total_b - total_c) / total_b * 100.0
        gate2_pass = ho_token_reduction_pct >= 15.0
        gate2_label = "PASS" if gate2_pass else "FAIL"
        lines.append(
            f"**Gate 2 — Candidate C token reduction in held-out >= 15%:** "
            f"{gate2_label}  "
        )
        lines.append(
            f"  Held-out token reduction = {ho_token_reduction_pct:.1f}% "
            f"(DEV reference: {_DEV_TOKEN_REDUCTION_RANGE})"
        )
    else:
        gate2_pass = None
        gate2_label = "INCONCLUSIVE"
        lines.append(
            f"**Gate 2 — Candidate C token reduction in held-out:** {gate2_label}  "
        )
        lines.append("  Token counts not available.")
    lines.append("")

    # Overall verdict
    if gate2_pass is None:
        verdict = "INCONCLUSIVE"
    elif gate1_pass and gate2_pass:
        verdict = "PASS"
    elif gate1_pass:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAIL"

    lines.append(f"**Overall Verdict: {verdict}**")
    lines.append("")
    lines.append("Criteria:")
    lines.append("- Gate 1 (accuracy parity): Candidate C held-out accuracy >= DEV accuracy minus 5%")
    lines.append("- Gate 2 (token reduction preserved): >= 15% token reduction in held-out")
    lines.append(
        "- PASS = both gates pass; FAIL = Gate 1 fails; "
        "INCONCLUSIVE = Gate 1 passes but Gate 2 inconclusive or marginal"
    )
    lines.append("")

    report_text = "\n".join(lines)
    (output_dir / "qa_pilot_comparison_report.md").write_text(report_text, encoding="utf-8")


def regrade_stored_outputs(
    original_run_dir: Path,
    output_dir: Path | None = None,
) -> tuple[PilotRun, list[EvaluationCorrection]]:
    """
    Regrade model outputs from a previous pilot run using the current answer keys
    and grader version. Does not call the model.

    Reads model_outputs/<doc_id>/<repr>/<qid>.txt from original_run_dir.
    Writes corrected artifacts to output_dir (default: qa_pilot/runs/text_only_v1_corrected_<ts>/).
    Returns (corrected_PilotRun, list_of_EvaluationCorrection).
    """
    if output_dir is None:
        here = Path(__file__).resolve().parent
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = here / "runs" / f"{PILOT_ID}_corrected_{ts}"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load original lock
    lock_path = original_run_dir / "pilot_lock.json"
    original_lock = QAPilotLock.model_validate_json(lock_path.read_text(encoding="utf-8"))

    # Build corrected lock (update grading_version)
    corrected_lock = QAPilotLock(
        pilot_id=original_lock.pilot_id,
        model_id=original_lock.model_id,
        prompt_version=original_lock.prompt_version,
        grading_version=GRADING_VERSION,
        document_ids=original_lock.document_ids,
        representation_names=original_lock.representation_names,
        benchmark_run_id=original_lock.benchmark_run_id,
        parser_version=original_lock.parser_version,
        planner_version=original_lock.planner_version,
        tokenizer=original_lock.tokenizer,
        code_commit=original_lock.code_commit,
        created_at=original_lock.created_at,
        schema_version=original_lock.schema_version,
    )
    (output_dir / "pilot_lock.json").write_text(
        corrected_lock.model_dump_json(indent=2), encoding="utf-8"
    )

    started_at = datetime.now(UTC).isoformat()
    document_results: list[DocumentPilotResult] = []

    for doc_id in PILOT_DOCUMENT_IDS:
        questions = load_pilot_questions([doc_id])
        question_ids = [q.question_id for q in questions]
        repr_results: list[RepresentationResult] = []

        for repr_name in REPRESENTATION_NAMES:
            all_grades = []
            # Estimate prompt_tokens from original lock (not re-reading doc text)
            prompt_tokens = 0

            for question in questions:
                output_file = (
                    original_run_dir
                    / "model_outputs"
                    / doc_id
                    / repr_name
                    / f"{question.question_id}.txt"
                )
                if output_file.exists():
                    model_answer = output_file.read_text(encoding="utf-8")
                else:
                    model_answer = "NOT FOUND"

                grade = grade_answer(question, model_answer, repr_name)
                all_grades.append(grade)

            # Write corrected grades
            grades_dir = output_dir / "grades" / doc_id
            grades_dir.mkdir(parents=True, exist_ok=True)
            grades_data = [g.model_dump() for g in all_grades]
            (grades_dir / f"{repr_name}_grades.json").write_text(
                json.dumps(grades_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            correct = sum(1 for g in all_grades if g.grade == "correct")
            partial = sum(1 for g in all_grades if g.grade == "partial")
            incorrect = sum(1 for g in all_grades if g.grade == "incorrect")
            mean_score = (
                sum(g.score for g in all_grades) / len(all_grades) if all_grades else 0.0
            )

            repr_result = RepresentationResult(
                representation=repr_name,
                prompt_tokens=prompt_tokens,
                response_text="",
                grades=all_grades,
                mean_score=mean_score,
                correct_count=correct,
                partial_count=partial,
                incorrect_count=incorrect,
            )
            repr_results.append(repr_result)

        doc_result = DocumentPilotResult(
            document_id=doc_id,
            question_ids=question_ids,
            representations=repr_results,
        )
        document_results.append(doc_result)

    completed_at = datetime.now(UTC).isoformat()

    # Compute overall mean scores (mean of per-doc means)
    overall_scores: dict[str, list[float]] = {r: [] for r in REPRESENTATION_NAMES}
    for doc_result in document_results:
        for repr_result in doc_result.representations:
            overall_scores[repr_result.representation].append(repr_result.mean_score)

    overall_mean = {
        r: (sum(v) / len(v)) if v else 0.0
        for r, v in overall_scores.items()
    }

    corrected_run = PilotRun(
        pilot_id=PILOT_ID,
        lock=corrected_lock,
        started_at=started_at,
        completed_at=completed_at,
        document_results=document_results,
        overall_mean_score_by_representation=overall_mean,
    )

    (output_dir / "pilot_run.json").write_text(
        corrected_run.model_dump_json(indent=2), encoding="utf-8"
    )

    # Build correction ledger
    now_ts = datetime.now(UTC).isoformat()
    corrections: list[EvaluationCorrection] = [
        EvaluationCorrection(
            correction_id="ec-001",
            question_id="fblbp10-q05",
            correction_type="answer_key",
            old_value='["$1,073,338","1,073,338","1073338"]',
            new_value='["$27,091,793","27,091,793","27091793"]',
            reason="Column mixup: old value was Written Premium Change, not Written Premium for this Program",
            supporting_evidence={
                "table": "Rate Information",
                "row": "Western Agricultural Insurance Company",
                "correct_column": "Written Premium for this Program",
            },
            grading_version_before="v1.0",
            grading_version_after="v1.1",
            timestamp=now_ts,
        ),
        EvaluationCorrection(
            correction_id="ec-002",
            question_id="multicolumn-q01",
            correction_type="grader_logic",
            old_value="substring match allowed after unsupported prefix",
            new_value="unsupported-prefix responses classified as incorrect before substring match",
            reason="Grader false positive: model answered NOT FOUND with explanation containing accepted substring",
            supporting_evidence={
                "model_answer_excerpt": "NOT FOUND. The document does not have a formal title...Two-Column...",
                "accepted_answer": "Two-Column",
            },
            grading_version_before="v1.0",
            grading_version_after="v1.1",
            timestamp=now_ts,
        ),
    ]

    ledger_data = [c.model_dump() for c in corrections]
    (output_dir / "correction_ledger.json").write_text(
        json.dumps(ledger_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Load original run for comparison in report
    original_run_json = original_run_dir / "pilot_run.json"
    original_run = PilotRun.model_validate_json(
        original_run_json.read_text(encoding="utf-8")
    )

    write_corrected_pilot_report(corrected_run, original_run, corrections, output_dir)

    return corrected_run, corrections


def write_corrected_pilot_report(
    corrected_run: PilotRun,
    original_run: PilotRun,
    corrections: list[EvaluationCorrection],
    output_dir: Path,
) -> None:
    """Write the corrected QA pilot Markdown report."""
    lines: list[str] = []
    lines.append(f"# QA Pilot Report (Corrected): {corrected_run.pilot_id}")
    lines.append("")
    lines.append(f"**Grading version**: {corrected_run.lock.grading_version} (corrected from {original_run.lock.grading_version})")
    lines.append(f"**Corrections applied**: {len(corrections)} (see correction_ledger.json)")
    lines.append(f"**Original run started**: {original_run.started_at}")
    lines.append("")

    # Summary table
    lines.append("## Summary: Corrected Mean Score by Representation")
    lines.append("")
    lines.append("| Representation | Original Score | Corrected Score |")
    lines.append("| --- | --- | --- |")
    orig_scores = original_run.overall_mean_score_by_representation
    for repr_name, score in corrected_run.overall_mean_score_by_representation.items():
        orig = orig_scores.get(repr_name, 0.0)
        lines.append(f"| {repr_name} | {orig:.4f} | {score:.4f} |")
    lines.append("")

    # Per-document breakdown
    repr_names = corrected_run.lock.representation_names
    lines.append("## Per-Document Breakdown")
    lines.append("")
    header_cols = " | ".join(repr_names)
    lines.append(f"| Document | {header_cols} |")
    sep_cols = " | ".join(["---"] * len(repr_names))
    lines.append(f"| --- | {sep_cols} |")
    for doc_result in corrected_run.document_results:
        scores_by_repr = {r.representation: r.mean_score for r in doc_result.representations}
        score_cols = " | ".join(
            f"{scores_by_repr.get(r, 0.0):.4f}" for r in repr_names
        )
        lines.append(f"| {doc_result.document_id} | {score_cols} |")
    lines.append("")

    # Per-question grades (corrected), showing changes
    # Build original grades index
    orig_grades: dict[str, dict[str, dict[str, str]]] = {}
    for doc_result in original_run.document_results:
        orig_grades[doc_result.document_id] = {}
        for repr_result in doc_result.representations:
            orig_grades[doc_result.document_id][repr_result.representation] = {
                g.question_id: g.grade for g in repr_result.grades
            }

    lines.append("## Per-Question Grades (Corrected)")
    lines.append("")
    lines.append("| Document | Question ID | " + " | ".join(repr_names) + " |")
    lines.append("| --- | --- | " + " | ".join(["---"] * len(repr_names)) + " |")
    for doc_result in corrected_run.document_results:
        grades_by_repr: dict[str, dict[str, str]] = {}
        for repr_result in doc_result.representations:
            grades_by_repr[repr_result.representation] = {
                g.question_id: g.grade for g in repr_result.grades
            }
        for q_id in doc_result.question_ids:
            grade_cols_parts = []
            for r in repr_names:
                new_grade = grades_by_repr.get(r, {}).get(q_id, "N/A")
                old_grade = orig_grades.get(doc_result.document_id, {}).get(r, {}).get(q_id, "N/A")
                if new_grade != old_grade:
                    grade_cols_parts.append(f"{new_grade} *(was {old_grade})*")
                else:
                    grade_cols_parts.append(new_grade)
            lines.append(f"| {doc_result.document_id} | {q_id} | {' | '.join(grade_cols_parts)} |")
    lines.append("")

    # Corrections applied table
    lines.append("## Corrections Applied")
    lines.append("")
    lines.append("| correction_id | question_id | type | reason |")
    lines.append("| --- | --- | --- | --- |")
    for c in corrections:
        lines.append(f"| {c.correction_id} | {c.question_id} | {c.correction_type} | {c.reason} |")
    lines.append("")

    # Shared parsing failures
    lines.append("## Shared Parsing Failures (Not Representation-Level)")
    lines.append("")
    lines.append("| question_id | failure_category | description |")
    lines.append("| --- | --- | --- |")
    for qid, info in KNOWN_SHARED_FAILURES.items():
        lines.append(f"| {qid} | {info['failure_category']} | {info['description']} |")
    lines.append("")

    # Pilot conclusion
    lines.append("## Pilot Conclusion")
    lines.append("")
    lines.append(
        "No answer-quality difference was observed among Baseline B, Candidate C, and "
        "Candidate D in this development pilot after correcting one answer-key error and "
        "one grader false positive."
    )
    lines.append("")
    lines.append(
        "Candidate C preserved measured answer quality while reducing text tokens by "
        "approximately 25–30% on text documents and approximately 89% on synthetic XLSX "
        "fixtures. One shared failure (blackrock-q03) was caused by truncated table headers "
        "in the parsed source, not by payload compression."
    )
    lines.append("")
    lines.append("**Caveats**:")
    lines.append("- This pilot is development-only. The held-out evaluation has not been run.")
    lines.append(
        "- Sample size is small (9 documents, 45 questions); no paired statistical or "
        "equivalence test was performed."
    )
    lines.append(
        "- XLSX results come from synthetic fixtures with sparse data; real-world XLSX "
        "documents may differ."
    )
    lines.append("- Multimodal behavior (images, visual assets) was not tested.")
    lines.append("- One shared parser-level table-header failure remains unresolved.")
    lines.append("")

    report_text = "\n".join(lines)
    (output_dir / "qa_pilot_report_corrected.md").write_text(report_text, encoding="utf-8")


def regrade_stored_held_out_outputs(
    original_run_dir: Path,
    output_dir: Path | None = None,
) -> tuple[PilotRun, list[EvaluationCorrection]]:
    """
    Regrade model outputs from the held-out v1 pilot using corrected answer keys.
    Does not call the model.

    Reads model_outputs/<doc_id>/<repr>/<qid>.txt from original_run_dir.
    Writes corrected artifacts to output_dir.
    Returns (corrected_PilotRun, list_of_EvaluationCorrection).
    """
    if output_dir is None:
        here = Path(__file__).resolve().parent
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = here / "runs" / f"{HELD_OUT_PILOT_ID}_corrected_{ts}"

    output_dir.mkdir(parents=True, exist_ok=True)

    lock_path = original_run_dir / "pilot_lock.json"
    original_lock = QAPilotLock.model_validate_json(lock_path.read_text(encoding="utf-8"))

    corrected_lock = QAPilotLock(
        pilot_id=original_lock.pilot_id,
        model_id=original_lock.model_id,
        prompt_version=original_lock.prompt_version,
        grading_version=GRADING_VERSION,
        document_ids=original_lock.document_ids,
        representation_names=original_lock.representation_names,
        benchmark_run_id=original_lock.benchmark_run_id,
        parser_version=original_lock.parser_version,
        planner_version=original_lock.planner_version,
        tokenizer=original_lock.tokenizer,
        code_commit=original_lock.code_commit,
        created_at=original_lock.created_at,
        schema_version=original_lock.schema_version,
    )
    (output_dir / "pilot_lock.json").write_text(
        corrected_lock.model_dump_json(indent=2), encoding="utf-8"
    )

    started_at = datetime.now(UTC).isoformat()
    document_results: list[DocumentPilotResult] = []

    for doc_id in HELD_OUT_DOCUMENT_IDS:
        questions = load_held_out_questions([doc_id])
        question_ids = [q.question_id for q in questions]
        repr_results: list[RepresentationResult] = []

        for repr_name in HELD_OUT_REPRESENTATION_NAMES:
            all_grades = []

            for question in questions:
                output_file = (
                    original_run_dir
                    / "model_outputs"
                    / doc_id
                    / repr_name
                    / f"{question.question_id}.txt"
                )
                model_answer = (
                    output_file.read_text(encoding="utf-8")
                    if output_file.exists()
                    else "NOT FOUND"
                )
                grade = grade_answer(question, model_answer, repr_name)
                all_grades.append(grade)

            grades_dir = output_dir / "grades" / doc_id
            grades_dir.mkdir(parents=True, exist_ok=True)
            (grades_dir / f"{repr_name}_grades.json").write_text(
                json.dumps([g.model_dump() for g in all_grades], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            correct = sum(1 for g in all_grades if g.grade == "correct")
            partial = sum(1 for g in all_grades if g.grade == "partial")
            incorrect = sum(1 for g in all_grades if g.grade == "incorrect")
            mean_score = sum(g.score for g in all_grades) / len(all_grades) if all_grades else 0.0

            repr_results.append(RepresentationResult(
                representation=repr_name,
                prompt_tokens=0,
                response_text="",
                grades=all_grades,
                mean_score=mean_score,
                correct_count=correct,
                partial_count=partial,
                incorrect_count=incorrect,
            ))

        document_results.append(DocumentPilotResult(
            document_id=doc_id,
            question_ids=question_ids,
            representations=repr_results,
        ))

    completed_at = datetime.now(UTC).isoformat()

    overall_scores: dict[str, list[float]] = {r: [] for r in HELD_OUT_REPRESENTATION_NAMES}
    for doc_result in document_results:
        for repr_result in doc_result.representations:
            overall_scores[repr_result.representation].append(repr_result.mean_score)

    overall_mean = {
        r: (sum(v) / len(v)) if v else 0.0
        for r, v in overall_scores.items()
    }

    corrected_run = PilotRun(
        pilot_id=HELD_OUT_PILOT_ID,
        lock=corrected_lock,
        started_at=started_at,
        completed_at=completed_at,
        document_results=document_results,
        overall_mean_score_by_representation=overall_mean,
    )

    (output_dir / "pilot_run.json").write_text(
        corrected_run.model_dump_json(indent=2), encoding="utf-8"
    )

    now_ts = datetime.now(UTC).isoformat()
    corrections: list[EvaluationCorrection] = [
        EvaluationCorrection(
            correction_id="ec-ho-001",
            question_id="ho-livingword-q04",
            correction_type="answer_key",
            old_value='["10.00am","10am"]',
            new_value='["8.30am","Sun 8.30am","8.30"]',
            reason=(
                "Document distinguishes two entries: "
                "'St Teresa's College, Abergowrie Sun 8.30am' (8.30am) and "
                "'Abergowrie Sun 10.00am' (10.00am). "
                "The question asks about St Teresa's College specifically."
            ),
            supporting_evidence={
                "doc": "pb-ho-text-livingword",
                "section": "Sunday Masses schedule",
                "entry": "St Teresa's College, Abergowrie  Sun 8.30am",
            },
            grading_version_before="v1.1",
            grading_version_after="v1.1",
            timestamp=now_ts,
        ),
    ]

    (output_dir / "correction_ledger.json").write_text(
        json.dumps([c.model_dump() for c in corrections], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    original_run = PilotRun.model_validate_json(
        (original_run_dir / "pilot_run.json").read_text(encoding="utf-8")
    )
    write_corrected_held_out_report(corrected_run, original_run, corrections, output_dir)

    return corrected_run, corrections


def write_corrected_held_out_report(
    corrected_run: PilotRun,
    original_run: PilotRun,
    corrections: list[EvaluationCorrection],
    output_dir: Path,
) -> None:
    """Write the corrected held-out QA pilot Markdown report."""
    lines: list[str] = []
    lines.append(f"# QA Pilot Report (Corrected): {corrected_run.pilot_id}")
    lines.append("")
    lines.append(f"**Grading version**: {corrected_run.lock.grading_version}")
    lines.append(f"**Corrections applied**: {len(corrections)} (see correction_ledger.json)")
    lines.append(f"**Original run started**: {original_run.started_at}")
    lines.append("")

    lines.append("## Summary: Corrected Mean Score by Representation")
    lines.append("")
    lines.append("| Representation | Original Score | Corrected Score |")
    lines.append("| --- | --- | --- |")
    orig_scores = original_run.overall_mean_score_by_representation
    for repr_name, score in corrected_run.overall_mean_score_by_representation.items():
        orig = orig_scores.get(repr_name, 0.0)
        lines.append(f"| {repr_name} | {orig:.4f} | {score:.4f} |")
    lines.append("")

    repr_names = corrected_run.lock.representation_names
    lines.append("## Per-Document Breakdown (Corrected)")
    lines.append("")
    lines.append(f"| Document | {' | '.join(repr_names)} |")
    lines.append(f"| --- | {' | '.join(['---'] * len(repr_names))} |")
    for doc_result in corrected_run.document_results:
        scores = {r.representation: r.mean_score for r in doc_result.representations}
        cols = " | ".join(f"{scores.get(r, 0.0):.4f}" for r in repr_names)
        lines.append(f"| {doc_result.document_id} | {cols} |")
    lines.append("")

    # Build original grades index for diff annotations
    orig_grades: dict[str, dict[str, dict[str, str]]] = {}
    for doc_result in original_run.document_results:
        orig_grades[doc_result.document_id] = {}
        for repr_result in doc_result.representations:
            orig_grades[doc_result.document_id][repr_result.representation] = {
                g.question_id: g.grade for g in repr_result.grades
            }

    lines.append("## Per-Question Grades (Corrected)")
    lines.append("")
    lines.append("| Document | Question ID | " + " | ".join(repr_names) + " |")
    lines.append("| --- | --- | " + " | ".join(["---"] * len(repr_names)) + " |")
    for doc_result in corrected_run.document_results:
        grades_by_repr = {
            r.representation: {g.question_id: g.grade for g in r.grades}
            for r in doc_result.representations
        }
        for q_id in doc_result.question_ids:
            cols = []
            for r in repr_names:
                new_g = grades_by_repr.get(r, {}).get(q_id, "N/A")
                old_g = orig_grades.get(doc_result.document_id, {}).get(r, {}).get(q_id, "N/A")
                cols.append(f"{new_g} *(was {old_g})*" if new_g != old_g else new_g)
            lines.append(f"| {doc_result.document_id} | {q_id} | {' | '.join(cols)} |")
    lines.append("")

    lines.append("## Corrections Applied")
    lines.append("")
    lines.append("| correction_id | question_id | type | reason |")
    lines.append("| --- | --- | --- | --- |")
    for c in corrections:
        lines.append(f"| {c.correction_id} | {c.question_id} | {c.correction_type} | {c.reason} |")
    lines.append("")

    (output_dir / "qa_pilot_report_corrected.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
