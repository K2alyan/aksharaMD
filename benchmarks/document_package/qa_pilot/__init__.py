"""QA Pilot for AksharaMD document packaging benchmark (Benchmark C)."""

from benchmarks.document_package.qa_pilot.grading import (
    compute_representation_scores,
    grade_answer,
    grade_document,
)
from benchmarks.document_package.qa_pilot.runner import (
    load_pilot_questions,
    regrade_stored_outputs,
    run_pilot,
)
from benchmarks.document_package.qa_pilot.schema import (
    DocumentPilotResult,
    EvaluationCorrection,
    PilotGradeResult,
    PilotRun,
    QAPilotLock,
    RepresentationResult,
    TableGradeBreakdown,
)

__all__ = [
    "load_pilot_questions",
    "run_pilot",
    "regrade_stored_outputs",
    "grade_answer",
    "grade_document",
    "compute_representation_scores",
    "QAPilotLock",
    "PilotGradeResult",
    "RepresentationResult",
    "DocumentPilotResult",
    "PilotRun",
    "TableGradeBreakdown",
    "EvaluationCorrection",
]
