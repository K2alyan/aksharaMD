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
    assert r.parser_matrix_balanced is True
    assert r.observations_per_document == (2, 2, 2)
    assert r.aggregation == "per_document"


def test_bootstrap_by_document_rejects_unbalanced_matrix_by_default():
    from benchmarks.eval_v1.statistics import (
        UnbalancedParserMatrixError,
        bootstrap_by_document,
    )

    docs = ["d1", "d2", "d3"]
    # d3 is missing a parser observation — silent flattening would
    # underweight it, so §11.2 requires a hard stop by default.
    obs = {"d1": [0.8, 0.9], "d2": [0.5, 0.6], "d3": [0.7]}
    with pytest.raises(UnbalancedParserMatrixError, match="unbalanced"):
        bootstrap_by_document(docs, obs, n_resamples=50)


def test_bootstrap_by_document_missing_doc_is_unbalanced():
    from benchmarks.eval_v1.statistics import (
        UnbalancedParserMatrixError,
        bootstrap_by_document,
    )

    # d3 not present in the mapping at all — treated as zero observations,
    # which is an imbalance and must be caught.
    docs = ["d1", "d2", "d3"]
    obs = {"d1": [0.8, 0.9], "d2": [0.5, 0.6]}
    with pytest.raises(UnbalancedParserMatrixError):
        bootstrap_by_document(docs, obs, n_resamples=50)


def test_bootstrap_by_document_records_imbalance_when_opted_in():
    from benchmarks.eval_v1.statistics import bootstrap_by_document

    docs = ["d1", "d2", "d3"]
    obs = {"d1": [0.8, 0.9], "d2": [0.5, 0.6], "d3": [0.7]}
    r = bootstrap_by_document(docs, obs, n_resamples=200, require_balanced=False)
    assert r.parser_matrix_balanced is False
    assert r.observations_per_document == (2, 2, 1)
    assert r.n_observations == 5


def test_bootstrap_by_document_per_document_vs_pooled_differ_when_appropriate():
    from benchmarks.eval_v1.statistics import bootstrap_by_document

    # Construct a case where per-document mean and pooled mean of the
    # SAME point estimate diverge: d3 has a single extreme observation,
    # so pooled weights it 1/5 while per_document weights it 1/3.
    docs = ["d1", "d2", "d3"]
    obs = {"d1": [0.0, 0.0], "d2": [0.0, 0.0], "d3": [1.0]}

    per_doc = bootstrap_by_document(
        docs, obs, n_resamples=100, require_balanced=False, aggregation="per_document"
    )
    pooled = bootstrap_by_document(
        docs, obs, n_resamples=100, require_balanced=False, aggregation="pooled"
    )

    # per_document: mean([0.0, 0.0, 1.0]) = 1/3
    # pooled:      mean([0.0, 0.0, 0.0, 0.0, 1.0]) = 1/5
    assert abs(per_doc.estimator_value - (1 / 3)) < 1e-9
    assert abs(pooled.estimator_value - (1 / 5)) < 1e-9
    assert per_doc.aggregation == "per_document"
    assert pooled.aggregation == "pooled"


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


# ---------------- adapters/pmc_oa_v1.py ----------------------------

