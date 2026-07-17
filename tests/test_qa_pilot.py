"""Tests for benchmarks/document_package/qa_pilot (Benchmark C)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from benchmarks.document_package.qa_pilot.grading import (
    UNSUPPORTED_SENTINELS,
    compute_representation_scores,
    grade_answer,
)
from benchmarks.document_package.qa_pilot.questions import (
    PILOT_DOCUMENT_IDS,
    PILOT_QUESTIONS,
)
from benchmarks.document_package.qa_pilot.runner import (
    build_model_input,
    call_model_if_available,
    get_representation_text,
    load_pilot_questions,
)
from benchmarks.document_package.qa_pilot.schema import (
    DocumentPilotResult,
    PilotGradeResult,
    PilotRun,
    QAPilotLock,
    RepresentationResult,
    TableGradeBreakdown,
)
from benchmarks.document_package.schema import (
    AnswerKey,
    GradingMethod,
    QuestionRecord,
    QuestionType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BENCHMARK_RUN_BASE = (
    Path(__file__).resolve().parent.parent
    / "benchmarks"
    / "document_package"
    / "results"
    / "dev_20260714_185921"
    / "runs"
)


def _make_question(
    question_id: str = "test-q01",
    document_id: str = "test-doc",
    question: str = "What is X?",
    question_type: QuestionType = QuestionType.TEXT_RETRIEVAL,
    accepted_answers: list[str] | None = None,
    answer_type: str = "exact",
) -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        document_id=document_id,
        question=question,
        question_type=question_type,
        requires_visual=False,
        answer_key=AnswerKey(
            accepted_answers=accepted_answers or ["expected answer"],
            answer_type=answer_type,
            grading_method=GradingMethod.DETERMINISTIC,
            notes="",
        ),
    )


def _make_pilot_lock(**kwargs) -> QAPilotLock:
    defaults = dict(
        pilot_id="text_only_v1",
        model_id="claude-haiku-4-5-20251001",
        prompt_version="v1.0",
        grading_version="v1.0",
        document_ids=["doc-a"],
        representation_names=["baseline_b", "candidate_c"],
        benchmark_run_id="dev_20260714_185921",
        parser_version="0.3.6",
        planner_version="1.0",
        tokenizer="cl100k_base",
        code_commit="abc1234",
        created_at="2026-07-14T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return QAPilotLock(**defaults)


def _make_grade_result(**kwargs) -> PilotGradeResult:
    defaults = dict(
        question_id="test-q01",
        document_id="test-doc",
        representation="baseline_b",
        model_answer="expected answer",
        grade="correct",
        score=1.0,
        grading_method="v1.0",
    )
    defaults.update(kwargs)
    return PilotGradeResult(**defaults)


# ---------------------------------------------------------------------------
# 1. QAPilotLock serialization
# ---------------------------------------------------------------------------


def test_qa_pilot_lock_round_trip():
    lock = _make_pilot_lock()
    data = lock.model_dump_json()
    restored = QAPilotLock.model_validate_json(data)
    assert restored.pilot_id == lock.pilot_id
    assert restored.document_ids == lock.document_ids
    assert restored.schema_version == "1.0"


# ---------------------------------------------------------------------------
# 2. PilotGradeResult serialization
# ---------------------------------------------------------------------------


def test_pilot_grade_result_round_trip():
    grade = _make_grade_result(
        table_breakdown=TableGradeBreakdown(value_correct=True, row_correct=False)
    )
    data = grade.model_dump_json()
    restored = PilotGradeResult.model_validate_json(data)
    assert restored.question_id == grade.question_id
    assert restored.table_breakdown is not None
    assert restored.table_breakdown.value_correct is True


# ---------------------------------------------------------------------------
# 3. PilotRun serialization
# ---------------------------------------------------------------------------


def test_pilot_run_round_trip():
    lock = _make_pilot_lock()
    repr_result = RepresentationResult(
        representation="baseline_b",
        prompt_tokens=100,
        response_text="",
        grades=[_make_grade_result()],
        mean_score=1.0,
        correct_count=1,
        partial_count=0,
        incorrect_count=0,
    )
    doc_result = DocumentPilotResult(
        document_id="doc-a",
        question_ids=["test-q01"],
        representations=[repr_result],
    )
    run = PilotRun(
        pilot_id="text_only_v1",
        lock=lock,
        started_at="2026-07-14T00:00:00+00:00",
        completed_at="2026-07-14T00:01:00+00:00",
        document_results=[doc_result],
        overall_mean_score_by_representation={"baseline_b": 1.0},
    )
    data = run.model_dump_json()
    restored = PilotRun.model_validate_json(data)
    assert restored.pilot_id == run.pilot_id
    assert len(restored.document_results) == 1
    assert restored.overall_mean_score_by_representation["baseline_b"] == 1.0


# ---------------------------------------------------------------------------
# 4. Question set completeness
# ---------------------------------------------------------------------------


def test_question_set_all_document_ids_present():
    doc_ids_in_questions = {q["document_id"] for q in PILOT_QUESTIONS}
    for doc_id in PILOT_DOCUMENT_IDS:
        assert doc_id in doc_ids_in_questions, f"Missing doc_id: {doc_id}"


def test_question_set_total_count():
    assert len(PILOT_QUESTIONS) >= 40, f"Expected >= 40 questions, got {len(PILOT_QUESTIONS)}"


def test_question_set_each_doc_has_five():
    from collections import Counter

    counts = Counter(q["document_id"] for q in PILOT_QUESTIONS)
    for doc_id in PILOT_DOCUMENT_IDS:
        assert counts[doc_id] >= 5, f"{doc_id} has only {counts[doc_id]} questions"


# ---------------------------------------------------------------------------
# 5. Question IDs unique
# ---------------------------------------------------------------------------


def test_question_ids_unique():
    ids = [q["question_id"] for q in PILOT_QUESTIONS]
    assert len(ids) == len(set(ids)), "Duplicate question_ids found"


# ---------------------------------------------------------------------------
# 6. Accepted answers non-empty
# ---------------------------------------------------------------------------


def test_accepted_answers_non_empty():
    for q in PILOT_QUESTIONS:
        ak = q.get("answer_key", {})
        assert ak.get("accepted_answers"), (
            f"question_id={q['question_id']} has empty accepted_answers"
        )


# ---------------------------------------------------------------------------
# 7. Grading exact match — correct answer grades as correct
# ---------------------------------------------------------------------------


def test_grading_exact_correct():
    q = _make_question(accepted_answers=["expected answer"], answer_type="exact")
    result = grade_answer(q, "expected answer", "baseline_b")
    assert result.grade == "correct"
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# 8. Grading exact mismatch — wrong answer grades as incorrect
# ---------------------------------------------------------------------------


def test_grading_exact_mismatch():
    q = _make_question(accepted_answers=["expected answer"], answer_type="exact")
    result = grade_answer(q, "wrong answer here", "baseline_b")
    assert result.grade == "incorrect"
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# 9. Grading NOT FOUND — grades as incorrect when answer exists
# ---------------------------------------------------------------------------


def test_grading_not_found_when_answer_exists():
    q = _make_question(accepted_answers=["expected answer"], answer_type="exact")
    result = grade_answer(q, "NOT FOUND", "baseline_b")
    assert result.grade == "incorrect"
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# 10. Grading normalized numeric — "1,234.56" matches "1234.56"
# ---------------------------------------------------------------------------


def test_grading_normalized_comma_number():
    q = _make_question(accepted_answers=["1234.56"], answer_type="normalized")
    result = grade_answer(q, "1,234.56", "candidate_c")
    assert result.grade == "correct"
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# 11. Grading normalized percent — "4.4%" matches "4.4"
# ---------------------------------------------------------------------------


def test_grading_normalized_percent():
    q = _make_question(accepted_answers=["4.4"], answer_type="normalized")
    result = grade_answer(q, "4.4%", "candidate_c")
    assert result.grade == "correct"
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# 12. Table breakdown populated for table_lookup questions
# ---------------------------------------------------------------------------


def test_table_breakdown_populated():
    q = _make_question(
        question_type=QuestionType.TABLE_LOOKUP,
        accepted_answers=["42"],
        answer_type="normalized",
    )
    result = grade_answer(q, "42", "candidate_d")
    assert result.table_breakdown is not None
    assert result.table_breakdown.value_correct is True


def test_table_breakdown_not_populated_for_text_retrieval():
    q = _make_question(
        question_type=QuestionType.TEXT_RETRIEVAL,
        accepted_answers=["hello"],
        answer_type="exact",
    )
    result = grade_answer(q, "hello", "baseline_b")
    assert result.table_breakdown is None


# ---------------------------------------------------------------------------
# 13. compute_representation_scores returns correct mean
# ---------------------------------------------------------------------------


def test_compute_representation_scores():
    grades = [
        _make_grade_result(representation="baseline_b", score=1.0),
        _make_grade_result(representation="baseline_b", score=0.0),
        _make_grade_result(representation="candidate_c", score=0.5),
        _make_grade_result(representation="candidate_c", score=1.0),
    ]
    scores = compute_representation_scores(grades)
    assert scores["baseline_b"] == pytest.approx(0.5)
    assert scores["candidate_c"] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# 14. get_representation_text loads baseline_b_document.md
# ---------------------------------------------------------------------------


def test_get_representation_text_baseline_b():
    # Use the first document in the pilot that we know exists
    doc_id = "syn-docx-01"
    run_dir = BENCHMARK_RUN_BASE / doc_id
    if not run_dir.exists():
        pytest.skip("Benchmark run dir not found — skipping integration test")
    text = get_representation_text(run_dir, "baseline_b")
    assert len(text) > 0
    assert "AksharaMD" in text or "Benchmark" in text


def test_get_representation_text_file_not_found():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        with pytest.raises(FileNotFoundError):
            get_representation_text(run_dir, "baseline_b")


# ---------------------------------------------------------------------------
# 15. build_model_input returns dict with "system" and "user" keys
# ---------------------------------------------------------------------------


def test_build_model_input_keys():
    result = build_model_input("Some document text.", "What is the answer?")
    assert "system" in result
    assert "user" in result
    assert "What is the answer?" in result["user"]
    assert "Some document text." in result["user"]


# ---------------------------------------------------------------------------
# 16. call_model_if_available returns None when ANTHROPIC_API_KEY is not set
# ---------------------------------------------------------------------------


def test_call_model_returns_none_without_api_key():
    with patch.dict(os.environ, {}, clear=True):
        # Ensure key is not present
        os.environ.pop("ANTHROPIC_API_KEY", None)
        result = call_model_if_available(
            "claude-haiku-4-5-20251001",
            "System prompt",
            "User message",
        )
    assert result is None


# ---------------------------------------------------------------------------
# Extra: load_pilot_questions filtering
# ---------------------------------------------------------------------------


def test_load_pilot_questions_all():
    questions = load_pilot_questions()
    assert len(questions) >= 40


def test_load_pilot_questions_filter():
    questions = load_pilot_questions(["syn-docx-01"])
    assert all(q.document_id == "syn-docx-01" for q in questions)
    assert len(questions) >= 5


# ---------------------------------------------------------------------------
# Extra: dry_run mode works without API key
# ---------------------------------------------------------------------------


def test_run_pilot_dry_run_smoke():
    """Smoke test: dry_run mode should complete without API calls."""
    from benchmarks.document_package.qa_pilot.runner import run_pilot

    run_dir_base = BENCHMARK_RUN_BASE
    if not run_dir_base.exists():
        pytest.skip("Benchmark run dir not found — skipping integration test")

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp) / "pilot_out"
        pilot_run = run_pilot(
            benchmark_run_id="dev_20260714_185921",
            results_base_dir=run_dir_base,
            output_dir=output_dir,
            dry_run=True,
        )

    assert pilot_run.pilot_id == "text_only_v1"
    assert len(pilot_run.document_results) == len(PILOT_DOCUMENT_IDS)
    # All grades should be "incorrect" since dry_run uses "NOT FOUND"
    for doc_result in pilot_run.document_results:
        for repr_result in doc_result.representations:
            for grade in repr_result.grades:
                assert grade.grade in {"correct", "partial", "incorrect"}


# ---------------------------------------------------------------------------
# New tests: corrected answer key and grader logic fixes
# ---------------------------------------------------------------------------


def test_fblb_corrected_answer_scores_correct():
    """fblbp10-q05 with corrected answer key: $27,091,793 should grade correct."""
    q = _make_question(
        question_id="fblbp10-q05",
        document_id="pb-table-fblb-p10",
        question="What is the Written Premium for this Program for Western Agricultural?",
        question_type=QuestionType.TABLE_LOOKUP,
        accepted_answers=["$27,091,793", "27,091,793", "27091793"],
        answer_type="normalized",
    )
    result = grade_answer(q, "$27,091,793", "baseline_b")
    assert result.grade == "correct"
    assert result.score == 1.0


def test_fblb_old_wrong_answer_rejected():
    """Old (wrong) answer key value $1,073,338 should grade incorrect against corrected key."""
    q = _make_question(
        question_id="fblbp10-q05",
        document_id="pb-table-fblb-p10",
        question="What is the Written Premium for this Program for Western Agricultural?",
        question_type=QuestionType.TABLE_LOOKUP,
        accepted_answers=["$27,091,793", "27,091,793", "27091793"],
        answer_type="normalized",
    )
    result = grade_answer(q, "$1,073,338", "baseline_b")
    assert result.grade == "incorrect"
    assert result.score == 0.0


def test_not_found_prefix_with_accepted_substring_is_incorrect():
    """Model answer starting with 'NOT FOUND' but containing accepted substring is incorrect."""
    q = _make_question(
        accepted_answers=["Two-Column"],
        answer_type="semantic",
    )
    model_answer = (
        "NOT FOUND. The document does not have a formal title, "
        "although it has a section Two-Column."
    )
    result = grade_answer(q, model_answer, "baseline_b")
    assert result.grade == "incorrect"
    assert result.score == 0.0


def test_plain_correct_answer_still_correct():
    """A plain accepted answer grades correct (no regression from sentinel fix)."""
    q = _make_question(
        accepted_answers=["Two-Column"],
        answer_type="semantic",
    )
    result = grade_answer(q, "Two-Column", "baseline_b")
    assert result.grade == "correct"
    assert result.score == 1.0


def test_semantic_not_found_expected_still_correct():
    """When accepted_answers=['NOT FOUND'], grading 'NOT FOUND' is correct."""
    q = _make_question(
        accepted_answers=["NOT FOUND"],
        answer_type="exact",
    )
    result = grade_answer(q, "NOT FOUND", "baseline_b")
    assert result.grade == "correct"
    assert result.score == 1.0


def test_unsupported_sentinels_all_reject():
    """Each sentinel in UNSUPPORTED_SENTINELS grades incorrect when a real answer is expected."""
    q = _make_question(
        accepted_answers=["expected answer"],
        answer_type="exact",
    )
    for sentinel in UNSUPPORTED_SENTINELS:
        result = grade_answer(q, sentinel, "baseline_b")
        assert result.grade == "incorrect", (
            f"Sentinel '{sentinel}' should grade incorrect but got '{result.grade}'"
        )
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# Held-out question set tests
# ---------------------------------------------------------------------------


def test_held_out_question_set_completeness():
    """All 6 held-out doc IDs present, 30 total questions, each doc has exactly 5."""
    from collections import Counter

    from benchmarks.document_package.qa_pilot.held_out_questions import (
        HELD_OUT_DOCUMENT_IDS,
        HELD_OUT_QUESTIONS,
    )

    assert len(HELD_OUT_QUESTIONS) == 30, (
        f"Expected 30 held-out questions, got {len(HELD_OUT_QUESTIONS)}"
    )
    doc_ids_in_questions = {q["document_id"] for q in HELD_OUT_QUESTIONS}
    for doc_id in HELD_OUT_DOCUMENT_IDS:
        assert doc_id in doc_ids_in_questions, f"Missing held-out doc_id: {doc_id}"
    counts = Counter(q["document_id"] for q in HELD_OUT_QUESTIONS)
    for doc_id in HELD_OUT_DOCUMENT_IDS:
        assert counts[doc_id] == 5, (
            f"{doc_id} has {counts[doc_id]} questions, expected exactly 5"
        )


def test_held_out_question_ids_unique():
    """No duplicate question_id in held-out question set."""
    from benchmarks.document_package.qa_pilot.held_out_questions import HELD_OUT_QUESTIONS

    ids = [q["question_id"] for q in HELD_OUT_QUESTIONS]
    assert len(ids) == len(set(ids)), "Duplicate question_ids found in HELD_OUT_QUESTIONS"


def test_held_out_accepted_answers_nonempty():
    """Every held-out question has at least one accepted answer."""
    from benchmarks.document_package.qa_pilot.held_out_questions import HELD_OUT_QUESTIONS

    for q in HELD_OUT_QUESTIONS:
        ak = q.get("answer_key", {})
        assert ak.get("accepted_answers"), (
            f"question_id={q['question_id']} has empty accepted_answers"
        )


def test_load_held_out_questions():
    """load_held_out_questions() returns 30 QuestionRecord objects."""
    from benchmarks.document_package.qa_pilot.runner import load_held_out_questions

    questions = load_held_out_questions()
    assert len(questions) == 30, (
        f"Expected 30 QuestionRecord objects, got {len(questions)}"
    )
    for q in questions:
        assert isinstance(q, QuestionRecord)


def test_run_held_out_pilot_dry_run():
    """dry_run=True completes without error; returns PilotRun with 6 docs, 30 questions graded incorrect."""
    from benchmarks.document_package.qa_pilot.runner import (
        HELD_OUT_DOCUMENT_IDS,
        run_held_out_pilot,
    )

    held_out_run_base = (
        Path(__file__).resolve().parent.parent
        / "benchmarks"
        / "document_package"
        / "results"
        / "held_out_20260714_214543"
        / "runs"
    )
    if not held_out_run_base.exists():
        pytest.skip("Held-out benchmark run dir not found — skipping integration test")

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp) / "held_out_pilot_out"
        pilot_run = run_held_out_pilot(
            benchmark_run_id="held_out_20260714_214543",
            results_base_dir=held_out_run_base,
            output_dir=output_dir,
            dry_run=True,
        )

    assert pilot_run.pilot_id == "text_only_held_out_v1"
    assert len(pilot_run.document_results) == len(HELD_OUT_DOCUMENT_IDS)

    total_questions = sum(
        len(doc_result.question_ids) for doc_result in pilot_run.document_results
    )
    assert total_questions == 30, (
        f"Expected 30 total questions across docs, got {total_questions}"
    )

    # dry_run uses "NOT FOUND" which grades as incorrect for all real-answer questions
    for doc_result in pilot_run.document_results:
        for repr_result in doc_result.representations:
            for grade in repr_result.grades:
                assert grade.grade == "incorrect", (
                    f"Expected 'incorrect' in dry_run for {grade.question_id} "
                    f"({repr_result.representation}), got '{grade.grade}'"
                )
