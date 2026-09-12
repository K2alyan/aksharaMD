"""Shared datatypes for the parsed-vs-raw harness.

Keeping the shared shapes in one module avoids circular imports between
the corpora, arms, judge, and aggregator packages.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    """A single QA pair grounded in a document.

    Fields mirror the QASPER shape: an extractive/abstractive/boolean/
    unanswerable classification plus a canonical gold answer string.
    """

    question: str
    gold_answer: str
    answer_type: str  # "extractive" | "abstractive" | "boolean" | "unanswerable"


@dataclass
class DocumentRecord:
    """A single document plus its grounded QA pairs."""

    doc_id: str
    pdf_bytes: bytes
    questions: list[Question]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ArmResult:
    """One arm's output for one (document, question) pair.

    ``readiness_score`` is ``None`` for the raw arm because raw-PDF
    ingestion has no extraction step to score. Parser arms populate it
    with the integer 0-100 value from the AksharaMD assessor.
    """

    doc_id: str
    question: str
    gold_answer: str
    arm: str  # "raw" | "markitdown" | "aksharamd-reference" | "marker" | "docling"
    answer: str
    readiness_score: int | None
    judge_score: int  # 0-10 from the LLM judge; -1 = operational failure
    correctness: float  # normalized 0-1 correctness derived from judge_score
    error: str = ""


class CorpusAdapter:
    """Protocol implemented by corpus loaders.

    Concrete adapters iterate documents; the driver applies a --limit N
    cap on top of a deterministic sort so pilots are repeatable.
    """

    name: str

    def iter_documents(self, limit: int | None = None) -> Iterator[DocumentRecord]:
        raise NotImplementedError


def judge_score_to_correctness(judge_score: int) -> float:
    """Map the 0-10 LLM-judge score to a normalized 0.0-1.0 correctness value.

    Operational failures (judge_score < 0) are mapped to 0.0 so callers can
    still compute aggregate correlations without discarding rows, but the
    aggregator also emits an unscored count for transparency.
    """
    if judge_score < 0:
        return 0.0
    if judge_score > 10:
        return 1.0
    return judge_score / 10.0