_MINIMAL_JATS = """<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:mml="http://www.w3.org/1998/Math/MathML">
  <front>
    <article-meta>
      <article-id pub-id-type="pmc">PMC0000001</article-id>
      <title-group>
        <article-title>Bootstrap Semantics for Document Clustered Analysis</article-title>
      </title-group>
      <abstract>
        <p>The abstract discusses <xref rid="B1" ref-type="bibr">[1]</xref> the
        clustered bootstrap for parser evaluation studies.</p>
      </abstract>
      <permissions>
        <license license-type="open-access">
          <license-p>This is an open access article under CC BY.</license-p>
        </license>
      </permissions>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Introduction</title>
      <p>Document level resampling preserves within-document correlation
      structure, as noted by <xref rid="B2" ref-type="bibr">[2]</xref>.</p>
    </sec>
    <sec>
      <title>Methods</title>
      <p>We define the balanced parser matrix condition.</p>
      <fn-group>
        <fn id="fn1"><p>Footnote text is preserved in the oracle.</p></fn>
      </fn-group>
      <disp-formula id="eq1">
        <mml:math alttext="x + y = z"><mml:mi>x</mml:mi></mml:math>
      </disp-formula>
      <disp-formula id="eq2">
        <mml:math><mml:mi>alpha</mml:mi></mml:math>
      </disp-formula>
      <table-wrap id="T1">
        <label>Table 1</label>
        <caption><p>Parser matrix balance across the pilot corpus.</p></caption>
        <table>
          <thead>
            <tr><th>Parser</th><th>Documents</th></tr>
          </thead>
          <tbody>
            <tr><td>reference</td><td>20</td></tr>
            <tr><td>marker</td><td>20</td></tr>
          </tbody>
        </table>
      </table-wrap>
      <fig id="F1">
        <label>Figure 1</label>
        <caption><p>Overview of the resampling procedure.</p></caption>
      </fig>
    </sec>
  </body>
  <back>
    <ref-list>
      <ref id="B1"><label>1</label><mixed-citation>Excluded from oracle.</mixed-citation></ref>
      <ref id="B2"><label>2</label><mixed-citation>Also excluded.</mixed-citation></ref>
    </ref-list>
  </back>
</article>
""".strip().encode("utf-8")


def _write_pmc_oa_fixture(tmp_path):
    """Write a schema-v2 (AWS-era) PMC-OA fixture on disk.

    Mirrors what ``benchmarks.eval_v1.acquisition.pmc_oa_aws.acquire_article``
    produces: side-by-side ``<PMCID>.<v>.{json,pdf,xml}`` inside the
    versioned prefix, plus a v2 ``manifest.json`` provenance receipt.
    """
    import hashlib
    import json

    from benchmarks.eval_v1.acquisition.pmc_oa_aws import MANIFEST_SCHEMA_VERSION
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import PmcOaAsset

    pmcid = "PMC0000001"
    version = 1
    art_dir = tmp_path / f"{pmcid}.{version}"
    art_dir.mkdir()
    pdf_bytes = b"%PDF-1.4\n%synthetic-fixture-not-a-real-pdf\n"
    xml_bytes = _MINIMAL_JATS
    pdf_path = art_dir / f"{pmcid}.{version}.pdf"
    xml_path = art_dir / f"{pmcid}.{version}.xml"
    metadata_json_path = art_dir / f"{pmcid}.{version}.json"
    pdf_path.write_bytes(pdf_bytes)
    xml_path.write_bytes(xml_bytes)

    metadata_json = {
        "pmcid": pmcid,
        "version": version,
        "title": "Bootstrap Semantics for Document Clustered Analysis",
        "citation": "Test Author. Bootstrap Semantics. J. Fake Sci. 2026.",
        "doi": None,
        "pmid": None,
        "license_code": "CC BY",
        "is_pmc_openaccess": True,
        "is_retracted": False,
        "is_manuscript": False,
        "is_historical_ocr": False,
        "mid": None,
        "pdf_url": f"s3://pmc-oa-opendata/{pmcid}.{version}/{pmcid}.{version}.pdf?md5={hashlib.md5(pdf_bytes).hexdigest()}",
        "xml_url": f"s3://pmc-oa-opendata/{pmcid}.{version}/{pmcid}.{version}.xml?md5={hashlib.md5(xml_bytes).hexdigest()}",
        "text_url": None,
        "media_urls": [],
    }
    metadata_json_bytes = json.dumps(metadata_json, indent=2, sort_keys=True).encode()
    metadata_json_path.write_bytes(metadata_json_bytes)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus": "pmc_oa",
        "pmcid": pmcid,
        "version": version,
        "article_key_prefix": f"{pmcid}.{version}/",
        "acquired_utc": "2026-09-14T17:00:00+00:00",
        "distribution": {
            "source": "aws_open_data_pmc_oa",
            "bucket": "pmc-oa-opendata",
            "https_base_url": "https://pmc-oa-opendata.s3.amazonaws.com/",
            "inventory_snapshot_utc": None,
            "inventory_manifest_sha256": None,
        },
        "metadata_object": {
            "key": f"metadata/{pmcid}.{version}.json",
            "size_bytes": len(metadata_json_bytes),
            "sha256": hashlib.sha256(metadata_json_bytes).hexdigest(),
        },
        "pdf": {
            "key": f"{pmcid}.{version}/{pmcid}.{version}.pdf",
            "canonical_url": metadata_json["pdf_url"],
            "size_bytes": len(pdf_bytes),
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "md5": hashlib.md5(pdf_bytes).hexdigest(),
            "etag": hashlib.md5(pdf_bytes).hexdigest(),
            "md5_from_metadata": hashlib.md5(pdf_bytes).hexdigest(),
        },
        "xml": {
            "key": f"{pmcid}.{version}/{pmcid}.{version}.xml",
            "canonical_url": metadata_json["xml_url"],
            "size_bytes": len(xml_bytes),
            "sha256": hashlib.sha256(xml_bytes).hexdigest(),
            "md5": hashlib.md5(xml_bytes).hexdigest(),
            "etag": hashlib.md5(xml_bytes).hexdigest(),
            "md5_from_metadata": hashlib.md5(xml_bytes).hexdigest(),
            "pmcid_in_jats": pmcid,
        },
        "license": {
            "license_code_from_metadata": "CC BY",
            "license_type_from_xml": "open-access",
            "license_text_from_xml": "This is an open access article under CC BY.",
        },
        "flags_from_metadata": {
            "is_pmc_openaccess": True,
            "is_retracted": False,
            "is_manuscript": False,
            "is_historical_ocr": False,
        },
        "citation": metadata_json["citation"],
        "doi": None,
        "pmid": None,
    }
    manifest_path = art_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    asset = PmcOaAsset(
        pmcid=pmcid,
        pdf_path=pdf_path,
        xml_path=xml_path,
        manifest_path=manifest_path,
        version=version,
        metadata_json_path=metadata_json_path,
    )
    return asset


