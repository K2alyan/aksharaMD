"""Deterministic grader for QA Pilot answers."""

from __future__ import annotations

import re

from benchmarks.document_package.qa_pilot.schema import (
    PilotGradeResult,
    TableGradeBreakdown,
)
from benchmarks.document_package.schema import QuestionRecord, QuestionType

GRADING_VERSION = "v1.1"

UNSUPPORTED_SENTINELS: tuple[str, ...] = (
    "not found",
    "insufficient information",
    "not available",
    "cannot determine",
    "unable to determine",
    "not stated",
    "not mentioned",
    "not provided",
)


def _normalize_text(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_unsupported_response(text: str) -> bool:
    """Return True if text is an unsupported-response sentinel (e.g. 'NOT FOUND ...')."""
    norm = _normalize_text(text)
    for sentinel in UNSUPPORTED_SENTINELS:
        if norm == sentinel:
            return True
        # Sentinel followed by space or punctuation (e.g. "not found. the doc...")
        if norm.startswith(sentinel) and len(norm) > len(sentinel):
            next_char = norm[len(sentinel)]
            if next_char in (" ", ".", ",", ":", ";", "!", "?", "\n", "\r"):
                return True
    return False


def _normalize_number(text: str) -> float | None:
    """Strip commas, percent signs, dollar signs and parse as float. Returns None on failure."""
    cleaned = text.strip().replace(",", "").replace("%", "").replace("$", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _grade_exact(model_norm: str, accepted_norms: list[str]) -> str:
    """Return 'correct' or 'incorrect' using substring containment in either direction."""
    for ans in accepted_norms:
        if model_norm == ans:
            return "correct"
        if ans and ans in model_norm:
            return "correct"
        if model_norm and model_norm in ans:
            return "correct"
    return "incorrect"


def _grade_normalized(model_answer: str, accepted_answers: list[str]) -> str:
    """Compare numeric values within 0.1% relative tolerance."""
    model_val = _normalize_number(model_answer)
    if model_val is None:
        # Fall back to exact text match
        model_norm = _normalize_text(model_answer)
        accepted_norms = [_normalize_text(a) for a in accepted_answers]
        return _grade_exact(model_norm, accepted_norms)
    for ans in accepted_answers:
        ans_val = _normalize_number(ans)
        if ans_val is None:
            continue
        if ans_val == 0.0:
            if model_val == 0.0:
                return "correct"
            continue
        rel_diff = abs(model_val - ans_val) / abs(ans_val)
        if rel_diff <= 0.001:
            return "correct"
    return "incorrect"


def _grade_semantic(model_answer: str, accepted_answers: list[str]) -> tuple[str, float]:
    """
    Return (grade, score).
    - correct (1.0) if any accepted_answer is substring of model_answer
    - partial (0.5) if model_answer non-empty and not NOT FOUND
    - incorrect (0.0) if NOT FOUND or empty
    """
    model_norm = _normalize_text(model_answer)
    for ans in accepted_answers:
        if _normalize_text(ans) in model_norm:
            return "correct", 1.0
    if model_norm and model_norm != "not found":
        return "partial", 0.5
    return "incorrect", 0.0


def _build_table_breakdown(
    grade: str,
    model_answer: str,
    accepted_answers: list[str],
) -> TableGradeBreakdown:
    """Build a TableGradeBreakdown based on grade result."""
    value_correct = grade == "correct"
    return TableGradeBreakdown(
        value_correct=value_correct,
        row_correct=None,
        column_correct=None,
        unit_correct=None,
        provenance_correct=None,
    )


def grade_answer(
    question: QuestionRecord,
    model_answer: str,
    representation: str,
) -> PilotGradeResult:
    """Grade a single model answer against the question's answer key."""
    answer_key = question.answer_key
    accepted_answers = answer_key.accepted_answers
    answer_type = answer_key.answer_type

    # Handle NOT FOUND special case
    model_stripped = model_answer.strip()
    is_not_found = _is_unsupported_response(model_stripped) or model_stripped == ""

    # If accepted answers list has "NOT FOUND" as first entry, that's the expected answer
    expects_not_found = (
        accepted_answers
        and _normalize_text(accepted_answers[0]) == "not found"
    )

    if is_not_found and not expects_not_found:
        table_breakdown = None
        if question.question_type == QuestionType.TABLE_LOOKUP:
            table_breakdown = TableGradeBreakdown(value_correct=False)
        return PilotGradeResult(
            question_id=question.question_id,
            document_id=question.document_id,
            representation=representation,
            model_answer=model_answer,
            grade="incorrect",
            score=0.0,
            grading_method=GRADING_VERSION,
            table_breakdown=table_breakdown,
            notes="Model answered NOT FOUND but answer exists.",
        )

    if is_not_found and expects_not_found:
        table_breakdown = None
        if question.question_type == QuestionType.TABLE_LOOKUP:
            table_breakdown = TableGradeBreakdown(value_correct=True)
        return PilotGradeResult(
            question_id=question.question_id,
            document_id=question.document_id,
            representation=representation,
            model_answer=model_answer,
            grade="correct",
            score=1.0,
            grading_method=GRADING_VERSION,
            table_breakdown=table_breakdown,
            notes="Expected NOT FOUND; model answered correctly.",
        )

    # Grade based on answer_type
    grade: str
    score: float
    notes: str = ""

    if answer_type == "exact":
        model_norm = _normalize_text(model_stripped)
        accepted_norms = [_normalize_text(a) for a in accepted_answers]
        result = _grade_exact(model_norm, accepted_norms)
        grade = result
        score = 1.0 if result == "correct" else 0.0

    elif answer_type == "normalized":
        result = _grade_normalized(model_stripped, accepted_answers)
        grade = result
        score = 1.0 if result == "correct" else 0.0

    elif answer_type == "semantic":
        grade, score = _grade_semantic(model_stripped, accepted_answers)

    else:
        # unsupported or unknown
        grade = "unscorable"
        score = 0.0
        notes = f"Unsupported answer_type: {answer_type}"

    table_breakdown = None
    if question.question_type == QuestionType.TABLE_LOOKUP:
        table_breakdown = _build_table_breakdown(grade, model_stripped, accepted_answers)

    return PilotGradeResult(
        question_id=question.question_id,
        document_id=question.document_id,
        representation=representation,
        model_answer=model_answer,
        grade=grade,
        score=score,
        grading_method=GRADING_VERSION,
        table_breakdown=table_breakdown,
        notes=notes,
    )


def grade_document(
    questions: list[QuestionRecord],
    answers_by_representation: dict[str, list[str]],
) -> list[PilotGradeResult]:
    """
    Grade all questions for all representations.

    answers_by_representation: repr_name -> model answers in the same order as questions.
    """
    results: list[PilotGradeResult] = []
    for repr_name, answers in answers_by_representation.items():
        for question, answer in zip(questions, answers):
            results.append(grade_answer(question, answer, repr_name))
    return results


def compute_representation_scores(grades: list[PilotGradeResult]) -> dict[str, float]:
    """Return mean score per representation."""
    scores_by_repr: dict[str, list[float]] = {}
    for grade in grades:
        scores_by_repr.setdefault(grade.representation, []).append(grade.score)
    return {
        repr_name: (sum(scores) / len(scores)) if scores else 0.0
        for repr_name, scores in scores_by_repr.items()
    }
