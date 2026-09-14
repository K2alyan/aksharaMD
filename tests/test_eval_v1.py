"""Unit tests for benchmarks/eval_v1 infrastructure.

Scope: module-level invariants for the plumbing shipped under
Authorization A / A.1. Does not exercise the full pipeline (see
`benchmarks/eval_v1/smoke_run*.py` for rerun scripts and archived
results in `benchmarks/results/authorization-*-2026-09-14/`).

The A.1 completion criterion — 156/156 stage cells with zero DEFECT —
is validated by rerunning `smoke_run_v2.py`, not by these tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------- stages.py ----------------------------------------


def test_stage_status_executed_forbids_reason():
    from benchmarks.eval_v1.stages import StageStatus, StageResult

    with pytest.raises(ValueError, match="EXECUTED must not carry a reason"):
        StageResult(stage="s", status=StageStatus.EXECUTED, reason="nope")


def test_stage_status_non_executed_requires_reason():
    from benchmarks.eval_v1.stages import StageStatus, StageResult

    for status in (
        StageStatus.NOT_APPLICABLE,
        StageStatus.DEFECT,
        StageStatus.REQUIRES_REVIEW,
        StageStatus.INFRASTRUCTURE_READY_NOT_EXECUTED,
    ):
        with pytest.raises(ValueError, match="requires a non-empty reason"):
            StageResult(stage="s", status=status, reason="")


def test_stage_result_to_dict_round_trip():
    from benchmarks.eval_v1.stages import executed, not_applicable

    e = executed("s", payload={"x": 1})
    assert e.to_dict()["status"] == "EXECUTED"
    n = not_applicable("s", reason="because")
    assert n.to_dict()["status"] == "NOT_APPLICABLE"
    assert n.to_dict()["reason"] == "because"


# ---------------- normalization.py ---------------------------------


def test_normalizer_applies_expected_rules():
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "---\ntitle: X\n---\nHello­world pro-\ncessing\r\n\n\n\nend  "
    out = UnicodeWhitespaceNormalizer().normalize(md)
    # Frontmatter stripped
    assert "title:" not in out.text
    # Soft hyphen removed
    assert "Hello­world" not in out.text
    # Dehyphenated
    assert "processing" in out.text
    # CRLF collapsed
    assert "\r" not in out.text
    # Blank runs collapsed
    assert "\n\n\n" not in out.text
    # Trailing spaces removed
    assert "end  " not in out.text
    # Version recorded
    assert out.version == "1"


# ---------------- conventional_metrics.py --------------------------


def test_word_overlap_ratio():
    from benchmarks.eval_v1.conventional_metrics import word_overlap

    r = word_overlap("the quick brown fox", "the brown fox jumps")
    assert r.gold_token_count == 4
    assert r.matched_token_count == 3
    assert r.ratio == pytest.approx(0.75)


def test_number_overlap_ignores_thousands_commas():
    from benchmarks.eval_v1.conventional_metrics import number_overlap

    r = number_overlap("Revenue was $1,234.56", "revenue of 1234.56 dollars")
    assert r.matched_token_count == 1
    assert r.ratio == 1.0


def test_llm_metric_stub_reports_not_executed():
    from benchmarks.eval_v1.conventional_metrics import (
        downstream_ragas_metric,
        llm_answer_judge_metric,
    )

    stub_a = llm_answer_judge_metric()
    stub_b = downstream_ragas_metric()
    assert stub_a.executed is False
    assert "not authorized" in stub_a.reason.lower() or "outside" in stub_a.reason.lower()
    assert stub_b.executed is False


# ---------------- corpus_adapter.py --------------------------------


def test_corpus_capabilities_fields_present():
    from benchmarks.eval_v1.corpus_adapter import CorpusCapabilities

    caps = CorpusCapabilities(
        corpus_name="test",
        on_v1_manifest=True,
        supports_textual_gt=True,
        supports_layout_gt=False,
        supports_clause_span_gt=False,
        supports_downstream_qa_gt=True,
        supports_clean_native_fpr=False,
    )
    assert caps.corpus_name == "test"
    assert caps.on_v1_manifest is True
    assert caps.not_applicable_reasons == {}


# ---------------- adjudication.py ----------------------------------


def test_severity_mapper_loads_only_appendix_b_diagonal():
    from benchmarks.eval_v1.adjudication import SeverityMapper, UNRESOLVED_MAPPING

    m = SeverityMapper.load()
    # v0 loads only the four diagonal rows Appendix B specifies verbatim.
    assert len(m.rows) == 4
    # Diagonal rows resolve.
    assert m.map("yes", "faithful", "usable").value == "GOOD"
    assert m.map("mostly", "faithful", "usable-with-caveats").value == "MINOR"
    assert m.map("partially", "minor issues", "degraded").value == "MAJOR"
    assert m.map("no", "mostly stub or junk", "wrong").value == "CATASTROPHIC"
    # An off-diagonal combination is deliberately not resolved.
    assert m.map("yes", "minor issues", "degraded") == UNRESOLVED_MAPPING


def test_severity_mapper_unresolved_count():
    from benchmarks.eval_v1.adjudication import SeverityMapper

    m = SeverityMapper.load()
    assert len(m.unresolved_combinations()) == 4 * 4 * 4 - 4  # 60


def test_reviewer_artifact_blinds_parser():
    from benchmarks.eval_v1.adjudication import (
        SeverityMapper,
        prepare_reviewer_artifact,
    )

    m = SeverityMapper.load()
    art_a = prepare_reviewer_artifact(
        doc_id="doc1",
        parser="marker",
        source_pdf_path="/x/y.pdf",
        extraction_markdown_path="/x/marker/y.md",
        normalized_markdown_path="/x/marker/y.norm.md",
        mapping=m,
    )
    art_b = prepare_reviewer_artifact(
        doc_id="doc1",
        parser="docling",
        source_pdf_path="/x/y.pdf",
        extraction_markdown_path="/x/docling/y.md",
        normalized_markdown_path="/x/docling/y.norm.md",
        mapping=m,
    )
    # Parser identity is blinded to a 16-hex-char digest, not the raw name.
    assert "marker" not in art_a.blinded_parser_hash
    assert "docling" not in art_b.blinded_parser_hash
    assert art_a.blinded_parser_hash != art_b.blinded_parser_hash
    # Pair id is also blinded and deterministic.
    assert art_a.pair_id != art_b.pair_id


# ---------------- corpus_split.py ----------------------------------


def test_assign_partition_is_deterministic():
    from benchmarks.eval_v1.corpus_split import assign_partition

    a1 = assign_partition("some-doc-id")
    a2 = assign_partition("some-doc-id")
    assert a1 == a2


def test_assign_partition_bucket_boundaries():
    from benchmarks.eval_v1.corpus_split import Partition, assign_partition

    # Assign 1_000 synthetic doc_ids and confirm the split roughly matches
    # 10 / 20 / 70 targeted proportions per PROTOCOL_V1.md §8.1.
    counts = {p: 0 for p in Partition}
    for i in range(1000):
        counts[assign_partition(f"doc-{i}")] += 1
    # Loose bounds — deterministic hashing is not perfectly uniform on 1000
    # ids but should be well within these guardrails.
    assert 60 <= counts[Partition.DEV] <= 160
    assert 150 <= counts[Partition.CAL] <= 260
    assert 620 <= counts[Partition.HELD_OUT] <= 800


def test_validate_no_overlap_raises_on_intersection():
    from benchmarks.eval_v1.corpus_split import validate_no_overlap

    with pytest.raises(ValueError, match="overlap"):
        validate_no_overlap(["a", "b"], ["b", "c"], ["d"])


# ---------------- statistics.py ------------------------------------


def test_bootstrap_by_document_flags_degenerate_on_small_n():
    from benchmarks.eval_v1.statistics import bootstrap_by_document

    docs = ["d1", "d2", "d3"]
    obs = {"d1": [0.8, 0.9], "d2": [0.5, 0.6], "d3": [0.7, 0.75]}
    r = bootstrap_by_document(docs, obs, n_resamples=200)
    assert r.n_documents == 3
    assert r.n_observations == 6
    assert r.degenerate is True  # N=3 << 20


def test_per_observation_bootstrap_forbidden():
    from benchmarks.eval_v1.statistics import per_observation_bootstrap_forbidden

    with pytest.raises(RuntimeError, match="prohibited"):
        per_observation_bootstrap_forbidden()


# ---------------- analysis_record.py -------------------------------


def test_analysis_record_schema_shape():
    from benchmarks.eval_v1.analysis_record import (
        ANALYSIS_RECORD_SCHEMA_VERSION,
        build_analysis_record,
    )

    pair_summary = {
        "doc_id": "D1",
        "parser": "marker",
        "corpus_name": "qasper",
        "corpus_capabilities": {"corpus_name": "qasper", "on_v1_manifest": True},
        "stages": [
            {"stage": "source_ingestion", "status": "EXECUTED", "reason": "", "payload": {"sha256": "abc"}},
            {"stage": "aksharamd_evaluation", "status": "EXECUTED", "reason": "", "payload": {"readiness_score": 84}},
        ],
        "total_elapsed_s": 1.23,
    }
    rec = build_analysis_record(pair_summary)
    assert rec["schema_version"] == ANALYSIS_RECORD_SCHEMA_VERSION
    assert rec["identity"]["doc_id"] == "D1"
    assert rec["identity"]["parser"] == "marker"
    assert rec["hashes"]["source_sha256"] == "abc"
    assert rec["aksharamd_result"]["readiness_score"] == 84


# ---------------- smoke_manifest.py --------------------------------


def test_smoke_manifest_has_three_dev_docs():
    from benchmarks.eval_v1.smoke_manifest import SMOKE_DOCS, V1_PARSERS

    assert len(SMOKE_DOCS) == 3
    assert {d.doc_id for d in SMOKE_DOCS} == {
        "D1_qasper_1503_00841",
        "D2_docbench_P19_1598",
        "D3_tatdqa_003755794b",
    }
    # Four V1 parsers per PROTOCOL_V1.md §7.
    assert len(V1_PARSERS) == 4
    assert set(V1_PARSERS) == {"aksharamd-reference", "marker", "docling", "markitdown"}