def test_pmc_oa_capabilities_declare_textual_g1_only():
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import PmcOaV1Adapter

    adapter = PmcOaV1Adapter({})
    caps = adapter.capabilities()
    assert caps.corpus_name == "pmc_oa"
    assert caps.on_v1_manifest is True
    assert caps.supports_textual_gt is True
    for f in (
        "supports_layout_gt",
        "supports_clause_span_gt",
        "supports_downstream_qa_gt",
        "supports_clean_native_fpr",
    ):
        assert getattr(caps, f) is False
        stage_key = f.removeprefix("supports_")
        assert "§2.2" in caps.not_applicable_reasons[stage_key]


def test_pmc_oa_ingest_source_uses_manifest_hashes(tmp_path):
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import (
        CORPUS_NAME,
        PmcOaV1Adapter,
    )

    asset = _write_pmc_oa_fixture(tmp_path)
    adapter = PmcOaV1Adapter({asset.pmcid: asset})
    si = adapter.ingest_source(asset.pmcid)
    assert si.doc_id == asset.pmcid
    assert si.media_type == "application/pdf"
    assert si.path == asset.pdf_path
    assert si.provenance["corpus"] == CORPUS_NAME
    assert si.provenance["source_kind"] == "aws_open_data_pmc_oa"
    assert si.provenance["pmcid"] == asset.pmcid
    assert si.provenance["version"] == asset.version
    assert si.provenance["bucket"] == "pmc-oa-opendata"
    assert si.provenance["pdf_key"].endswith(".pdf")
    assert si.provenance["pdf_canonical_url"].startswith("s3://")
    assert si.provenance["pdf_md5"]  # non-empty MD5 recorded
    assert si.provenance["manifest_path"] == str(asset.manifest_path)
    assert si.provenance["metadata_json_path"] == str(asset.metadata_json_path)


def test_pmc_oa_ingest_source_rejects_hash_mismatch(tmp_path):
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import PmcOaV1Adapter

    asset = _write_pmc_oa_fixture(tmp_path)
    # Corrupt the PDF on disk after the manifest was written.
    asset.pdf_path.write_bytes(b"%PDF-1.4\n%tampered\n")
    adapter = PmcOaV1Adapter({asset.pmcid: asset})
    with pytest.raises(RuntimeError, match="cache is inconsistent"):
        adapter.ingest_source(asset.pmcid)


