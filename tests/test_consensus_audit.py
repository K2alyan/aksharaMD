"""Tests for the consensus-audit module and `aksharamd audit` CLI (P5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from click.testing import CliRunner

from aksharamd.cli import main
from aksharamd.consensus_audit import (
    AuditResult,
    ParserMetric,
    _jaccard,
    _tokenize,
    format_json,
    format_report,
    run_audit,
)

# ── Helpers ────────────────────────────────────────────────────────────────

def _make_pdf(tmp_path: Path, name: str, sentences: list[str]) -> Path:
    """Create a small in-memory PDF and write to tmp_path/name."""
    doc = fitz.open()
    for sentence in sentences:
        page = doc.new_page()
        rect = fitz.Rect(72, 72, 540, 750)
        page.insert_textbox(rect, sentence, fontsize=10)
    pdf_path = tmp_path / name
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def _write_md(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── Tokenizer / helpers ────────────────────────────────────────────────────

def test_tokenize_extracts_alphanumeric_tokens():
    assert _tokenize("Hello, world! 123 don't stop.") == [
        "hello", "world", "123", "don't", "stop"
    ]


def test_tokenize_lowercases():
    assert _tokenize("Revenue REPORTED") == ["revenue", "reported"]


def test_tokenize_empty_string():
    assert _tokenize("") == []


def test_jaccard_identity():
    s = {"a", "b", "c"}
    assert _jaccard(s, s) == 1.0


def test_jaccard_disjoint():
    assert _jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_empty_sets_treated_as_identical():
    assert _jaccard(set(), set()) == 1.0


def test_jaccard_partial_overlap():
    # |{a,b} ∩ {b,c}| = 1, |{a,b} ∪ {b,c}| = 3 → 1/3
    assert abs(_jaccard({"a", "b"}, {"b", "c"}) - (1 / 3)) < 1e-9


# ── run_audit smoke path ───────────────────────────────────────────────────

def test_run_audit_single_parser_full_fidelity(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["alpha beta gamma delta epsilon"])
    md_path = _write_md(tmp_path, "ref.md", "alpha beta gamma delta epsilon")
    result = run_audit(pdf_path, {"reference": md_path})
    assert result.pdf_page_count == 1
    assert result.pdf_word_count == 5
    assert len(result.parsers) == 1
    ref = result.parsers[0]
    assert ref.parser_id == "reference"
    assert ref.markdown_word_count == 5
    assert ref.fidelity_ratio == 1.0
    assert ref.pdf_overlap_ratio == 1.0
    # Only one parser present — every word is unique_vs_others.
    assert ref.unique_vs_others_count == 5


def test_run_audit_two_parsers_partial_agreement(tmp_path):
    pdf_path = _make_pdf(
        tmp_path,
        "src.pdf",
        ["alpha beta gamma delta epsilon zeta eta theta iota kappa"],
    )
    # marker retains everything; docling drops half.
    marker = _write_md(tmp_path, "marker.md", "alpha beta gamma delta epsilon zeta eta theta iota kappa")
    docling = _write_md(tmp_path, "docling.md", "alpha beta gamma delta epsilon")
    result = run_audit(pdf_path, {"marker": marker, "docling": docling})
    assert len(result.parsers) == 2
    metrics = {m.parser_id: m for m in result.parsers}
    assert metrics["marker"].fidelity_ratio == 1.0
    assert 0.4 <= metrics["docling"].fidelity_ratio <= 0.6
    # Jaccard: marker vs docling = |{5 shared}| / |{10 union}| = 0.5
    matrix = {
        (result.parsers[i].parser_id, result.parsers[j].parser_id): result.jaccard_matrix[i][j]
        for i in range(2)
        for j in range(2)
    }
    assert matrix[("marker", "marker")] == 1.0
    assert matrix[("docling", "docling")] == 1.0
    assert abs(matrix[("marker", "docling")] - 0.5) < 1e-6


def test_run_audit_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_audit(tmp_path / "does_not_exist.pdf", {"ref": tmp_path / "x.md"})


def test_run_audit_missing_parsed_file_raises(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["alpha beta gamma"])
    with pytest.raises(FileNotFoundError):
        run_audit(pdf_path, {"missing": tmp_path / "does_not_exist.md"})


# ── unique_vs_others ───────────────────────────────────────────────────────

def test_unique_vs_others_counts_only_words_no_other_parser_has(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["shared alone"])
    a = _write_md(tmp_path, "a.md", "shared alone-word")
    b = _write_md(tmp_path, "b.md", "shared")
    result = run_audit(pdf_path, {"a": a, "b": b})
    metrics = {m.parser_id: m for m in result.parsers}
    # a has words {shared, alone, word}; b has {shared}. a-only = {alone, word}.
    assert metrics["a"].unique_vs_others_count == 2
    assert metrics["b"].unique_vs_others_count == 0


# ── Report formatting ──────────────────────────────────────────────────────

def test_format_report_contains_parser_ids_and_metrics(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["alpha beta gamma"])
    md = _write_md(tmp_path, "ref.md", "alpha")
    result = run_audit(pdf_path, {"myparser": md})
    report = format_report(result)
    assert "myparser" in report
    assert "words" in report
    assert "fidelity" in report
    assert "Jaccard" in report


def test_format_json_parses_and_has_required_keys(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["alpha beta gamma"])
    md = _write_md(tmp_path, "ref.md", "alpha")
    result = run_audit(pdf_path, {"myparser": md})
    payload = json.loads(format_json(result))
    assert payload["pdf_word_count"] == 3
    assert payload["parsers"][0]["parser_id"] == "myparser"
    assert "jaccard_matrix" in payload


# ── AuditResult serialization ──────────────────────────────────────────────

def test_audit_result_to_dict_round_trip():
    result = AuditResult(
        source_path="/tmp/x.pdf",
        pdf_page_count=1,
        pdf_word_count=10,
        parsers=(
            ParserMetric(
                parser_id="a",
                markdown_word_count=8,
                markdown_unique_word_count=7,
                fidelity_ratio=0.8,
                pdf_overlap_ratio=0.7,
                unique_vs_others_count=1,
            ),
        ),
        jaccard_matrix=((1.0,),),
    )
    d = result.to_dict()
    assert d["pdf_word_count"] == 10
    assert d["parsers"][0]["fidelity_ratio"] == 0.8


# ── CLI end-to-end ─────────────────────────────────────────────────────────

def test_cli_audit_happy_path(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["hello world audit test content"])
    md_path = _write_md(tmp_path, "ref.md", "hello world audit test content")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["audit", str(pdf_path), "--parsed", f"reference={md_path}"],
    )
    assert result.exit_code == 0, result.output
    assert "reference" in result.output
    assert "words" in result.output


def test_cli_audit_json_flag(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["hello world"])
    md_path = _write_md(tmp_path, "ref.md", "hello world")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["audit", str(pdf_path), "--parsed", f"reference={md_path}", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "pdf_word_count" in payload


def test_cli_audit_rejects_malformed_parsed(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["hello world"])
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["audit", str(pdf_path), "--parsed", "no_equals_sign"],
    )
    assert result.exit_code == 2


def test_cli_audit_rejects_duplicate_parser_id(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["hello world"])
    md1 = _write_md(tmp_path, "a.md", "hello")
    md2 = _write_md(tmp_path, "b.md", "world")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            str(pdf_path),
            "--parsed", f"same={md1}",
            "--parsed", f"same={md2}",
        ],
    )
    assert result.exit_code == 2


def test_cli_audit_requires_at_least_one_parsed(tmp_path):
    pdf_path = _make_pdf(tmp_path, "src.pdf", ["hello world"])
    runner = CliRunner()
    result = runner.invoke(main, ["audit", str(pdf_path)])
    # Click's required=True on --parsed multiple option surfaces as exit 2.
    assert result.exit_code == 2
