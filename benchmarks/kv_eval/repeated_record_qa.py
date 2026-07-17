"""Structural QA comparison for repeated-record KeyValueGroups.

This is a deterministic, LLM-free QA harness. It measures whether the
serialised representation (Markdown or TSV) preserves record boundaries
well enough to answer per-record lookup questions.

Method
------
For each question, we build the KeyValueGroup from the fixture text using
the v2 experimental profile, render it as Markdown and TSV, and check
whether a scoped substring lookup around the record's differentiator key
(e.g. Day=Sunday) yields the expected answer.

A rendering is *correct* if it exposes the target field for the specified
record. It is *wrong_record* if the answer for another record also appears
in the same "record window" as the target — i.e. the record boundary
failed to separate them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QAQuestion:
    question: str
    fixture_text: str
    differentiator_key: str
    differentiator_value: str
    target_field: str
    expected_answer: str
    wrong_record_answer: str | None = None  # answer from the OTHER record


@dataclass
class QAResult:
    question: str
    format: str  # "prose", "markdown", "tsv"
    found_answer: str | None
    correct: bool
    wrong_record: bool
    tokens: int

    def model_dump(self) -> dict:
        return {
            "question": self.question,
            "format": self.format,
            "found_answer": self.found_answer,
            "correct": self.correct,
            "wrong_record": self.wrong_record,
            "tokens": self.tokens,
        }


_ABERGOWRIE = (
    "Location: Abergowrie\nDay: Saturday\nTime: 7:00 PM\n"
    "Location: Abergowrie\nDay: Sunday\nTime: 9:00 AM"
)
_MULTI_CONTACT = (
    "Name: Alice\nEmail: alice@example.com\n"
    "Name: Bob\nEmail: bob@example.com"
)
_MULTI_EVENT = (
    "Date: 15/06/2024\nTime: 6:00 PM\nVenue: Town Hall\n"
    "Date: 08/07/2024\nTime: 6:00 PM\nVenue: Conference Center"
)


def _questions() -> list[QAQuestion]:
    return [
        QAQuestion(
            question="What time is the Saturday service at Abergowrie?",
            fixture_text=_ABERGOWRIE,
            differentiator_key="Day",
            differentiator_value="Saturday",
            target_field="Time",
            expected_answer="7:00 PM",
            wrong_record_answer="9:00 AM",
        ),
        QAQuestion(
            question="What time is the Sunday service at Abergowrie?",
            fixture_text=_ABERGOWRIE,
            differentiator_key="Day",
            differentiator_value="Sunday",
            target_field="Time",
            expected_answer="9:00 AM",
            wrong_record_answer="7:00 PM",
        ),
        QAQuestion(
            question="What is Alice's email?",
            fixture_text=_MULTI_CONTACT,
            differentiator_key="Name",
            differentiator_value="Alice",
            target_field="Email",
            expected_answer="alice@example.com",
            wrong_record_answer="bob@example.com",
        ),
        QAQuestion(
            question="What is Bob's email?",
            fixture_text=_MULTI_CONTACT,
            differentiator_key="Name",
            differentiator_value="Bob",
            target_field="Email",
            expected_answer="bob@example.com",
            wrong_record_answer="alice@example.com",
        ),
        QAQuestion(
            question="What venue hosts the second event?",
            fixture_text=_MULTI_EVENT,
            differentiator_key="Date",
            differentiator_value="08/07/2024",
            target_field="Venue",
            expected_answer="Conference Center",
            wrong_record_answer="Town Hall",
        ),
    ]


def _build_group(text: str):
    from aksharamd.scoring.key_value_config import KeyValueDetectionProfile
    from aksharamd.scoring.key_value_detection import detect_key_value_entries

    profile = KeyValueDetectionProfile.experimental()
    result = detect_key_value_entries(text, page=1, profile=profile)
    return result.group


def _record_window(rendered: str, question: QAQuestion) -> str:
    """Return the substring around the record identified by the
    differentiator key/value.

    We search for the first occurrence of the differentiator value, then
    walk forward until we hit a record boundary marker or end of string.
    Record boundaries:
      - blank line (double newline)
      - "**Record N**" or "[Record N]" marker
    """
    needle = question.differentiator_value
    idx = rendered.find(needle)
    if idx < 0:
        return ""

    # Find start: go back to nearest record marker or start-of-text.
    start = 0
    for marker in ("**Record ", "[Record ", "\n\n"):
        pos = rendered.rfind(marker, 0, idx)
        if pos >= 0 and pos > start:
            start = pos

    # Find end: nearest downstream record marker.
    end = len(rendered)
    for marker in ("**Record ", "[Record ", "\n\n"):
        pos = rendered.find(marker, idx + len(needle))
        if pos >= 0 and pos < end:
            end = pos

    return rendered[start:end]


def _score_window(window: str, question: QAQuestion, fmt: str) -> QAResult:
    from aksharamd.packaging.token_accounting import count_text_tokens

    found_answer = None
    correct = False
    wrong_record = False

    # Look for expected answer in the window
    if question.expected_answer and question.expected_answer in window:
        found_answer = question.expected_answer
        correct = True

    # Check for wrong-record leakage inside the same window
    if (
        question.wrong_record_answer
        and question.wrong_record_answer in window
    ):
        wrong_record = True
        # correctness may still be False if the expected answer was NOT
        # found, but leakage always counts.

    tokens = count_text_tokens(window)

    return QAResult(
        question=question.question,
        format=fmt,
        found_answer=found_answer,
        correct=correct and not wrong_record,
        wrong_record=wrong_record,
        tokens=tokens,
    )


def run_qa_comparison() -> list[QAResult]:
    """Run the QA comparison across prose/markdown/TSV formats.

    Returns one QAResult per (question, format) combination.
    """
    from aksharamd.renderers.key_value_markdown import (
        render_key_value_group,
        render_key_value_tsv,
    )

    results: list[QAResult] = []
    for q in _questions():
        group = _build_group(q.fixture_text)
        if group is None:
            # No group inferred; record prose-only result.
            prose_window = _record_window(q.fixture_text, q)
            results.append(_score_window(prose_window, q, "prose"))
            results.append(_score_window("", q, "markdown"))
            results.append(_score_window("", q, "tsv"))
            continue

        prose_window = _record_window(q.fixture_text, q)
        md = render_key_value_group(group)
        md_window = _record_window(md, q)
        tsv = render_key_value_tsv(group)
        tsv_window = _record_window(tsv, q)

        results.append(_score_window(prose_window, q, "prose"))
        results.append(_score_window(md_window, q, "markdown"))
        results.append(_score_window(tsv_window, q, "tsv"))
    return results


def summarize_qa_results(results: list[QAResult]) -> dict:
    """Aggregate QAResult list into per-format accuracy summary."""
    by_fmt: dict[str, dict] = {}
    for r in results:
        b = by_fmt.setdefault(
            r.format,
            {"correct": 0, "wrong_record": 0, "total": 0, "tokens": 0},
        )
        b["total"] += 1
        b["tokens"] += r.tokens
        if r.correct:
            b["correct"] += 1
        if r.wrong_record:
            b["wrong_record"] += 1
    for fmt, b in by_fmt.items():
        b["accuracy"] = (
            round(b["correct"] / b["total"], 3) if b["total"] else 0.0
        )
        b["wrong_record_rate"] = (
            round(b["wrong_record"] / b["total"], 3) if b["total"] else 0.0
        )
        b["avg_tokens"] = (
            round(b["tokens"] / b["total"], 1) if b["total"] else 0.0
        )
    return by_fmt