def test_pmc_oa_ground_truth_preserves_ordered_tokens(tmp_path):
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import (
        EXTRACTION_RULES_VERSION,
        GT_KIND,
        PmcOaV1Adapter,
    )

    asset = _write_pmc_oa_fixture(tmp_path)
    adapter = PmcOaV1Adapter({asset.pmcid: asset})
    gt = adapter.ingest_ground_truth(asset.pmcid)
    assert gt is not None
    assert gt.kind == GT_KIND
    assert gt.provenance["extraction_rules_version"] == EXTRACTION_RULES_VERSION
    assert gt.provenance["xml_sha256"]  # non-empty
    # License comes from the canonical PMC metadata JSON, not our manifest.
    assert gt.provenance["license_code_from_metadata"] == "CC BY"
    assert gt.provenance["flags_from_metadata"]["is_pmc_openaccess"] is True
    assert gt.provenance["flags_from_metadata"]["is_historical_ocr"] is False

    data = gt.data
    # Title, abstract, body prose, table cell contents, and caption text
    # must all appear in body_text; ref-list content must not.
    assert "Bootstrap Semantics" in data["title"]
    assert "clustered bootstrap" in data["abstract"]
    assert "Document level resampling" in data["body_text"]
    assert "Footnote text is preserved" in data["body_text"]
    assert "Parser matrix balance" in data["body_text"]
    assert "reference" in data["body_text"]  # table cell
    assert "Excluded from oracle" not in data["body_text"]

    # Ordered tokens with multiplicity — no set semantics baked in.
    assert isinstance(data["tokens"], list)
    assert data["tokens"] == [t.lower() for t in data["tokens"]]
    # "the" appears many times in the fixture; multiplicity > 1 proves
    # the oracle is not a set.
    assert data["tokens"].count("the") > 1

    # Tables preserved with cell boundaries.
    assert data["tables"], "expected at least one table"
    t = data["tables"][0]
    assert t["label"] == "Table 1"
    assert ["reference", "20"] in t["rows"]

    # Captions preserved separately for auditability.
    assert any("resampling procedure" in c for c in data["captions"])


def test_pmc_oa_extraction_stats_count_exclusions(tmp_path):
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import PmcOaV1Adapter

    asset = _write_pmc_oa_fixture(tmp_path)
    adapter = PmcOaV1Adapter({asset.pmcid: asset})
    gt = adapter.ingest_ground_truth(asset.pmcid)
    assert gt is not None
    stats = gt.provenance["extraction_stats"]

    # Two <ref> entries in <ref-list>, two <xref> markers in body/abstract.
    assert stats["excluded_ref_list_entries"] == 2
    assert stats["excluded_xref_markers"] == 2

    # Fixture has 2 <disp-formula>: one with alttext, one text-only.
    assert stats["equations_with_alttext"] == 1
    assert stats["equations_with_text_only"] == 1
    assert stats["equations_unrepresented"] == 0

    # Non-negative counters for the included buckets.
    for k in (
        "included_paragraphs",
        "included_section_titles",
        "included_table_cells",
        "included_captions",
        "included_footnotes",
    ):
        assert stats[k] >= 0


def test_pmc_oa_word_overlap_self_roundtrip_is_one(tmp_path):
    """Plumbing sanity check — self-overlap must be 1.0."""
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import PmcOaV1Adapter
    from benchmarks.eval_v1.conventional_metrics import word_overlap

    asset = _write_pmc_oa_fixture(tmp_path)
    gt = PmcOaV1Adapter({asset.pmcid: asset}).ingest_ground_truth(asset.pmcid)
    assert gt is not None
    body_text = gt.data["body_text"]
    r = word_overlap(body_text, body_text)
    assert r.ratio == 1.0


def test_pmc_oa_acquisition_manifest_schema_constant():
    from benchmarks.eval_v1.acquisition.pmc_oa_aws import MANIFEST_SCHEMA_VERSION

    # Schema v2 corresponds to the AWS Open Data distribution contract.
    assert MANIFEST_SCHEMA_VERSION == "2"


