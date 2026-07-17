"""Schema models for the QA Pilot (Benchmark C)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QAPilotLock(BaseModel):
    pilot_id: str
    model_id: str
    prompt_version: str
    grading_version: str
    document_ids: list[str]
    representation_names: list[str]
    benchmark_run_id: str
    parser_version: str
    planner_version: str
    tokenizer: str
    code_commit: str
    created_at: str
    schema_version: str = "1.0"


class TableGradeBreakdown(BaseModel):
    value_correct: bool | None = None
    row_correct: bool | None = None
    column_correct: bool | None = None
    unit_correct: bool | None = None
    provenance_correct: bool | None = None


class PilotGradeResult(BaseModel):
    question_id: str
    document_id: str
    representation: str
    model_answer: str
    grade: Literal["correct", "partial", "incorrect", "unscorable"]
    score: float
    grading_method: str
    table_breakdown: TableGradeBreakdown | None = None
    notes: str = ""


class RepresentationResult(BaseModel):
    representation: str
    prompt_tokens: int
    response_text: str
    grades: list[PilotGradeResult] = Field(default_factory=list)
    mean_score: float
    correct_count: int
    partial_count: int
    incorrect_count: int


class DocumentPilotResult(BaseModel):
    document_id: str
    question_ids: list[str]
    representations: list[RepresentationResult] = Field(default_factory=list)


class PilotRun(BaseModel):
    pilot_id: str
    lock: QAPilotLock
    started_at: str
    completed_at: str | None = None
    document_results: list[DocumentPilotResult] = Field(default_factory=list)
    overall_mean_score_by_representation: dict[str, float] = Field(default_factory=dict)
    schema_version: str = "1.0"


class EvaluationCorrection(BaseModel):
    correction_id: str
    question_id: str
    correction_type: str          # "answer_key" | "grader_logic"
    old_value: str | None = None
    new_value: str | None = None
    reason: str
    supporting_evidence: dict = Field(default_factory=dict)
    grading_version_before: str
    grading_version_after: str
    timestamp: str
