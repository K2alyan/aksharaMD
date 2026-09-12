"""Tests for the parsed-vs-raw evaluation harness.

Everything here runs offline. No real Anthropic call is made. No arxiv
download is attempted. The QASPER loader test only checks the shape of
what would be yielded if ``datasets`` were installed; if it's not, the
test skips.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from benchmarks.parsed_vs_raw import aggregate
from benchmarks.parsed_vs_raw.arms.parser_arm import ParserArm, ParserUnavailable
from benchmarks.parsed_vs_raw.corpora.qasper import (
    QasperCorpus,
    _canonical_answer,
    _extract_questions,
)
from benchmarks.parsed_vs_raw.llm_client import (
    load_fixture_client,
    set_client,
)
from benchmarks.parsed_vs_raw.run import main as run_main
from benchmarks.parsed_vs_raw.types import ArmResult, Question

FIXTURES = Path(__file__).resolve().parent.parent / "benchmarks" / "parsed_vs_raw" / "fixtures"
TINY_PDF = FIXTURES / "tiny.pdf"
LLM_FIXTURE = FIXTURES / "llm_responses.json"


# -- QASPER shape ---------------------------------------------------------


def test_extract_questions_from_qasper_row_shape() -> None:
    """QASPER row -> Question conversion handles the four answer types."""
    row: dict[str, Any] = {
        "id": "2109.12345",
        "qas": {
            "question": [
                "What is the main contribution?",
                "Is BERT used?",
                "What accuracy did they report?",
                "What corpus was used?",
            ],
            "answers": [
                {
                    "answer": [
                        {
                            "unanswerable": False,
                            "extractive_spans": ["a new decoding algorithm"],
                            "yes_no": None,
                            "free_form_answer": "",
                        }
                    ]
                },
                {
                    "answer": [
                        {
                            "unanswerable": False,
                            "extractive_spans": [],
                            "yes_no": True,
                            "free_form_answer": "",
                        }
                    ]
                },
                {
                    "answer": [
                        {
                            "unanswerable": False,
                            "extractive_spans": [],
                            "yes_no": None,
                            "free_form_answer": "92.3 F1",
                        }
                    ]
                },
                {
                    "answer": [
                        {
                            "unanswerable": True,
                            "extractive_spans": [],
                            "yes_no": None,
                            "free_form_answer": "",
                        }
                    ]
                },
            ],
        },
    }
    questions = _extract_questions(row)
    assert len(questions) == 4
    assert questions[0].answer_type == "extractive"
    assert "decoding algorithm" in questions[0].gold_answer
    assert questions[1].answer_type == "boolean"
    assert questions[1].gold_answer == "Yes"
    assert questions[2].answer_type == "abstractive"
    assert "92.3" in questions[2].gold_answer
    assert questions[3].answer_type == "unanswerable"
    assert questions[3].gold_answer == ""


def test_canonical_answer_prefers_answerable_annotator_over_unanswerable() -> None:
    group = {
        "answer": [
            {"unanswerable": True, "extractive_spans": [], "yes_no": None, "free_form_answer": ""},
            {
                "unanswerable": False,
                "extractive_spans": [],
                "yes_no": None,
                "free_form_answer": "a small dataset",
            },
        ]
    }
    gold, atype = _canonical_answer(group)
    assert gold == "a small dataset"
    assert atype == "abstractive"


def test_qasper_corpus_loader_yields_expected_shape(tmp_path: Path) -> None:
    """Smoke: the loader chains dataset rows into DocumentRecord objects.

    We monkeypatch ``datasets.load_dataset`` and ``_download_pdf`` so no
    network traffic occurs. Skipped if ``datasets`` is not installed.
    """
    pytest.importorskip("datasets")
    from benchmarks.parsed_vs_raw.corpora import qasper as qasper_module

    fake_rows = [
        {
            "id": "2101.00001",
            "title": "Fake paper A",
            "qas": {
                "question": ["Q1?"],
                "answers": [
                    {
                        "answer": [
                            {
                                "unanswerable": False,
                                "extractive_spans": ["answer one"],
                                "yes_no": None,
                                "free_form_answer": "",
                            }
                        ]
                    }
                ],
            },
        },
        {
            "id": "2101.00002",
            "title": "Fake paper B",
            "qas": {"question": ["Q2?"], "answers": [{"answer": [{"unanswerable": True}]}]},
        },
    ]

    def fake_load_dataset(name: str, split: str, revision: str | None = None) -> list[dict[str, Any]]:
        assert name == "allenai/qasper"
        assert split == "validation"
        assert revision is not None
        return fake_rows

    def fake_download(url: str) -> bytes:
        return TINY_PDF.read_bytes()

    with patch.object(qasper_module, "load_dataset", fake_load_dataset, create=True), patch.object(
        qasper_module, "_download_pdf", fake_download
    ):
        # Route the load_dataset call through the module. QasperCorpus
        # imports it inline, so we patch the module-level symbol.
        corpus = QasperCorpus(
            cache_dir=str(tmp_path / "qasper_cache"),
            arxiv_sleep_seconds=0.0,
        )
        # The corpus imports datasets.load_dataset inside iter_documents;
        # override sys.modules mapping so the inline import resolves to
        # our fake without touching real HF.
        import sys
        import types

        stub = types.SimpleNamespace(load_dataset=fake_load_dataset)
        sys.modules["datasets"] = stub  # type: ignore[assignment]
        try:
            docs = list(corpus.iter_documents(limit=2))
        finally:
            sys.modules.pop("datasets", None)
    assert [d.doc_id for d in docs] == ["2101.00001", "2101.00002"]
    # Doc B has only an unanswerable question => it's kept but with
    # answer_type "unanswerable" and gold_answer == "".
    assert docs[0].questions[0].question == "Q1?"
    assert docs[0].questions[0].gold_answer == "answer one"
    assert docs[1].questions[0].answer_type == "unanswerable"


# -- Parser arm -----------------------------------------------------------


def test_parser_arm_produces_markdown_and_readiness_score() -> None:
    """Running the aksharamd parser arm on a real (tiny) PDF yields markdown + a score."""
    pytest.importorskip("aksharamd")
    arm = ParserArm(parser="aksharamd", answer_model="unused", judge_model="unused")
    pdf_bytes = TINY_PDF.read_bytes()
    extraction = arm.extract(pdf_bytes)
    assert extraction.markdown, "expected non-empty markdown from aksharamd"
    assert isinstance(extraction.readiness_score, int)
    assert 0 <= extraction.readiness_score <= 100
    assert extraction.parser_name == "aksharamd"


def test_parser_arm_skips_when_optional_dep_missing() -> None:
    """Missing docling is surfaced as ParserUnavailable, not a crash."""
    arm = ParserArm(parser="docling", answer_model="unused", judge_model="unused")
    # Force the import branch by shadowing docling with a broken module.
    import sys

    sentinel = object()
    original = sys.modules.get("docling")
    sys.modules["docling"] = sentinel  # type: ignore[assignment]
    try:
        with pytest.raises((ParserUnavailable, AttributeError, ImportError)):
            arm.extract(TINY_PDF.read_bytes())
    finally:
        if original is None:
            sys.modules.pop("docling", None)
        else:
            sys.modules["docling"] = original


# -- Aggregation ----------------------------------------------------------


def _rows(*specs: tuple[str, int, float]) -> list[ArmResult]:
    """Build fake ArmResult rows: (arm, readiness_score, correctness)."""
    out = []
    for i, (arm, readiness, correctness) in enumerate(specs):
        judge_score = int(round(correctness * 10))
        out.append(
            ArmResult(
                doc_id=f"doc-{i}",
                question=f"q-{i}",
                gold_answer="gold",
                arm=arm,
                answer="ans",
                readiness_score=readiness,
                judge_score=judge_score,
                correctness=correctness,
            )
        )
    return out


def test_aggregate_computes_correlation_for_perfect_linear_relationship() -> None:
    rows = _rows(
        ("aksharamd", 40, 0.4),
        ("aksharamd", 60, 0.6),
        ("aksharamd", 80, 0.8),
        ("aksharamd", 100, 1.0),
    )
    summaries = {s.arm: s for s in aggregate.summarise(rows)}
    ak = summaries["aksharamd"]
    assert ak.n == 4
    assert ak.n_scored == 4
    assert math.isclose(ak.pearson or 0.0, 1.0, abs_tol=1e-9)
    assert math.isclose(ak.spearman or 0.0, 1.0, abs_tol=1e-9)
    assert math.isclose(ak.mean_correctness, 0.7, abs_tol=1e-9)
    assert math.isclose(ak.mean_readiness or 0.0, 70.0, abs_tol=1e-9)


def test_aggregate_computes_zero_correlation_for_flat_readiness() -> None:
    rows = _rows(
        ("mit", 80, 0.4),
        ("mit", 80, 0.6),
        ("mit", 80, 0.8),
    )
    summaries = {s.arm: s for s in aggregate.summarise(rows)}
    # Flat readiness => pearson is None (degenerate).
    assert summaries["mit"].pearson is None
    assert summaries["mit"].spearman is None


def test_aggregate_handles_negative_correlation() -> None:
    rows = _rows(
        ("mit", 40, 1.0),
        ("mit", 60, 0.5),
        ("mit", 80, 0.0),
    )
    summaries = {s.arm: s for s in aggregate.summarise(rows)}
    assert (summaries["mit"].pearson or 0.0) < -0.99
    assert (summaries["mit"].spearman or 0.0) < -0.99


def test_pearson_handles_three_point_exact() -> None:
    """Sanity check: hand-computed Pearson matches _pearson."""
    xs = [1.0, 2.0, 3.0]
    ys = [2.0, 4.0, 8.0]
    r = aggregate._pearson(xs, ys)
    # Hand-computed: mean_x=2, mean_y=14/3; cov = ((-1)*(2-14/3)+0+(1)*(8-14/3))
    # = -1 * (-8/3) + 1 * (10/3) = 8/3 + 10/3 = 18/3 = 6
    # denom_x = sqrt(2), denom_y = sqrt((2-14/3)^2 + (4-14/3)^2 + (8-14/3)^2)
    #         = sqrt(64/9 + 4/9 + 100/9) = sqrt(168/9)
    expected = 6.0 / (math.sqrt(2.0) * math.sqrt(168.0 / 9.0))
    assert math.isclose(r or 0.0, expected, abs_tol=1e-9)


# -- Fixture-mode end-to-end ---------------------------------------------


def test_fixture_client_scopes_lookups_to_current_arm() -> None:
    client = load_fixture_client(LLM_FIXTURE)
    from benchmarks.parsed_vs_raw.llm_client import set_current_arm

    set_current_arm("aksharamd")
    ans = client.answer_from_markdown("What text is in the document?", "irrelevant", model="x")
    assert "Hello" in ans.text
    judge = client.judge("What text is in the document?", "Hello", ans.text, model="x")
    assert judge == 10

    set_current_arm("raw")
    ans2 = client.answer_from_pdf_bytes("What text is in the document?", b"pdf", model="x")
    assert "Hello" in ans2.text


def test_fixture_mode_end_to_end(tmp_path: Path) -> None:
    """Run the driver end-to-end using a fake corpus + fixture LLM client.

    We patch the corpus factory so no ``datasets`` install is required,
    and use ``--fixture-mode`` so no anthropic call is made.
    """

    class _FakeCorpus:
        name = "fake"

        def iter_documents(self, limit: int | None = None) -> Iterator[Any]:
            from benchmarks.parsed_vs_raw.types import DocumentRecord

            yield DocumentRecord(
                doc_id="tiny-1",
                pdf_bytes=TINY_PDF.read_bytes(),
                questions=[
                    Question(
                        question="What text is in the document?",
                        gold_answer="Hello parsed-vs-raw harness test.",
                        answer_type="extractive",
                    ),
                    Question(
                        question="What is the title?",
                        gold_answer="Hello parsed-vs-raw harness test.",
                        answer_type="extractive",
                    ),
                ],
            )

    def fake_get_corpus(name: str) -> Any:
        assert name == "fake"
        return _FakeCorpus()

    output = tmp_path / "out"
    runner = CliRunner()
    with patch("benchmarks.parsed_vs_raw.run.get_corpus", fake_get_corpus):
        result = runner.invoke(
            run_main,
            [
                "--corpus",
                "fake",
                "--limit",
                "1",
                "--questions-per-doc",
                "2",
                "--arms",
                "raw,aksharamd",
                "--answer-model",
                "unused",
                "--judge-model",
                "unused",
                "--output",
                str(output),
                "--fixture-mode",
                str(LLM_FIXTURE),
            ],
        )
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception!r}"
    assert (output / "rows.csv").exists()
    assert (output / "summary.md").exists()
    assert (output / "summary.json").exists()
    rows_text = (output / "rows.csv").read_text(encoding="utf-8")
    assert "raw" in rows_text
    assert "aksharamd" in rows_text
    assert "Hello parsed-vs-raw harness test." in rows_text
    summary_json = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    arms_seen = {s["arm"] for s in summary_json}
    assert arms_seen == {"raw", "aksharamd"}
    # Reset the client so we don't poison subsequent tests.
    set_client(None)


def test_dry_run_writes_extraction_without_llm_call(tmp_path: Path) -> None:
    """--dry-run must not call the client. Even without a client set,
    the driver should succeed because dry-run bypasses the LLM path."""

    class _FakeCorpus:
        name = "fake"

        def iter_documents(self, limit: int | None = None) -> Iterator[Any]:
            from benchmarks.parsed_vs_raw.types import DocumentRecord

            yield DocumentRecord(
                doc_id="tiny-1",
                pdf_bytes=TINY_PDF.read_bytes(),
                questions=[
                    Question(
                        question="What text is in the document?",
                        gold_answer="Hello",
                        answer_type="extractive",
                    )
                ],
            )

    def fake_get_corpus(name: str) -> Any:
        return _FakeCorpus()

    output = tmp_path / "dryout"
    runner = CliRunner()
    # Belt and braces: make anthropic calls fail loudly if any code path
    # tries to hit them despite --dry-run.
    prev_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with patch("benchmarks.parsed_vs_raw.run.get_corpus", fake_get_corpus):
            result = runner.invoke(
                run_main,
                [
                    "--corpus",
                    "fake",
                    "--limit",
                    "1",
                    "--questions-per-doc",
                    "1",
                    "--arms",
                    "aksharamd",
                    "--answer-model",
                    "unused",
                    "--judge-model",
                    "unused",
                    "--output",
                    str(output),
                    "--dry-run",
                ],
            )
    finally:
        if prev_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = prev_key
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception!r}"
    dry_files = list((output / "dry_run" / "aksharamd").glob("*.md"))
    assert dry_files, "dry-run should have written extracted markdown"
    assert (output / "rows.csv").exists()