# ---- Regression: JATS traversal must not double-count semantic regions ----

_MINIMAL_JATS_WITH_TABLE_AND_FIG = """<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmcid">PMC0000002</article-id>
    <title-group><article-title>Traversal Regression Fixture</article-title></title-group>
    <abstract><p>Short abstract.</p></abstract>
  </article-meta></front>
  <body>
    <sec>
      <title>Introduction</title>
      <p>Body paragraph one.</p>
      <fig id="F1">
        <label>Figure 1</label>
        <caption><p>UNIQUE_FIGURE_CAPTION_TOKEN</p></caption>
      </fig>
      <p>Body paragraph two.</p>
      <table-wrap id="T1">
        <label>Table 1</label>
        <caption><p>UNIQUE_TABLE_CAPTION_TOKEN</p></caption>
        <table>
          <tbody>
            <tr><td>UNIQUE_TABLE_CELL_TOKEN</td><td>42</td></tr>
          </tbody>
        </table>
      </table-wrap>
      <fn-group>
        <fn id="fn1"><p>UNIQUE_FOOTNOTE_TOKEN preserved.</p></fn>
      </fn-group>
    </sec>
  </body>
</article>
""".strip().encode("utf-8")


def test_pmc_oa_traversal_emits_each_semantic_region_once():
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import _transform_jats

    core, stats, tables, captions = _transform_jats(_MINIMAL_JATS_WITH_TABLE_AND_FIG)
    body_text = core["body_text"]

    # Each semantic region appears in body_text exactly once.
    assert body_text.count("UNIQUE_FIGURE_CAPTION_TOKEN") == 1
    assert body_text.count("UNIQUE_TABLE_CAPTION_TOKEN") == 1
    assert body_text.count("UNIQUE_TABLE_CELL_TOKEN") == 1
    assert body_text.count("UNIQUE_FOOTNOTE_TOKEN") == 1

    # Body paragraphs stay body paragraphs; captions/cells/footnotes are
    # NOT counted as ordinary paragraphs.
    assert stats.included_paragraphs == 2, (
        "expected exactly 2 body <p> paragraphs; caption <p>, footnote <p>, "
        "and table-cell content must not inflate this counter. "
        f"got included_paragraphs={stats.included_paragraphs}"
    )

    # Section title counted separately.
    assert stats.included_section_titles == 1

    # Both figure caption and table caption were emitted; each was
    # counted once.
    assert stats.included_captions == 2

    # Footnote counted separately.
    assert stats.included_footnotes == 1

    # Table cell counter is semantically distinct from paragraphs.
    assert stats.included_table_cells == 2  # 1 row × 2 cells

    # And the enumerated collections reflect the same counts.
    assert len(captions) == 2
    assert len(tables) == 1
    assert len(tables[0]["rows"]) == 1
    assert tables[0]["rows"][0] == ["UNIQUE_TABLE_CELL_TOKEN", "42"]


def test_pmc_oa_traversal_paragraph_inside_fig_is_not_counted_as_body_paragraph():
    """The <p> inside a <fig><caption> is caption content, not a body paragraph."""
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import _transform_jats

    core, stats, _tables, _captions = _transform_jats(
        _MINIMAL_JATS_WITH_TABLE_AND_FIG
    )
    body_text = core["body_text"]
    # Sanity: caption text is present exactly once (from the fig handler,
    # NOT again from a stray <p> visit).
    assert body_text.count("UNIQUE_FIGURE_CAPTION_TOKEN") == 1
    # Sanity: included_paragraphs excludes the caption's inner <p>.
    assert stats.included_paragraphs == 2


# ---- DocLayNet adapter (B1a-3) ----------------------------------


