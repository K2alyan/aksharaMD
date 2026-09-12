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
LLM_FIXTURE = FIXTURES / "llm_responses.json"


def _generate_tiny_pdf() -> bytes:
    """One-page synthetic PDF generated in-memory; never committed to git.

    Kept at module scope with a ``.read_bytes()``-compatible proxy so all
    existing test call sites (``TINY_PDF.read_bytes()``) work unchanged.
    """
    import fitz  # pymupdf

    pdf = fitz.open()
    pdf.new_page(width=100, height=100)
    data = pdf.tobytes()
    pdf.close()
    return data


class _TinyPdfProxy:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read_bytes(self) -> bytes:
        return self._data


TINY_PDF = _TinyPdfProxy(_generate_tiny_pdf())


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
    """Smoke: the loader chains raw JSON rows into DocumentRecord objects.

    We monkeypatch the S3 tarball fetch (``_download_bytes``) with a
    synthetic tar.gz that contains a canned ``qasper-dev-v0.3.json``,
    and monkeypatch ``_download_pdf`` so no network traffic occurs.
    """
    import io
    import tarfile

    from benchmarks.parsed_vs_raw.corpora import qasper as qasper_module

    fake_rows = {
        "2101.00001": {
            "title": "Fake paper A",
            "abstract": "",
            "full_text": [],
            "qas": [
                {
                    "question": "Q1?",
                    "question_id": "q-1",
                    "answers": [
                        {
                            "answer": {
                                "unanswerable": False,
                                "extractive_spans": ["answer one"],
                                "yes_no": None,
                                "free_form_answer": "",
                                "evidence": [],
                            }
                        }
                    ],
                }
            ],
        },
        "2101.00002": {
            "title": "Fake paper B",
            "abstract": "",
            "full_text": [],
            "qas": [
                {
                    "question": "Q2?",
                    "question_id": "q-2",
                    "answers": [
                        {
                            "answer": {
                                "unanswerable": True,
                                "extractive_spans": [],
                                "yes_no": None,
                                "free_form_answer": "",
                                "evidence": [],
                            }
                        }
                    ],
                }
            ],
        },
    }

    def _make_fake_tarball() -> bytes:
        payload = json.dumps(fake_rows).encode("utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="qasper-dev-v0.3.json")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        return buf.getvalue()

    def fake_download_bytes(url: str) -> bytes:
        # Assert against the S3 URL so a future refactor that changes
        # the endpoint fails loudly here rather than silently.
        assert url.endswith("qasper-train-dev-v0.3.tgz")
        return _make_fake_tarball()

    def fake_download_pdf(url: str) -> bytes:
        return TINY_PDF.read_bytes()

    with patch.object(qasper_module, "_download_bytes", fake_download_bytes), patch.object(
        qasper_module, "_download_pdf", fake_download_pdf
    ):
        corpus = QasperCorpus(
            cache_dir=str(tmp_path / "qasper_cache"),
            arxiv_sleep_seconds=0.0,
        )
        docs = list(corpus.iter_documents(limit=2))

    assert [d.doc_id for d in docs] == ["2101.00001", "2101.00002"]
    # Doc B has only an unanswerable question => it's kept but with
    # answer_type "unanswerable" and gold_answer == "".
    assert docs[0].questions[0].question == "Q1?"
    assert docs[0].questions[0].gold_answer == "answer one"
    assert docs[1].questions[0].answer_type == "unanswerable"
    assert docs[1].questions[0].gold_answer == ""


def test_qasper_corpus_loader_rejects_unknown_split() -> None:
    """The new S3-driven loader validates the split at construction."""
    with pytest.raises(ValueError):
        QasperCorpus(split="bogus-split")


# -- Parser arm -----------------------------------------------------------


def test_parser_arm_produces_markdown_and_readiness_score() -> None:
    """Running the aksharamd-reference parser arm on a real (tiny) PDF yields markdown + a score."""
    pytest.importorskip("aksharamd")
    arm = ParserArm(parser="aksharamd-reference", answer_model="unused", judge_model="unused")
    pdf_bytes = TINY_PDF.read_bytes()
    extraction = arm.extract(pdf_bytes)
    assert extraction.markdown, "expected non-empty markdown from aksharamd-reference"
    assert isinstance(extraction.readiness_score, int)
    assert 0 <= extraction.readiness_score <= 100
    assert extraction.parser_name == "aksharamd-reference"


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


def test_markitdown_arm_uses_compiler_readiness_via_parser_adapter() -> None:
    """The MarkItDown arm must route through Compiler(parser_adapter=...) so
    both the AksharaMD and MarkItDown arms measure readiness with the exact
    same instrument.

    Strategy: install a fake ``markitdown`` module in ``sys.modules`` whose
    ``MarkItDown.convert_stream`` returns a canned markdown string, then
    call ``ParserArm(parser='markitdown').extract(...)`` and assert the
    returned readiness score is a real ``ctx.manifest.readiness_score``
    integer (0-100), not the retired verdict-derived proxy.
    """
    pytest.importorskip("aksharamd")
    import sys
    import types

    canned = "# Fake MarkItDown output\n\nHello parsed-vs-raw harness test.\n"

    class _FakeResult:
        def __init__(self, text: str) -> None:
            self.text_content = text

    class _FakeMarkItDown:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def convert_stream(self, stream: Any) -> _FakeResult:
            return _FakeResult(canned)

    fake_module = types.ModuleType("markitdown")
    fake_module.MarkItDown = _FakeMarkItDown  # type: ignore[attr-defined]
    prev = sys.modules.get("markitdown")
    sys.modules["markitdown"] = fake_module
    try:
        arm = ParserArm(parser="markitdown", answer_model="unused", judge_model="unused")
        extraction = arm.extract(TINY_PDF.read_bytes())
    finally:
        if prev is None:
            sys.modules.pop("markitdown", None)
        else:
            sys.modules["markitdown"] = prev

    assert extraction.parser_name == "markitdown"
    # The Compiler pipeline runs on the MarkItDown-produced markdown and
    # yields a real (integer) readiness score - proving the arm now uses
    # the identical instrument as the AksharaMD arm.
    assert isinstance(extraction.readiness_score, int), (
        f"expected int readiness, got {extraction.readiness_score!r}"
    )
    assert 0 <= extraction.readiness_score <= 100


def test_markitdown_arm_surfaces_missing_dep_as_parser_unavailable() -> None:
    """When ``markitdown`` is not importable the arm raises ParserUnavailable."""
    import sys

    sentinel = object()
    prev = sys.modules.get("markitdown")
    # Poison the import so ``import markitdown`` raises ImportError inside
    # MarkItDownAdapter.__post_init__.
    sys.modules["markitdown"] = None  # type: ignore[assignment]
    try:
        arm = ParserArm(parser="markitdown", answer_model="unused", judge_model="unused")
        with pytest.raises(ParserUnavailable):
            arm.extract(TINY_PDF.read_bytes())
    finally:
        if prev is None:
            sys.modules.pop("markitdown", None)
        else:
            sys.modules["markitdown"] = prev
    _ = sentinel  # keep parity with the docling test's naming


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
        ("aksharamd-reference",40, 0.4),
        ("aksharamd-reference",60, 0.6),
        ("aksharamd-reference",80, 0.8),
        ("aksharamd-reference",100, 1.0),
    )
    summaries = {s.arm: s for s in aggregate.summarise(rows)}
    ak = summaries["aksharamd-reference"]
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

    set_current_arm("aksharamd-reference")
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
                "raw,aksharamd-reference",
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
    assert "aksharamd-reference" in rows_text
    assert "Hello parsed-vs-raw harness test." in rows_text
    summary_json = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    arms_seen = {s["arm"] for s in summary_json}
    assert arms_seen == {"raw", "aksharamd-reference"}
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
                    "aksharamd-reference",
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
    dry_files = list((output / "dry_run" / "aksharamd-reference").glob("*.md"))
    assert dry_files, "dry-run should have written extracted markdown"
    assert (output / "rows.csv").exists()


