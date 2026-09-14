"""Unit tests for benchmarks/eval_v1 infrastructure.

Scope: module-level invariants for the plumbing shipped under
Authorization A / A.1. Does not exercise the full pipeline (see
`benchmarks/eval_v1/smoke_run*.py` for rerun scripts and archived
results in `benchmarks/results/authorization-*-2026-09-14/`).

The A.1 completion criterion — 156/156 stage cells with zero DEFECT —
is validated by rerunning `smoke_run_v2.py`, not by these tests.
"""
from __future__ import annotations

import pytest

# ---------------- stages.py ----------------------------------------


def test_stage_status_executed_forbids_reason():
    from benchmarks.eval_v1.stages import StageResult, StageStatus

    with pytest.raises(ValueError, match="EXECUTED must not carry a reason"):
        StageResult(stage="s", status=StageStatus.EXECUTED, reason="nope")


def test_stage_status_non_executed_requires_reason():
    from benchmarks.eval_v1.stages import StageResult, StageStatus

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


def test_normalizer_applies_narrow_v1_rules_only():
    """V1 normalization is NFKC + LF only. Everything else is deferred."""
    from benchmarks.eval_v1.normalization import (
        NORMALIZATION_VERSION,
        UnicodeWhitespaceNormalizer,
    )

    # Compat ligature (NFKC-collapsible) + CRLF line endings.
    md = "ﬁle\r\ntext"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    # NFKC unpacks the ligature.
    assert "ﬁ" not in out.text
    assert "file" in out.text
    # CRLF unified to LF.
    assert "\r" not in out.text
    # Version recorded.
    assert out.version == NORMALIZATION_VERSION == "2"
    # Only these two rules ran.
    assert set(out.rules_applied).issubset({"nfkc", "lf_line_endings"})


def test_normalizer_does_not_strip_frontmatter():
    """Frontmatter stripping was removed from V1 — it can collide with
    a leading thematic break + heading + thematic break pattern."""
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "---\ntitle: X\n---\nbody"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "title: X" in out.text


def test_normalizer_does_not_remove_soft_hyphens():
    """Soft hyphens can appear inside code payloads intentionally; V1
    does not strip them, per Authorization A.1 review."""
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "Hello­world"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "­" in out.text


def test_normalizer_does_not_dehyphenate_across_linebreaks():
    """Dehyphenation was rejected because it can alter identifiers in
    code and change tokenization semantically."""
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "pre-\nprocessing"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "pre-\nprocessing" in out.text
    assert "preprocessing" not in out.text


def test_normalizer_preserves_fenced_code_indentation():
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "```python\ndef foo():\n    return 42\n```\n"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "```python" in out.text
    assert "    return 42" in out.text
    assert "```\n" in out.text


def test_normalizer_preserves_indented_code_block():
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "text\n\n    x = 1\n    y = 2\n"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "    x = 1" in out.text
    assert "    y = 2" in out.text


def test_normalizer_preserves_markdown_table():
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "| col1 | col2 |\n|---|---|\n| a  | b  |\n| c  | d  |\n"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "| col1 | col2 |" in out.text
    assert "|---|---|" in out.text
    assert "| a  | b  |" in out.text
    assert "| c  | d  |" in out.text


def test_normalizer_preserves_heading_syntax():
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "# H1\n\n## H2\n\n### H3\n"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "# H1" in out.text
    assert "## H2" in out.text
    assert "### H3" in out.text


def test_normalizer_preserves_list_nesting():
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "- top\n  - nested\n    - deep\n"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "- top" in out.text
    assert "  - nested" in out.text
    assert "    - deep" in out.text


def test_normalizer_preserves_trailing_double_space_hard_break():
    """Two trailing spaces are a Markdown hard line break; V1
    normalization must not strip them."""
    from benchmarks.eval_v1.normalization import UnicodeWhitespaceNormalizer

    md = "line one  \nline two"
    out = UnicodeWhitespaceNormalizer().normalize(md)
    assert "line one  \n" in out.text


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


def test_severity_mapper_default_loads_v1_full_64():
    """The default mapping is v1 — full 64-combination coverage."""
    from benchmarks.eval_v1.adjudication import SeverityMapper

    m = SeverityMapper.load()
    assert m.mapping_id == "appendix_b_v1"
    assert m.mapping_frozen is True
    assert len(m.rows) == 64
    assert m.unresolved_combinations() == []