def _write_doclaynet_fixture(tmp_path):
    """Write a schema-v1 DocLayNet page fixture on disk.

    Mirrors what ``benchmarks.eval_v1.acquisition.doclaynet_hf.acquire_page``
    produces: PNG + PDF + annotations JSON + local manifest under
    ``<page_hash>/``. A tiny 4-byte PNG stand-in is fine for adapter
    tests — we assert on schema and hash consistency, not on image
    semantics.
    """
    import hashlib
    import json

    from benchmarks.eval_v1.acquisition.doclaynet_hf import (
        MANIFEST_SCHEMA_VERSION,
    )
    from benchmarks.eval_v1.adapters.doclaynet_v1 import DocLayNetAsset

    page_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    page_dir = tmp_path / page_hash
    page_dir.mkdir()

    png_bytes = b"\x89PNG\r\n\x1a\n_synthetic_fixture_not_a_real_png_"
    pdf_bytes = b"%PDF-1.4\n%synthetic-fixture-not-a-real-pdf\n"
    png_path = page_dir / f"{page_hash}.png"
    pdf_path = page_dir / f"{page_hash}.pdf"
    ann_path = page_dir / f"{page_hash}.annotations.json"
    png_path.write_bytes(png_bytes)
    pdf_path.write_bytes(pdf_bytes)

    annotations_payload = {
        "coco_categories": [
            {"id": 1, "name": "Caption"},
            {"id": 9, "name": "Table"},
            {"id": 10, "name": "Text"},
            {"id": 11, "name": "Title"},
        ],
        "coordinate_space": {
            "kind": "png_pixels",
            "coco_width": 1025,
            "coco_height": 1025,
            "original_width": 612.0,
            "original_height": 792.0,
            "note": "Bboxes are in COCO PNG-pixel space.",
        },
        "annotations": [
            {"category_id": 11, "category": "Title", "bbox_png": [100.0, 50.0, 400.0, 40.0], "area": 16000.0},
            {"category_id": 10, "category": "Text", "bbox_png": [80.0, 120.0, 800.0, 200.0], "area": 160000.0},
            {"category_id": 9, "category": "Table", "bbox_png": [80.0, 400.0, 800.0, 300.0], "area": 240000.0},
            {"category_id": 1, "category": "Caption", "bbox_png": [80.0, 720.0, 400.0, 20.0], "area": 8000.0},
        ],
    }
    ann_path.write_text(json.dumps(annotations_payload, indent=2, sort_keys=True))
    ann_bytes = ann_path.read_bytes()

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus": "doclaynet",
        "page_hash": page_hash,
        "acquired_utc": "2026-09-14T18:00:00+00:00",
        "distribution": {
            "source": "huggingface_datasets_parquet",
            "dataset_id": "docling-project/DocLayNet-v1.2",
            "dataset_commit_sha": "0daf93102e2efce76c3e11a274a5e0d0969391d3",
            "dataset_last_modified_utc": "2025-02-10T16:33:40.000Z",
            "hf_api_url": "https://huggingface.co/api/datasets/docling-project/DocLayNet-v1.2",
            "shard_key": "data/train-00000-of-00072.parquet",
            "shard_sha256": None,
            "split": "train",
        },
        "image_id": 42,
        "original_filename": "synthetic.pdf",
        "page_no": 3,
        "doc_category": "financial_reports",
        "collection": "synthetic_collection",
        "num_pages_in_original": 10,
        "modalities": ["layout"],
        "precedence": None,
        "coordinate_space": annotations_payload["coordinate_space"],
        "annotations_summary": {
            "n_annotations": 4,
            "category_distribution": {"Title": 1, "Text": 1, "Table": 1, "Caption": 1},
        },
        "png": {
            "path": png_path.name,
            "sha256": hashlib.sha256(png_bytes).hexdigest(),
            "size_bytes": len(png_bytes),
        },
        "pdf": {
            "path": pdf_path.name,
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "size_bytes": len(pdf_bytes),
            "present_in_distribution": True,
        },
        "annotations_file": {
            "path": ann_path.name,
            "sha256": hashlib.sha256(ann_bytes).hexdigest(),
            "size_bytes": len(ann_bytes),
        },
        "selection": {"authorization": "B1a-3", "role": "test-fixture", "discovery": {}},
    }
    manifest_path = page_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    return DocLayNetAsset(
        page_hash=page_hash,
        manifest_path=manifest_path,
        annotations_path=ann_path,
        png_path=png_path,
        pdf_path=pdf_path,
    )