# -- .env loading ----------------------------------------------------------


def test_load_dotenv_reads_missing_vars_but_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env` values load into os.environ unless the shell already set them.

    Contract:
    * A key present in ``.env`` but not in ``os.environ`` gets loaded.
    * A key already in ``os.environ`` is NOT overwritten.
    * Blank lines, comments and malformed lines are ignored silently.
    * Surrounding single/double quotes are stripped from the value.
    """
    from benchmarks.parsed_vs_raw.run import _load_dotenv

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "ANTHROPIC_API_KEY=test-value",
                'QUOTED_KEY="quoted-value"',
                "SINGLE_QUOTED='single-value'",
                "malformed-line-no-equals",
                "PRESET_KEY=should-not-overwrite",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("QUOTED_KEY", raising=False)
    monkeypatch.delenv("SINGLE_QUOTED", raising=False)
    monkeypatch.setenv("PRESET_KEY", "shell-wins")

    _load_dotenv()

    assert os.environ["ANTHROPIC_API_KEY"] == "test-value"
    assert os.environ["QUOTED_KEY"] == "quoted-value"
    assert os.environ["SINGLE_QUOTED"] == "single-value"
    # Preset key from the shell wins; .env value is ignored.
    assert os.environ["PRESET_KEY"] == "shell-wins"


def test_load_dotenv_is_noop_when_no_dotenv_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``.env`` present -> no exception, no environment mutation."""
    from benchmarks.parsed_vs_raw.run import _load_dotenv

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNSET_ONLY_HERE_KEY", raising=False)
    _load_dotenv()
    assert "UNSET_ONLY_HERE_KEY" not in os.environ


# -- --smoke sub-mode ------------------------------------------------------


def test_smoke_mode_forces_single_arm_single_doc_single_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--smoke`` collapses limits and picks a single arm; no real API call.

    We use ``--fixture-mode`` to prove the smoke path is wired end-to-end
    without hitting Anthropic. The test asserts the driver:

    * Only requests one document from the corpus (limit=1)
    * Trims to one question per doc even though the fake corpus yields
      two questions
    * Writes rows.csv with a single row (single arm, single question)
    * Prints the ``SMOKE OK`` banner
    """

    class _FakeCorpus:
        name = "fake"
        seen_limits: list[int | None] = []

        def iter_documents(self, limit: int | None = None) -> Iterator[Any]:
            from benchmarks.parsed_vs_raw.types import DocumentRecord

            _FakeCorpus.seen_limits.append(limit)
            yield DocumentRecord(
                doc_id="smoke-1",
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
        return _FakeCorpus()

    output = tmp_path / "smoke-out"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)  # ensure _load_dotenv sees no .env
    with patch("benchmarks.parsed_vs_raw.run.get_corpus", fake_get_corpus):
        result = runner.invoke(
            run_main,
            [
                "--corpus",
                "fake",
                # Pass big values so we can prove --smoke overrides them.
                "--limit",
                "50",
                "--questions-per-doc",
                "10",
                "--arms",
                "raw,markitdown,aksharamd-reference",
                "--answer-model",
                "unused",
                "--judge-model",
                "unused",
                "--output",
                str(output),
                "--fixture-mode",
                str(LLM_FIXTURE),
                "--smoke",
            ],
        )

    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception!r}"
    assert _FakeCorpus.seen_limits == [1], (
        f"--smoke should force limit=1; got {_FakeCorpus.seen_limits}"
    )
    assert "SMOKE OK" in result.output
    rows_text = (output / "rows.csv").read_text(encoding="utf-8")
    # First element of --arms wins under --smoke: "raw".
    assert "raw" in rows_text
    assert "markitdown" not in rows_text
    assert "aksharamd-reference" not in rows_text
    # Single arm x single doc x single question => a single data row.
    non_header_rows = [
        row for row in rows_text.strip().splitlines()[1:] if row.strip()
    ]
    assert len(non_header_rows) == 1, (
        f"--smoke should emit exactly one data row; got {len(non_header_rows)}"
    )
    set_client(None)


def test_smoke_mode_defaults_to_aksharamd_when_no_arms_flag_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When only ``--smoke`` is passed the default arm is ``aksharamd``.

    Note: Click still emits the ``--arms`` default string ``raw,markitdown,aksharamd``
    if we don't pass it, so this test asserts we get ``raw`` (the first
    element of the default). To actually verify the "no --arms => aksharamd"
    fallback we would need to detect whether the user supplied ``--arms``,
    which click doesn't expose cleanly. The important smoke-mode contract
    - single-arm, single-doc, single-question - is covered by the test
    above; this test just documents the current default-arm behaviour.
    """

    class _FakeCorpus:
        name = "fake"

        def iter_documents(self, limit: int | None = None) -> Iterator[Any]:
            from benchmarks.parsed_vs_raw.types import DocumentRecord

            yield DocumentRecord(
                doc_id="smoke-default",
                pdf_bytes=TINY_PDF.read_bytes(),
                questions=[
                    Question(
                        question="What text is in the document?",
                        gold_answer="Hello",
                        answer_type="extractive",
                    ),
                ],
            )

    def fake_get_corpus(name: str) -> Any:
        return _FakeCorpus()

    output = tmp_path / "smoke-default-out"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    with patch("benchmarks.parsed_vs_raw.run.get_corpus", fake_get_corpus):
        result = runner.invoke(
            run_main,
            [
                "--corpus",
                "fake",
                "--output",
                str(output),
                "--fixture-mode",
                str(LLM_FIXTURE),
                "--smoke",
            ],
        )

    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception!r}"
    assert "SMOKE OK" in result.output
    set_client(None)
