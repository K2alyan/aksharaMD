"""Tests for the MGAM evaluator — algorithm correctness and corpus smoke test."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from benchmarks.mgam_eval.mgam import (
    extract_reference_blocks,
    mgam_score,
    normalize,
)

# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_strips_bold():
    assert normalize("**hello** world") == "hello world"


def test_normalize_strips_italic():
    assert normalize("*hello* world") == "hello world"


def test_normalize_strips_heading():
    assert normalize("## Revenue Summary") == "Revenue Summary"


def test_normalize_strips_table_pipes():
    assert normalize("| A | B |") == "A B"


def test_normalize_strips_underline():
    assert normalize("<u>term</u> defined") == "term defined"


def test_normalize_collapses_whitespace():
    assert normalize("foo  \n  bar") == "foo bar"


# ── extract_reference_blocks ──────────────────────────────────────────────────

def test_extract_filters_short_blocks():
    raw = "Hi\n\nThis is a real paragraph with sufficient length to matter."
    blocks = extract_reference_blocks(raw)
    assert all(len(b) >= 20 for b in blocks)
    assert any("real paragraph" in b for b in blocks)


def test_extract_filters_numeric_noise():
    raw = "1234567890\n\nProper paragraph with enough content to pass the filter check."
    blocks = extract_reference_blocks(raw)
    assert not any(b.strip() == "1234567890" for b in blocks)


def test_extract_splits_on_double_newline():
    raw = "First paragraph with good content here.\n\nSecond paragraph with enough text too."
    blocks = extract_reference_blocks(raw)
    assert len(blocks) == 2


# ── mgam_score — edge cases ───────────────────────────────────────────────────

def test_mgam_empty_ref_is_perfect():
    result = mgam_score([], ["some prediction text here"])
    assert result.recall == 1.0


def test_mgam_empty_pred_is_zero():
    result = mgam_score(["reference block with text"], [])
    assert result.recall == 0.0
    assert result.f1 == 0.0


def test_mgam_identical_blocks_perfect_score():
    blocks = [
        "Revenue increased twelve percent in the third quarter of fiscal year.",
        "Operating expenses were well controlled at two billion dollars.",
    ]
    result = mgam_score(blocks, blocks)
    assert result.recall > 0.99
    assert result.f1 > 0.99


def test_mgam_split_block_recovered_by_merging():
    """Content correct but split across two prediction blocks — MGAM should
    score it near-perfectly by merging the two pred blocks."""
    ref = ["Revenue increased twelve percent and operating margins expanded by three points."]
    pred = [
        "Revenue increased twelve percent",
        "and operating margins expanded by three points.",
    ]
    result = mgam_score(ref, pred, max_merge=4)
    assert result.recall > 0.90, f"Expected >90% recall, got {result.recall:.2%}"


def test_mgam_unrelated_pred_low_recall():
    ref = ["Revenue increased twelve percent in the third quarter of fiscal year."]
    pred = ["Completely unrelated content about weather and cooking recipes today."]
    result = mgam_score(ref, pred)
    assert result.recall < 0.4


def test_mgam_partial_match_scores_between():
    """Partial content overlap should score between 0 and 1."""
    ref = ["Revenue increased twelve percent in the third quarter of fiscal year twenty four."]
    pred = ["Revenue increased twelve percent in the third quarter."]
    result = mgam_score(ref, pred)
    assert 0.4 < result.recall < 0.99


def test_mgam_precision_penalises_hallucination():
    """If prediction has blocks not in reference, precision should be lower than recall."""
    ref = ["Revenue increased twelve percent this year."]
    pred = [
        "Revenue increased twelve percent this year.",
        "This fabricated sentence has nothing to do with the document at all.",
        "Another hallucinated paragraph covering unrelated material entirely.",
    ]
    result = mgam_score(ref, pred)
    assert result.precision < result.recall


def test_mgam_returns_per_block_scores():
    ref = ["First real block.", "Second real block with more content."]
    pred = ["First real block.", "Completely unrelated text here."]
    result = mgam_score(ref, pred)
    assert len(result.per_ref_scores) == 2
    assert result.per_ref_scores[0] > result.per_ref_scores[1]


# ── Corpus smoke test ─────────────────────────────────────────────────────────

def test_corpus_builds_and_evaluates():
    """Build the synthetic corpus, run AksharaMD on it, verify recall >= 50%."""
    pytest.importorskip("fitz", reason="PyMuPDF required")
    from benchmarks.mgam_eval.evaluator import evaluate_corpus
    from benchmarks.mgam_eval.make_corpus import build_corpus

    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp)
        build_corpus(corpus_dir)

        pdfs = list(corpus_dir.glob("*.pdf"))
        assert len(pdfs) == 5, f"Expected 5 corpus PDFs, got {len(pdfs)}"

        corpus = evaluate_corpus(corpus_dir)

    assert corpus.n_total == 5
    # At least 3 documents should parse without errors
    assert corpus.n_errors <= 2, f"Too many errors: {corpus.n_errors}/5"
    # Mean recall should be at least 50% on clean synthetic documents
    assert corpus.mean_recall >= 0.50, (
        f"Mean recall {corpus.mean_recall:.2%} below threshold on synthetic corpus"
    )