def test_doclaynet_capabilities_declare_layout_g1_only():
    from benchmarks.eval_v1.adapters.doclaynet_v1 import DocLayNetV1Adapter

    caps = DocLayNetV1Adapter({}).capabilities()
    assert caps.corpus_name == "doclaynet"
    assert caps.on_v1_manifest is True
    assert caps.supports_layout_gt is True
    for f in (
        "supports_textual_gt",
        "supports_clause_span_gt",
        "supports_downstream_qa_gt",
        "supports_clean_native_fpr",
    ):
        assert getattr(caps, f) is False
        stage_key = f.removeprefix("supports_")
        assert stage_key in caps.not_applicable_reasons
    # Layout is the supported role and should NOT be in the NA dict.
    assert "layout_gt" not in caps.not_applicable_reasons


def test_doclaynet_ingest_source_uses_manifest_hashes(tmp_path):
    from benchmarks.eval_v1.adapters.doclaynet_v1 import (
        CORPUS_NAME,
        DocLayNetV1Adapter,
    )

    asset = _write_doclaynet_fixture(tmp_path)
    adapter = DocLayNetV1Adapter({asset.page_hash: asset})
    si = adapter.ingest_source(asset.page_hash)
    assert si.doc_id == asset.page_hash
    assert si.media_type == "image/png"
    assert si.path == asset.png_path
    assert si.provenance["corpus"] == CORPUS_NAME
    assert si.provenance["source_kind"] == "huggingface_datasets_parquet"
    assert si.provenance["page_hash"] == asset.page_hash
    assert si.provenance["dataset_id"] == "docling-project/DocLayNet-v1.2"
    assert len(si.provenance["dataset_commit_sha"]) == 40
    assert si.provenance["split"] == "train"
    assert si.provenance["pdf_present_in_distribution"] is True


def test_doclaynet_ingest_source_rejects_hash_mismatch(tmp_path):
    from benchmarks.eval_v1.adapters.doclaynet_v1 import DocLayNetV1Adapter

    asset = _write_doclaynet_fixture(tmp_path)
    asset.png_path.write_bytes(b"\x89PNG\r\n\x1a\n_tampered_")
    adapter = DocLayNetV1Adapter({asset.page_hash: asset})
    with pytest.raises(RuntimeError, match="cache is inconsistent"):
        adapter.ingest_source(asset.page_hash)


def test_doclaynet_ground_truth_primitive_oracle_only(tmp_path):
    """Adapter must NOT emit derived fields — no reading order, no cell grid."""
    from benchmarks.eval_v1.adapters.doclaynet_v1 import (
        EXTRACTION_RULES_VERSION,
        GT_KIND,
        DocLayNetV1Adapter,
    )

    asset = _write_doclaynet_fixture(tmp_path)
    gt = DocLayNetV1Adapter({asset.page_hash: asset}).ingest_ground_truth(
        asset.page_hash
    )
    assert gt is not None
    assert gt.kind == GT_KIND
    assert gt.provenance["extraction_rules_version"] == EXTRACTION_RULES_VERSION

    # Primitive oracle only.
    assert "expected_reading_order" not in gt.data
    assert "table_cell_structure" not in gt.data

    # What IS present.
    assert gt.data["page_hash"] == asset.page_hash
    assert gt.data["coordinate_space"]["kind"] == "png_pixels"
    assert gt.data["coordinate_space"]["coco_width"] == 1025
    assert gt.data["coordinate_space"]["original_width"] == 612.0
    assert len(gt.data["annotations"]) == 4
    assert {a["category"] for a in gt.data["annotations"]} == {
        "Title", "Text", "Table", "Caption",
    }

    # `precedence` recorded as null (v1.0 field absent in v1.2 Parquet).
    assert gt.provenance["precedence"] is None