def test_severity_mapper_diagonal_preserved_under_v1():
    """The four Appendix B diagonal rows must retain their labels
    under the v1 aggregation rule (coherence check §B.3)."""
    from benchmarks.eval_v1.adjudication import SeverityMapper

    m = SeverityMapper.load()
    assert m.map("yes", "faithful", "usable").value == "GOOD"
    assert m.map("mostly", "faithful", "usable-with-caveats").value == "MINOR"
    assert m.map("partially", "minor issues", "degraded").value == "MAJOR"
    assert m.map("no", "mostly stub or junk", "wrong").value == "CATASTROPHIC"


def test_severity_mapper_full_coverage_no_unresolved():
    """Under v1, every (q1, q2, q3) combination resolves to a label."""
    from benchmarks.eval_v1.adjudication import UNRESOLVED_MAPPING, SeverityMapper

    m = SeverityMapper.load()
    for combo in m.all_combinations():
        assert m.map(*combo) != UNRESOLVED_MAPPING, combo


def test_severity_mapper_monotonicity():
    """Worsening any one answer never improves severity.

    For any (q1, q2, q3) and any single-dimension replacement to a
    higher-rank value, the resulting label must have severity >= the
    original. This is PROTOCOL_V1.md §B.3 coherence check 3.
    """
    from benchmarks.eval_v1.adjudication import SeverityMapper

    m = SeverityMapper.load()
    label_rank = {"GOOD": 0, "MINOR": 1, "MAJOR": 2, "CATASTROPHIC": 3}

    def rank_of(q1: str, q2: str, q3: str) -> int:
        return label_rank[m.map(q1, q2, q3).value]

    axes = [
        (m.q1_values, 0),
        (m.q2_values, 1),
        (m.q3_values, 2),
    ]

    for combo in m.all_combinations():
        base_rank = rank_of(*combo)
        for values, dim in axes:
            base_val = combo[dim]
            base_idx = values.index(base_val)
            for higher_idx in range(base_idx + 1, len(values)):
                new_combo = list(combo)
                new_combo[dim] = values[higher_idx]
                new_rank = rank_of(*new_combo)
                assert new_rank >= base_rank, (
                    f"monotonicity violated: {combo} -> rank {base_rank} "
                    f"but {tuple(new_combo)} -> rank {new_rank} "
                    f"(dimension {dim} worsened, severity dropped)"
                )


def test_severity_mapper_v1_matches_max_rank_formula():
    """The v1 JSON file must match the aggregation rule
    ``label = LABELS[max(rank(q1), rank(q2), rank(q3))]`` on every row.

    If this test breaks, either mapping.v1.json was hand-edited away
    from the rule (should not happen) or the rule itself was amended
    (should coincide with a new mapping version and a new coherence
    proof)."""
    from benchmarks.eval_v1.adjudication import SeverityMapper

    m = SeverityMapper.load()
    labels = ("GOOD", "MINOR", "MAJOR", "CATASTROPHIC")
    q1r = {v: i for i, v in enumerate(m.q1_values)}
    q2r = {v: i for i, v in enumerate(m.q2_values)}
    q3r = {v: i for i, v in enumerate(m.q3_values)}
    for (q1, q2, q3), label in m.rows.items():
        expected = labels[max(q1r[q1], q2r[q2], q3r[q3])]
        assert label.value == expected, (q1, q2, q3, label, expected)


def test_severity_mapper_v0_still_loadable_as_historical_evidence():
    """The pre-amendment v0 mapping is preserved on disk so future
    reviewers can reconstruct exactly what methodology existed at
    Authorization A.1 time."""
    from benchmarks.eval_v1.adjudication import (
        MAPPING_FILE_HISTORICAL_V0,
        UNRESOLVED_MAPPING,
        SeverityMapper,
    )

    v0 = SeverityMapper.load(path=MAPPING_FILE_HISTORICAL_V0)
    assert v0.mapping_id == "appendix_b_v0"
    assert v0.mapping_frozen is False
    assert len(v0.rows) == 4
    # v0 refused to resolve off-diagonal combinations.
    assert v0.map("yes", "minor issues", "degraded") == UNRESOLVED_MAPPING


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
