"""Tests for Track C aggregator: primary/exploratory separation and stale-record rejection.

Gates required before Phase 2 (per engineering decision 2026-09-20):
  1. em_score is the primary Spearman endpoint.
  2. primary_score appears only in exploratory_analysis.
  3. corpus_gold is excluded from primary analysis (post-freeze arm).
  4. Resume rejects records with incompatible metric_schema_version, prompt_sha256,
     scoring_contract_id, or input_sha256.
  5. _is_phase2_result_compatible is production code, not test-only logic.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from benchmarks.eval_v1.stage2.aggregate_track_c import (
    EXPLORATORY_REFERENCE_BY_CORPUS,
    PRIMARY_REFERENCE,
    _build_pairs,
    _get_score,
    _run_analysis,
    _score_summary,
    aggregate,
)
from benchmarks.eval_v1.stage1.run_track_c import (
    METRIC_SCHEMA_VERSION,
    PROMPT_SHA256,
    SCORING_CONTRACT_ID,
    _is_phase2_result_compatible,
    _is_terminal_llm_error,
    _restorable_phase2_qa_results,
)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

def _make_rec(
    *,
    corpus: str,
    canonical_id: str,
    parser_id: str,
    em_score: float | None,
    primary_score: float | None,
    readiness_score: int | None,
    execution_status: str = "EXECUTED",
    primary_metric: str = "token_f1",
) -> dict[str, Any]:
    return {
        "schema_version": "3",
        "corpus": corpus,
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "execution_status": execution_status,
        "em_score": em_score,
        "primary_score": primary_score,
        "primary_metric": primary_metric,
        "readiness_score": readiness_score,
        "llm_evaluated": True,
    }


# Two QASPER docs, four parsers + corpus_gold
QASPER_RECORDS = [
    # doc1
    _make_rec(corpus="qasper", canonical_id="doc1", parser_id="aksharamd-reference", em_score=0.40, primary_score=0.60, readiness_score=90),
    _make_rec(corpus="qasper", canonical_id="doc1", parser_id="marker",              em_score=0.30, primary_score=0.55, readiness_score=75),
    _make_rec(corpus="qasper", canonical_id="doc1", parser_id="docling",             em_score=0.35, primary_score=0.58, readiness_score=80),
    _make_rec(corpus="qasper", canonical_id="doc1", parser_id="corpus_gold",         em_score=0.70, primary_score=0.80, readiness_score=95),
    # doc2
    _make_rec(corpus="qasper", canonical_id="doc2", parser_id="aksharamd-reference", em_score=0.20, primary_score=0.45, readiness_score=88),
    _make_rec(corpus="qasper", canonical_id="doc2", parser_id="marker",              em_score=0.25, primary_score=0.50, readiness_score=72),
    _make_rec(corpus="qasper", canonical_id="doc2", parser_id="docling",             em_score=0.30, primary_score=0.52, readiness_score=78),
    _make_rec(corpus="qasper", canonical_id="doc2", parser_id="corpus_gold",         em_score=0.60, primary_score=0.75, readiness_score=95),
    # doc3 (for n>=3 in Spearman)
    _make_rec(corpus="qasper", canonical_id="doc3", parser_id="aksharamd-reference", em_score=0.50, primary_score=0.65, readiness_score=85),
    _make_rec(corpus="qasper", canonical_id="doc3", parser_id="marker",              em_score=0.45, primary_score=0.62, readiness_score=70),
    _make_rec(corpus="qasper", canonical_id="doc3", parser_id="docling",             em_score=0.48, primary_score=0.63, readiness_score=73),
    _make_rec(corpus="qasper", canonical_id="doc3", parser_id="corpus_gold",         em_score=0.75, primary_score=0.85, readiness_score=95),
]

TATDQA_RECORDS = [
    _make_rec(corpus="tat_dqa", canonical_id="tdoc1", parser_id="aksharamd-reference", em_score=0.30, primary_score=0.50, readiness_score=92, primary_metric="numeric_em"),
    _make_rec(corpus="tat_dqa", canonical_id="tdoc1", parser_id="marker",              em_score=0.40, primary_score=0.60, readiness_score=75, primary_metric="numeric_em"),
    _make_rec(corpus="tat_dqa", canonical_id="tdoc2", parser_id="aksharamd-reference", em_score=0.25, primary_score=0.45, readiness_score=90, primary_metric="numeric_em"),
    _make_rec(corpus="tat_dqa", canonical_id="tdoc2", parser_id="marker",              em_score=0.35, primary_score=0.55, readiness_score=78, primary_metric="numeric_em"),
    _make_rec(corpus="tat_dqa", canonical_id="tdoc3", parser_id="aksharamd-reference", em_score=0.20, primary_score=0.40, readiness_score=88, primary_metric="numeric_em"),
    _make_rec(corpus="tat_dqa", canonical_id="tdoc3", parser_id="marker",              em_score=0.30, primary_score=0.50, readiness_score=80, primary_metric="numeric_em"),
]

ALL_RECORDS = QASPER_RECORDS + TATDQA_RECORDS


# ---------------------------------------------------------------------------
# Gate 1: _get_score returns the right field.
# ---------------------------------------------------------------------------

class TestGetScore:
    def test_em_score(self):
        rec = _make_rec(corpus="qasper", canonical_id="x", parser_id="p",
                        em_score=0.4, primary_score=0.7, readiness_score=80)
        assert _get_score(rec, "em_score") == pytest.approx(0.4)

    def test_primary_score(self):
        rec = _make_rec(corpus="qasper", canonical_id="x", parser_id="p",
                        em_score=0.4, primary_score=0.7, readiness_score=80)
        assert _get_score(rec, "primary_score") == pytest.approx(0.7)

    def test_missing_field_returns_none(self):
        rec = {"corpus": "qasper", "em_score": 0.4}
        assert _get_score(rec, "primary_score") is None

    def test_null_field_returns_none(self):
        rec = {"corpus": "qasper", "em_score": None}
        assert _get_score(rec, "em_score") is None


# ---------------------------------------------------------------------------
# Gate 2: Primary analysis uses em_score with aksharamd-reference.
# corpus_gold must be excluded (post-freeze arm).
# ---------------------------------------------------------------------------

class TestPrimaryAnalysis:
    def test_build_pairs_uses_em_score(self):
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="em_score",
            reference_parser=PRIMARY_REFERENCE,
            exclude_parsers=frozenset({"corpus_gold"}),
        )
        for p in pairs:
            assert "score" in p
            assert "degradation" in p
            assert p["reference_parser"] == "aksharamd-reference"

    def test_corpus_gold_excluded_from_primary(self):
        # corpus_gold is a post-freeze arm and must not appear in primary analysis pairs.
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="em_score",
            reference_parser=PRIMARY_REFERENCE,
            exclude_parsers=frozenset({"corpus_gold"}),
        )
        parser_ids = {p["parser_id"] for p in pairs}
        assert "corpus_gold" not in parser_ids
        assert "aksharamd-reference" not in parser_ids  # is the reference, not a pair

    def test_corpus_gold_included_when_not_excluded(self):
        # Verify exclude_parsers is doing work: without it, corpus_gold appears.
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="em_score",
            reference_parser=PRIMARY_REFERENCE,
        )
        assert "corpus_gold" in {p["parser_id"] for p in pairs}

    def test_primary_frozen_arms_only(self):
        # Primary pairs contain exactly the four frozen parser arms.
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="em_score",
            reference_parser=PRIMARY_REFERENCE,
            exclude_parsers=frozenset({"corpus_gold"}),
        )
        parser_ids = {p["parser_id"] for p in pairs}
        assert parser_ids == {"marker", "docling"}  # aksharamd-reference is the ref

    def test_degradation_uses_em_not_primary(self):
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="em_score",
            reference_parser=PRIMARY_REFERENCE,
            exclude_parsers=frozenset({"corpus_gold"}),
        )
        doc1_marker = next(p for p in pairs if p["canonical_id"] == "doc1" and p["parser_id"] == "marker")
        # em(aksharamd-reference for doc1) = 0.40, em(marker for doc1) = 0.30
        assert doc1_marker["score_reference"] == pytest.approx(0.40)
        assert doc1_marker["score"] == pytest.approx(0.30)
        assert doc1_marker["degradation"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Gate 3: Exploratory analysis uses primary_score with corpus_gold reference.
# ---------------------------------------------------------------------------

class TestExploratoryAnalysis:
    def test_build_pairs_uses_primary_score(self):
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="primary_score",
            reference_parser=PRIMARY_REFERENCE,
            reference_by_corpus=EXPLORATORY_REFERENCE_BY_CORPUS,
        )
        for p in pairs:
            assert p["reference_parser"] == "corpus_gold"  # QASPER exploratory ref
            assert "score" in p

    def test_corpus_gold_excluded_from_exploratory_pairs(self):
        # corpus_gold IS the reference in exploratory QASPER → excluded from pairs
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="primary_score",
            reference_parser=PRIMARY_REFERENCE,
            reference_by_corpus=EXPLORATORY_REFERENCE_BY_CORPUS,
        )
        parser_ids = {p["parser_id"] for p in pairs}
        assert "corpus_gold" not in parser_ids

    def test_aksharamd_reference_included_in_exploratory_qasper(self):
        # aksharamd-reference is NOT the exploratory reference for QASPER → included
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="primary_score",
            reference_parser=PRIMARY_REFERENCE,
            reference_by_corpus=EXPLORATORY_REFERENCE_BY_CORPUS,
        )
        parser_ids = {p["parser_id"] for p in pairs}
        assert "aksharamd-reference" in parser_ids

    def test_tatdqa_exploratory_reference_is_aksharamd(self):
        pairs = _build_pairs(
            TATDQA_RECORDS,
            corpus="tat_dqa",
            score_field="primary_score",
            reference_parser=PRIMARY_REFERENCE,
            reference_by_corpus=EXPLORATORY_REFERENCE_BY_CORPUS,
        )
        for p in pairs:
            assert p["reference_parser"] == "aksharamd-reference"

    def test_exploratory_degradation_uses_primary_score(self):
        pairs = _build_pairs(
            QASPER_RECORDS,
            corpus="qasper",
            score_field="primary_score",
            reference_parser=PRIMARY_REFERENCE,
            reference_by_corpus=EXPLORATORY_REFERENCE_BY_CORPUS,
        )
        doc1_marker = next(p for p in pairs if p["canonical_id"] == "doc1" and p["parser_id"] == "marker")
        # primary(corpus_gold for doc1) = 0.80, primary(marker for doc1) = 0.55
        assert doc1_marker["score_reference"] == pytest.approx(0.80)
        assert doc1_marker["score"] == pytest.approx(0.55)
        assert doc1_marker["degradation"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Gate 4: aggregate() output structure — primary and exploratory clearly separated.
# ---------------------------------------------------------------------------

class TestAggregateOutputStructure:
    def test_output_has_primary_and_exploratory(self, tmp_path):
        out = tmp_path / "result.json"
        result = aggregate(tmp_path / "run", n_bootstrap=10, output_path=out)
        # When run_dir is empty, both analyses should exist but with n_pairs=0
        assert "primary_analysis" in result
        assert "exploratory_analysis" in result

    def test_primary_analysis_metric_label(self, tmp_path):
        out = tmp_path / "result.json"
        result = aggregate(tmp_path / "run", n_bootstrap=10, output_path=out)
        assert result["primary_analysis"]["metric"] == "em_score"

    def test_exploratory_analysis_metric_label(self, tmp_path):
        out = tmp_path / "result.json"
        result = aggregate(tmp_path / "run", n_bootstrap=10, output_path=out)
        assert result["exploratory_analysis"]["metric"] == "primary_score"

    def test_primary_note_references_freeze_manifest(self, tmp_path):
        out = tmp_path / "result.json"
        result = aggregate(tmp_path / "run", n_bootstrap=10, output_path=out)
        note = result["primary_analysis"]["metric_note"]
        assert "STUDY_FREEZE_MANIFEST" in note

    def test_exploratory_note_labels_as_not_v1_endpoint(self, tmp_path):
        out = tmp_path / "result.json"
        result = aggregate(tmp_path / "run", n_bootstrap=10, output_path=out)
        note = result["exploratory_analysis"]["metric_note"]
        assert "EXPLORATORY" in note
        assert "NOT" in note

    def test_no_old_corpora_key_at_top_level(self, tmp_path):
        # Old schema had a top-level "corpora" dict; new schema nests inside analyses.
        out = tmp_path / "result.json"
        result = aggregate(tmp_path / "run", n_bootstrap=10, output_path=out)
        assert "corpora" not in result


# ---------------------------------------------------------------------------
# Gate 4b: Document-clustered bootstrap produces finite CI on real data.
# ---------------------------------------------------------------------------

class TestClusteredBootstrap:
    """Verify that _bootstrap_ci_clustered correctly resamples at document level."""

    from benchmarks.eval_v1.stage2.aggregate_track_c import _bootstrap_ci_clustered

    def test_finite_ci_with_multi_doc_multi_parser(self):
        """Three docs × two parsers — should produce a finite CI, not (nan, nan)."""
        from benchmarks.eval_v1.stage2.aggregate_track_c import _bootstrap_ci_clustered
        pairs = [
            # doc1: higher readiness → less degradation
            {"canonical_id": "doc1", "parser_id": "marker",  "readiness_score": 90.0, "degradation": 0.05},
            {"canonical_id": "doc1", "parser_id": "docling", "readiness_score": 90.0, "degradation": 0.08},
            # doc2: medium readiness
            {"canonical_id": "doc2", "parser_id": "marker",  "readiness_score": 75.0, "degradation": 0.20},
            {"canonical_id": "doc2", "parser_id": "docling", "readiness_score": 75.0, "degradation": 0.22},
            # doc3: lower readiness → more degradation
            {"canonical_id": "doc3", "parser_id": "marker",  "readiness_score": 60.0, "degradation": 0.40},
            {"canonical_id": "doc3", "parser_id": "docling", "readiness_score": 60.0, "degradation": 0.38},
        ]
        lo, hi = _bootstrap_ci_clustered(pairs, n_bootstrap=200, seed=42)
        assert lo == lo and hi == hi, "CI should be finite (not nan)"
        assert lo < hi, "lower bound must be less than upper bound"

    def test_resamples_documents_not_pairs(self):
        """Two parsers on same doc share readiness_score — CI width reflects
        document-level variance, not artificially inflated pair-level variance."""
        from benchmarks.eval_v1.stage2.aggregate_track_c import _bootstrap_ci_clustered
        # All pairs within a document have identical readiness_score — any variance
        # in the CI comes from document-level resampling, not pair shuffling.
        pairs = [
            {"canonical_id": "d1", "parser_id": "A", "readiness_score": 85.0, "degradation": 0.10},
            {"canonical_id": "d1", "parser_id": "B", "readiness_score": 85.0, "degradation": 0.12},
            {"canonical_id": "d2", "parser_id": "A", "readiness_score": 70.0, "degradation": 0.25},
            {"canonical_id": "d2", "parser_id": "B", "readiness_score": 70.0, "degradation": 0.27},
            {"canonical_id": "d3", "parser_id": "A", "readiness_score": 55.0, "degradation": 0.45},
            {"canonical_id": "d3", "parser_id": "B", "readiness_score": 55.0, "degradation": 0.43},
        ]
        lo, hi = _bootstrap_ci_clustered(pairs, n_bootstrap=500, seed=0)
        assert lo == lo and hi == hi

    def test_fewer_than_3_docs_returns_nan(self):
        """With only 2 unique documents Spearman is undefined — expect (nan, nan)."""
        from benchmarks.eval_v1.stage2.aggregate_track_c import _bootstrap_ci_clustered
        import math
        pairs = [
            {"canonical_id": "d1", "parser_id": "A", "readiness_score": 80.0, "degradation": 0.1},
            {"canonical_id": "d2", "parser_id": "A", "readiness_score": 60.0, "degradation": 0.3},
        ]
        lo, hi = _bootstrap_ci_clustered(pairs, n_bootstrap=100)
        assert math.isnan(lo) and math.isnan(hi)

    def test_constant_readiness_returns_nan(self):
        """If all documents have the same readiness_score Spearman is undefined."""
        from benchmarks.eval_v1.stage2.aggregate_track_c import _bootstrap_ci_clustered
        import math
        pairs = [
            {"canonical_id": f"d{i}", "parser_id": "A", "readiness_score": 80.0, "degradation": float(i) * 0.1}
            for i in range(5)
        ]
        lo, hi = _bootstrap_ci_clustered(pairs, n_bootstrap=100)
        assert math.isnan(lo) and math.isnan(hi)


# ---------------------------------------------------------------------------
# Gate 5: _is_phase2_result_compatible — production function, not test-only logic.
# ---------------------------------------------------------------------------

class TestStaleRecordInvalidation:
    """Tests exercise _is_phase2_result_compatible directly."""

    def _make_result_record(
        self,
        *,
        metric_schema_version: str = METRIC_SCHEMA_VERSION,
        prompt_sha256: str = PROMPT_SHA256,
        scoring_contract_id: str = SCORING_CONTRACT_ID,
        llm_evaluated: bool = True,
        input_sha256: str | None = "abc123def456",
        n_llm_errors: int = 0,
        qa_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "schema_version": "3",
            "metric_schema_version": metric_schema_version,
            "prompt_sha256": prompt_sha256,
            "scoring_contract_id": scoring_contract_id,
            "llm_evaluated": llm_evaluated,
            "n_llm_errors": n_llm_errors,
            "qa_results": qa_results or [],
            "em_score": 0.5,
            "primary_score": 0.6,
            "readiness_score": 85,
        }
        if input_sha256 is not None:
            rec["input_sha256"] = input_sha256
        return rec

    def test_current_version_is_compatible(self):
        rec = self._make_result_record()
        assert _is_phase2_result_compatible(rec, "abc123def456")

    def test_old_metric_schema_version_rejected(self):
        rec = self._make_result_record(metric_schema_version="2")
        assert not _is_phase2_result_compatible(rec, "abc123def456")

    def test_wrong_prompt_sha_rejected(self):
        rec = self._make_result_record(prompt_sha256="deadbeef" * 8)
        assert not _is_phase2_result_compatible(rec, "abc123def456")

    def test_wrong_scoring_contract_rejected(self):
        rec = self._make_result_record(scoring_contract_id="old_contract_v0")
        assert not _is_phase2_result_compatible(rec, "abc123def456")

    def test_llm_evaluated_false_rejected(self):
        # llm_evaluated=False means the record was never completed — always re-evaluate.
        rec = self._make_result_record(llm_evaluated=False)
        assert not _is_phase2_result_compatible(rec, "abc123def456")

    def test_record_with_llm_errors_rejected(self):
        rec = self._make_result_record(n_llm_errors=1)
        assert not _is_phase2_result_compatible(rec, "abc123def456")

    def test_record_with_error_row_rejected_even_if_count_is_stale(self):
        rec = self._make_result_record(qa_results=[{
            "question_id": 0,
            "status": "llm_error",
        }])
        assert not _is_phase2_result_compatible(rec, "abc123def456")

    def test_changed_input_hash_rejected(self):
        # Markdown file changed since last evaluation — must re-evaluate.
        rec = self._make_result_record(input_sha256="original_hash")
        assert not _is_phase2_result_compatible(rec, "different_hash")

    def test_matching_input_hash_compatible(self):
        rec = self._make_result_record(input_sha256="abc123")
        assert _is_phase2_result_compatible(rec, "abc123")

    def test_no_stored_hash_rejected(self):
        # Fail closed: a record without input_sha256 cannot be verified — must re-evaluate.
        rec = self._make_result_record(input_sha256=None)
        assert not _is_phase2_result_compatible(rec, "any_hash")

    def test_stored_hash_present_current_hash_none_rejected(self):
        # Markdown file absent (current_input_sha256=None) but record has a hash — reject.
        rec = self._make_result_record(input_sha256="some_hash")
        assert not _is_phase2_result_compatible(rec, None)

    def test_partial_restore_reuses_answers_but_retries_errors(self):
        rec = self._make_result_record(
            llm_evaluated=True,
            n_llm_errors=1,
            qa_results=[
                {"question_id": 0, "status": "answered", "prediction": "saved"},
                {"question_id": 1, "status": "llm_error", "prediction": None},
                {"question_id": 2, "status": "no_gold", "prediction": None},
            ],
        )
        restored = _restorable_phase2_qa_results(rec, "abc123def456")
        assert set(restored) == {0, 2}
        assert restored[0]["prediction"] == "saved"

    def test_partial_restore_rejects_wrong_input_hash(self):
        rec = self._make_result_record(qa_results=[{
            "question_id": 0,
            "status": "answered",
        }])
        assert _restorable_phase2_qa_results(rec, "different") == {}

    def test_low_credit_error_is_terminal(self):
        exc = RuntimeError("Your credit balance is too low to access the Anthropic API")
        assert _is_terminal_llm_error(exc)

    def test_transient_network_error_is_not_terminal(self):
        assert not _is_terminal_llm_error(RuntimeError("connection reset by peer"))

    def test_all_fields_present_in_written_record(self, tmp_path):
        """_write_track_c_result must include all provenance fields."""
        from benchmarks.eval_v1.stage1.run_track_c import _write_track_c_result
        out = tmp_path / "result.json"
        _write_track_c_result(
            out,
            corpus="qasper",
            canonical_id="doc1",
            parser_id="marker",
            execution_status="EXECUTED",
            n_qa_pairs=10,
            n_answered=8,
            n_llm_errors=0,
            em_score=0.4,
            primary_score=0.6,
            primary_metric="token_f1",
            readiness_score=85,
            warning_codes=[],
            llm_evaluated=True,
            input_sha256="abc123",
        )
        rec = json.loads(out.read_text())
        assert rec["metric_schema_version"] == METRIC_SCHEMA_VERSION
        assert rec["prompt_sha256"] == PROMPT_SHA256
        assert rec["scoring_contract_id"] == SCORING_CONTRACT_ID
        assert rec["input_sha256"] == "abc123"
        assert rec["n_llm_errors"] == 0

    def test_schema_version_is_3(self, tmp_path):
        from benchmarks.eval_v1.stage1.run_track_c import _write_track_c_result
        out = tmp_path / "result.json"
        _write_track_c_result(
            out,
            corpus="qasper",
            canonical_id="doc1",
            parser_id="marker",
            execution_status="EXECUTED",
            n_qa_pairs=10,
            n_answered=8,
            em_score=0.4,
            readiness_score=85,
            warning_codes=[],
            llm_evaluated=True,
        )
        rec = json.loads(out.read_text())
        assert rec["schema_version"] == "3"

    def test_qa_results_stored_in_written_record(self, tmp_path):
        """Per-question results must be persisted for audit and correction without re-pay."""
        from benchmarks.eval_v1.stage1.run_track_c import _write_track_c_result
        out = tmp_path / "result.json"
        qa_results = [
            {
                "question_id": 0,
                "question": "What is the main finding?",
                "answer_type": "abstractive",
                "scale": "",
                "gold_annotations": [{"gold_answer": "yes", "answer_type": "abstractive"}],
                "status": "answered",
                "prediction": "yes",
                "prediction_sha256": "abc",
                "em_score": 1.0,
                "primary_score": 1.0,
            },
            {
                "question_id": 1,
                "question": "How many samples?",
                "answer_type": "extractive",
                "scale": "",
                "gold_annotations": [{"gold_answer": "50", "answer_type": "extractive"}],
                "status": "llm_error",
                "error": "timeout",
                "prediction": None,
                "prediction_sha256": None,
                "em_score": None,
                "primary_score": None,
            },
        ]
        _write_track_c_result(
            out,
            corpus="qasper",
            canonical_id="doc1",
            parser_id="marker",
            execution_status="EXECUTED",
            n_qa_pairs=2,
            n_answered=1,
            n_llm_errors=1,
            em_score=1.0,
            readiness_score=None,
            warning_codes=[],
            llm_evaluated=True,
            qa_results=qa_results,
        )
        rec = json.loads(out.read_text())
        assert "qa_results" in rec
        assert len(rec["qa_results"]) == 2
        assert rec["qa_results"][0]["status"] == "answered"
        assert rec["qa_results"][0]["prediction"] == "yes"
        assert rec["qa_results"][1]["status"] == "llm_error"
        assert rec["qa_results"][1]["prediction"] is None