def test_doclaynet_convert_bbox_png_to_pdf_points_reversible():
    """Coordinate-space conversion round-trip — silent-tank-recall guard."""
    from benchmarks.eval_v1.adapters.doclaynet_v1 import convert_bbox_to_pdf_points

    cw, ch = 1025, 1025
    ow, oh = 612.0, 792.0
    bbox_png = (100.0, 50.0, 400.0, 40.0)
    conv = convert_bbox_to_pdf_points(bbox_png, cw, ch, ow, oh)
    # Manual expected values from linear scale.
    sx, sy = ow / cw, oh / ch
    expected = (100.0 * sx, 50.0 * sy, 400.0 * sx, 40.0 * sy)
    for got, want in zip(conv, expected, strict=True):
        assert abs(got - want) < 1e-9

    # Reverse — must round-trip within numerical tolerance.
    reversed_bbox = tuple(v / (sx if i % 2 == 0 else sy) for i, v in enumerate(conv))
    for got, want in zip(reversed_bbox, bbox_png, strict=True):
        assert abs(got - want) < 1e-9


def test_doclaynet_convert_bbox_rejects_bad_input():
    from benchmarks.eval_v1.adapters.doclaynet_v1 import convert_bbox_to_pdf_points

    with pytest.raises(ValueError, match="non-zero"):
        convert_bbox_to_pdf_points((0.0, 0.0, 1.0, 1.0), 0, 1025, 612.0, 792.0)
    with pytest.raises(ValueError, match="4 elements"):
        convert_bbox_to_pdf_points((0.0, 0.0, 1.0), 1025, 1025, 612.0, 792.0)


def test_doclaynet_apply_page_filters_locked_set():
    """The lightweight structural filter: table + non-Text non-Table + count bounds."""
    from benchmarks.eval_v1.acquisition.doclaynet_hf import (
        NAME_TO_CATEGORY_ID,
        apply_page_filters,
    )

    T = NAME_TO_CATEGORY_ID["Table"]
    TXT = NAME_TO_CATEGORY_ID["Text"]
    TITLE = NAME_TO_CATEGORY_ID["Title"]

    # OK: 10 annotations, has Table + Title (non-Text non-Table)
    ok_cats = [T, TITLE] + [TXT] * 8
    d = apply_page_filters({"category_id": ok_cats})
    assert d.ok is True

    # too few
    d = apply_page_filters({"category_id": [T, TITLE, TXT]})
    assert d.ok is False and d.reason.startswith("n_annotations_lt_")

    # too many
    d = apply_page_filters({"category_id": [T] + [TXT] * 250})
    assert d.ok is False and d.reason.startswith("n_annotations_gt_")

    # no Table
    d = apply_page_filters({"category_id": [TITLE] + [TXT] * 10})
    assert d.ok is False and d.reason == "no_table_region"

    # Only Table and Text — no additional non-Text structural region
    d = apply_page_filters({"category_id": [T] + [TXT] * 10})
    assert d.ok is False and d.reason == "no_additional_non_text_structural_region"

    # no annotations
    d = apply_page_filters({"category_id": []})
    assert d.ok is False and d.reason == "no_annotations"


def test_pmc_oa_aws_apply_metadata_filters_locked_set():
    from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
        PmcVersionMetadata,
        apply_metadata_filters,
    )

    base = dict(
        pmcid="PMC1",
        version=1,
        title="",
        citation=None,
        doi=None,
        pmid=None,
        license_code="CC BY",
        is_pmc_openaccess=True,
        is_retracted=False,
        is_manuscript=False,
        is_historical_ocr=False,
        mid=None,
        pdf_url="s3://p/pdf",
        xml_url="s3://p/xml",
        text_url=None,
        media_urls=(),
        raw={},
    )

    ok = apply_metadata_filters(PmcVersionMetadata(**base))
    assert ok.ok and ok.reason is None

    for override, expected_prefix in (
        ({"license_code": "CC BY-NC"}, "license_not_CC_BY"),
        ({"license_code": None}, "license_not_CC_BY"),
        ({"is_pmc_openaccess": False}, "is_pmc_openaccess_false"),
        ({"is_retracted": True}, "is_retracted_true"),
        ({"is_manuscript": True}, "is_manuscript_true"),
        ({"is_historical_ocr": True}, "is_historical_ocr_true"),
        ({"pdf_url": ""}, "no_pdf_url"),
        ({"xml_url": ""}, "no_xml_url"),
    ):
        d = apply_metadata_filters(PmcVersionMetadata(**{**base, **override}))
        assert d.ok is False
        assert d.reason is not None and d.reason.startswith(expected_prefix), (
            override,
            d.reason,
        )
