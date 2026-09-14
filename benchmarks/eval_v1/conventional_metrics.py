"""Non-LLM conventional-metric implementations for V1.

A.1b ships **only** the non-LLM metrics: word-overlap for prose gold
answers, number/word-overlap for numeric/table gold answers. These
metrics are extraction-facing — they measure whether the parser's
extracted markdown contains the gold answer's tokens — and are
independent of any live model call.

Live LLM answer + judge + downstream RAGAS metrics live behind an
interface here but return ``INFRASTRUCTURE_READY_NOT_EXECUTED`` at A.1
time. Executing them requires a freeze on provider/model/version,
prompts, decoding parameters, retries, timeouts, rubrics, response
parsing, nondeterminism handling, failure modes, cost accounting, and
provenance — none of which are settled by PROTOCOL_V1.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|\d+(?:[.,]\d+)*")
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)*")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in _NUMBER_RE.findall(text or "")]


@dataclass(frozen=True)
class OverlapResult:
    metric: str
    gold_token_count: int
    matched_token_count: int
    ratio: float
    gold_numbers: tuple[str, ...] = ()
    matched_numbers: tuple[str, ...] = ()


def word_overlap(gold_answer: str, extracted_markdown: str) -> OverlapResult:
    """Fraction of gold-answer tokens present in the extraction.

    Case-insensitive, punctuation-stripped, deduplicated on the gold
    side so a gold answer of "the the the" doesn't inflate the score.
    """
    gold_tokens = list(dict.fromkeys(_tokenize(gold_answer)))
    extraction_tokens = set(_tokenize(extracted_markdown))
    matched = [t for t in gold_tokens if t in extraction_tokens]
    ratio = (len(matched) / len(gold_tokens)) if gold_tokens else 0.0
    return OverlapResult(
        metric="word_overlap",
        gold_token_count=len(gold_tokens),
        matched_token_count=len(matched),
        ratio=ratio,
    )


def number_overlap(gold_answer: str, extracted_markdown: str) -> OverlapResult:
    """Fraction of gold-answer numeric literals present in the extraction.

    Punctuation-tolerant on numbers (commas stripped). Useful for
    TAT-DQA-style financial gold answers where numeric fidelity is the
    substantive question.
    """
    gold_nums = list(dict.fromkeys(_numbers(gold_answer)))
    extraction_nums = set(_numbers(extracted_markdown))
    matched = [n for n in gold_nums if n in extraction_nums]
    ratio = (len(matched) / len(gold_nums)) if gold_nums else 0.0
    return OverlapResult(
        metric="number_overlap",
        gold_token_count=len(gold_nums),
        matched_token_count=len(matched),
        ratio=ratio,
        gold_numbers=tuple(gold_nums),
        matched_numbers=tuple(matched),
    )


# --------------------------------------------------------------------
# LLM-based metric interfaces — infrastructure only under A.1.
# --------------------------------------------------------------------


LLM_METRIC_NOT_EXECUTED_REASON = (
    "live model execution is outside Authorization A.1 — provider, "
    "model, version, prompts, decoding parameters, retries, judge "
    "rubric, response parsing, nondeterminism handling, and cost "
    "accounting must be frozen before this stage can run."
)


@dataclass(frozen=True)
class LlmMetricStub:
    """Placeholder record emitted by the live-model interface at A.1."""

    metric: str
    executed: bool = False
    reason: str = LLM_METRIC_NOT_EXECUTED_REASON


def llm_answer_judge_metric(*args, **kwargs) -> LlmMetricStub:  # noqa: ARG001
    """Interface for the LLM-answer + LLM-judge metric.

    Do not execute under A.1. Returns a stub so the pipeline can record
    ``INFRASTRUCTURE_READY_NOT_EXECUTED`` at the corresponding stage.
    """
    return LlmMetricStub(metric="llm_answer_judge")


def downstream_ragas_metric(*args, **kwargs) -> LlmMetricStub:  # noqa: ARG001
    """Interface for the downstream-RAG demonstration.

    §6.2 is a separate product-utility experiment; its plumbing exists
    but its live execution is not authorized under A.1.
    """
    return LlmMetricStub(metric="downstream_ragas")
