"""Tests for Benchmark A harness and schema."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.document_package.schema import (
    BASELINE_A_SERIALIZER_VERSION,
    AnomalyRecord,
    AnswerKey,
    BaselineARecord,
    BenchmarkMetadata,
    CategorySummary,
    CorpusEntry,
    CorpusRunSummary,
    DocumentCapture,
    DocumentCategory,
    DocumentSplit,
    GradingMethod,
    HeldOutRunLock,
    OcrStatus,
    PreservationMetrics,
    QuestionRecord,
    QuestionType,
    RepresentationMetrics,
    RepresentationName,
    TextTokenBreakdown,
    TokenSavingsAttribution,
    VisualMetrics,
)


# ── Schema validation tests ────────────────────────────────────────────────────

def test_corpus_entry_required_fields():
    entry = CorpusEntry(document_id="doc1", file_path="docs/test.pdf", file_type="pdf", split=DocumentSplit.DEV)
    assert entry.document_id == "doc1"
    assert entry.split == DocumentSplit.DEV

def test_corpus_entry_held_out_split():
    entry = CorpusEntry(document_id="doc2", file_path="docs/test2.pdf", file_type="pdf", split=DocumentSplit.HELD_OUT)
    assert entry.split == "held_out"

def test_corpus_entry_categories():
    entry = CorpusEntry(
        document_id="doc3", file_path="f.pdf", file_type="pdf",
        split=DocumentSplit.DEV,
        categories=[DocumentCategory.FINANCIAL, DocumentCategory.TABLE_HEAVY],
    )
    assert DocumentCategory.FINANCIAL in entry.categories

def test_corpus_entry_ocr_status_default():
    entry = CorpusEntry(document_id="d", file_path="f.pdf", file_type="pdf", split=DocumentSplit.DEV)
    assert entry.ocr_status == OcrStatus.UNKNOWN

def test_question_record_types():
    q = QuestionRecord(
        question_id="q1", document_id="doc1",
        question="What was the revenue?",
        question_type=QuestionType.TABLE_LOOKUP,
    )
    assert q.question_type == "table_lookup"

def test_question_record_requires_visual_default():
    q = QuestionRecord(question_id="q1", document_id="d", question="?", question_type=QuestionType.TEXT_RETRIEVAL)
    assert q.requires_visual is False

def test_answer_key_grading_methods():
    ak = AnswerKey(grading_method=GradingMethod.DETERMINISTIC, accepted_answers=["42"])
    assert ak.grading_method == "deterministic"

def test_answer_key_answer_types():
    for t in ("exact", "normalized", "semantic", "unsupported"):
        ak = AnswerKey(answer_type=t)
        assert ak.answer_type == t

def test_benchmark_metadata_required_fields():
    m = BenchmarkMetadata(
        parser_version="0.3.6",
        planner_version="1.0",
        tokenizer="heuristic",
        code_commit="abc1234",
        capture_timestamp="2026-07-14T00:00:00+00:00",
    )
    assert m.code_commit == "abc1234"
    assert m.planner_version == "1.0"

def test_benchmark_metadata_evaluation_model_optional():
    m = BenchmarkMetadata(
        parser_version="1.0", planner_version="1.0",
        tokenizer="heuristic", code_commit="abc",
        capture_timestamp="2026-07-14T00:00:00+00:00",
    )
    assert m.evaluation_model is None

def test_representation_name_values():
    assert RepresentationName.BASELINE_A == "baseline_a"
    assert RepresentationName.CANDIDATE_D == "candidate_d"

def test_text_token_breakdown_total():
    tbd = TextTokenBreakdown(markdown_tokens=100, structured_table_tokens=50, warning_tokens=5)
    assert tbd.total == 155

def test_representation_metrics_schema_version():
    meta = BenchmarkMetadata(
        parser_version="0.3.6", planner_version="1.0",
        tokenizer="heuristic", code_commit="abc",
        capture_timestamp="2026-07-14T00:00:00Z",
    )
    rm = RepresentationMetrics(
        capture_id="cap1", document_id="doc1",
        representation=RepresentationName.CANDIDATE_D,
        metadata=meta,
    )
    assert rm.schema_version == "1.0"

def test_representation_metrics_has_all_version_fields():
    meta = BenchmarkMetadata(
        parser_version="0.3.6", planner_version="1.0",
        tokenizer="heuristic", code_commit="abc1234",
        capture_timestamp="2026-07-14T00:00:00Z",
    )
    rm = RepresentationMetrics(
        capture_id="c", document_id="d",
        representation=RepresentationName.BASELINE_B,
        metadata=meta,
    )
    assert rm.metadata.parser_version == "0.3.6"
    assert rm.metadata.planner_version == "1.0"
    assert rm.metadata.code_commit == "abc1234"
    assert rm.metadata.tokenizer == "heuristic"

def test_document_capture_structure():
    meta = BenchmarkMetadata(
        parser_version="1.0", planner_version="1.0",
        tokenizer="heuristic", code_commit="abc",
        capture_timestamp="2026-07-14T00:00:00Z",
    )
    cap = DocumentCapture(
        capture_id="cap1", document_id="doc1",
        timestamp="2026-07-14T00:00:00Z",
        metadata=meta,
    )
    assert cap.capture_id == "cap1"
    assert cap.schema_version == "1.0"
    assert cap.baselines == []
    assert cap.candidates == []

def test_dev_held_out_separation():
    dev = CorpusEntry(document_id="d1", file_path="f.pdf", file_type="pdf", split=DocumentSplit.DEV)
    held = CorpusEntry(document_id="d2", file_path="g.pdf", file_type="pdf", split=DocumentSplit.HELD_OUT)
    assert dev.split != held.split

def test_preservation_metrics_defaults():
    pm = PreservationMetrics()
    assert pm.meaningful_elements_discovered == 0
    assert pm.representation_downgrades == 0

def test_visual_metrics_defaults():
    vm = VisualMetrics()
    assert vm.selected_visual_asset_count == 0
    assert vm.total_image_pixels == 0

def test_corpus_entry_serializes_to_dict():
    entry = CorpusEntry(
        document_id="doc1", file_path="docs/test.pdf", file_type="pdf",
        split=DocumentSplit.DEV, categories=[DocumentCategory.PROSE],
        page_count=10,
    )
    d = entry.model_dump()
    assert d["document_id"] == "doc1"
    assert d["split"] == "dev"
    assert d["page_count"] == 10

def test_question_round_trip():
    q = QuestionRecord(
        question_id="q1", document_id="doc1",
        question="What was the total?",
        question_type=QuestionType.TABLE_LOOKUP,
        requires_visual=False,
        answer_key=AnswerKey(
            accepted_answers=["100"],
            grading_method=GradingMethod.DETERMINISTIC,
            answer_type="exact",
        ),
    )
    d = q.model_dump()
    q2 = QuestionRecord.model_validate(d)
    assert q2.question_id == q.question_id
    assert q2.answer_key.accepted_answers == ["100"]


# ── Harness helpers ────────────────────────────────────────────────────────────

def _mock_block(content: str) -> MagicMock:
    b = MagicMock()
    b.content = content
    return b


def _make_metadata() -> BenchmarkMetadata:
    return BenchmarkMetadata(
        parser_version="test",
        planner_version="test",
        tokenizer="heuristic",
        code_commit="abc1234",
        capture_timestamp="2026-07-13T00:00:00+00:00",
    )


def _make_rep_metrics(rep: RepresentationName, tokens: int, visual: int = 0) -> RepresentationMetrics:
    return RepresentationMetrics(
        capture_id="abc",
        document_id="doc-01",
        representation=rep,
        metadata=_make_metadata(),
        emitted_text_tokens=tokens,
        token_breakdown=TextTokenBreakdown(markdown_tokens=tokens),
        visual=VisualMetrics(selected_visual_asset_count=visual),
        preservation=PreservationMetrics(
            meaningful_elements_discovered=10,
            elements_preserved_in_package=9,
        ),
    )


def _make_capture(
    doc_id: str = "doc-01",
    a: int = 1000,
    b: int = 800,
    c: int = 750,
    d: int = 770,
    e: int = 790,
) -> DocumentCapture:
    meta = _make_metadata()
    return DocumentCapture(
        capture_id="cap1",
        document_id=doc_id,
        timestamp="2026-07-13T00:00:00+00:00",
        metadata=meta,
        baselines=[
            _make_rep_metrics(RepresentationName.BASELINE_A, a),
            _make_rep_metrics(RepresentationName.BASELINE_B, b),
        ],
        candidates=[
            _make_rep_metrics(RepresentationName.CANDIDATE_C, c, visual=0),
            _make_rep_metrics(RepresentationName.CANDIDATE_D, d, visual=2),
            _make_rep_metrics(RepresentationName.CANDIDATE_E, e, visual=3),
        ],
    )


# ── Baseline A tests ───────────────────────────────────────────────────────────

def test_serialize_baseline_a_is_deterministic():
    from benchmarks.document_package.harness import serialize_baseline_a
    blocks = [_mock_block("Hello world"), _mock_block("Second block")]
    result1 = serialize_baseline_a(blocks)
    result2 = serialize_baseline_a(blocks)
    assert result1 == result2


def test_serialize_baseline_a_includes_all_blocks():
    from benchmarks.document_package.harness import serialize_baseline_a
    blocks = [_mock_block("Alpha content"), _mock_block("Beta content"), _mock_block("Gamma content")]
    result = serialize_baseline_a(blocks)
    assert "Alpha content" in result
    assert "Beta content" in result
    assert "Gamma content" in result


def test_serialize_baseline_a_skips_empty_blocks():
    from benchmarks.document_package.harness import serialize_baseline_a
    blocks = [_mock_block("Real block"), _mock_block(""), _mock_block("   ")]
    result = serialize_baseline_a(blocks)
    assert result == "Real block"


def test_serialize_baseline_a_joins_with_double_newline():
    from benchmarks.document_package.harness import serialize_baseline_a
    blocks = [_mock_block("Block one"), _mock_block("Block two")]
    result = serialize_baseline_a(blocks)
    assert result == "Block one\n\nBlock two"


def test_serialize_baseline_a_serializer_version():
    assert BASELINE_A_SERIALIZER_VERSION == "1.1"


def test_baseline_a_record_fields():
    rec = BaselineARecord(
        baseline_a_text_path="baseline_a_naive.md",
        baseline_a_text_checksum="deadbeef" * 8,
        baseline_a_tokens=500,
        manifest_original_tokens=480,
        baseline_a_manifest_token_delta=20,
    )
    assert rec.baseline_a_text_path == "baseline_a_naive.md"
    assert rec.baseline_a_tokens == 500
    assert rec.manifest_original_tokens == 480
    assert rec.baseline_a_manifest_token_delta == 20
    assert rec.serializer_version == "1.1"


def test_baseline_a_manifest_delta_computed():
    baseline_a_tokens = 520
    manifest_original_tokens = 480
    delta = baseline_a_tokens - manifest_original_tokens
    rec = BaselineARecord(
        baseline_a_text_path="baseline_a_naive.md",
        baseline_a_text_checksum="x" * 64,
        baseline_a_tokens=baseline_a_tokens,
        manifest_original_tokens=manifest_original_tokens,
        baseline_a_manifest_token_delta=delta,
    )
    assert rec.baseline_a_manifest_token_delta == 40


# ── Split enforcement tests ────────────────────────────────────────────────────

def test_run_corpus_requires_explicit_split(tmp_path):
    from benchmarks.document_package.harness import run_corpus
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="split must be"):
        run_corpus(manifest_path, tmp_path / "out", split="invalid_split")


def test_held_out_run_creates_lock(tmp_path):
    from benchmarks.document_package.harness import run_corpus

    manifest_data = [
        {
            "document_id": "test-doc",
            "file_path": str(tmp_path / "nonexistent.pdf"),
            "file_type": "pdf",
            "split": "held_out",
            "categories": ["prose"],
        }
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    captures, run_dir = run_corpus(manifest_path, tmp_path / "out", split="held_out")
    lock_path = run_dir / "held_out_run_lock.json"
    assert lock_path.exists(), "held_out_run_lock.json must be written for held_out runs"


def test_held_out_lock_has_required_fields(tmp_path):
    from benchmarks.document_package.harness import run_corpus

    manifest_data = [
        {
            "document_id": "test-doc",
            "file_path": str(tmp_path / "nonexistent.pdf"),
            "file_type": "pdf",
            "split": "held_out",
            "categories": ["prose"],
        }
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    captures, run_dir = run_corpus(manifest_path, tmp_path / "out", split="held_out")
    lock_data = json.loads((run_dir / "held_out_run_lock.json").read_text())
    for field in [
        "corpus_manifest_checksum", "document_ids", "code_commit", "parser_version",
        "policy_version", "planner_version", "payload_schema_version", "tokenizer",
        "run_timestamp", "schema_version",
    ]:
        assert field in lock_data, f"HeldOutRunLock missing field: {field!r}"


def test_corpus_entry_split_filters_correctly(tmp_path):
    from benchmarks.document_package.harness import run_corpus

    manifest_data = [
        {
            "document_id": "dev-doc",
            "file_path": str(tmp_path / "dev.pdf"),
            "file_type": "pdf",
            "split": "dev",
            "categories": ["prose"],
        },
        {
            "document_id": "held-doc",
            "file_path": str(tmp_path / "held.pdf"),
            "file_type": "pdf",
            "split": "held_out",
            "categories": ["prose"],
        },
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    captures_dev, run_dir_dev = run_corpus(manifest_path, tmp_path / "out_dev", split="dev")
    assert run_dir_dev.name.startswith("dev_")
    assert not (run_dir_dev / "held_out_run_lock.json").exists()


def test_no_mixed_split_run(tmp_path):
    from benchmarks.document_package.harness import run_corpus

    manifest_data = [
        {
            "document_id": "dev-doc",
            "file_path": str(tmp_path / "dev.pdf"),
            "file_type": "pdf",
            "split": "dev",
            "categories": ["prose"],
        },
        {
            "document_id": "held-doc",
            "file_path": str(tmp_path / "held.pdf"),
            "file_type": "pdf",
            "split": "held_out",
            "categories": ["prose"],
        },
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    captures_dev, run_dir_dev = run_corpus(manifest_path, tmp_path / "out", split="dev")
    assert not (run_dir_dev / "held_out_run_lock.json").exists()


# ── Attribution tests ─────────────────────────────────────────────────────────

def test_token_savings_reconciliation():
    from benchmarks.document_package.harness import compute_token_savings_attribution
    cap = _make_capture(a=1000, b=800, c=750, d=770, e=790)
    attr = compute_token_savings_attribution(cap, [])
    computed = (
        attr.baseline_a_tokens
        - attr.repeated_furniture_removed_tokens
        - attr.duplicate_removed_tokens
        - attr.structural_omission_tokens
        - attr.caption_dedup_tokens
        - attr.table_representation_delta
        - attr.warning_added_tokens
        - attr.other_delta
        - attr.final_payload_tokens
    )
    assert computed == attr.reconciliation_residual


def test_anomaly_detection_c_vs_b():
    from benchmarks.document_package.harness import detect_anomalies
    cap = _make_capture(a=1000, b=800, c=900, d=850, e=870)
    anomalies = detect_anomalies([cap], {})
    types = [a.anomaly_type for a in anomalies]
    assert "candidate_c_exceeds_baseline_b" in types


def test_anomaly_detection_baseline_ordering():
    from benchmarks.document_package.harness import detect_anomalies
    cap = _make_capture(a=500, b=800, c=750, d=770, e=790)
    anomalies = detect_anomalies([cap], {})
    types = [a.anomaly_type for a in anomalies]
    assert "baseline_a_smaller_than_b" in types


# ── New schema tests ──────────────────────────────────────────────────────────

def test_corpus_run_summary_fields():
    s = CorpusRunSummary(
        run_id="dev_20260713",
        split="dev",
        timestamp="2026-07-13T00:00:00+00:00",
        corpus_manifest_checksum="abc",
        document_count=5,
        successful_count=4,
        failed_document_ids=["doc-fail"],
    )
    assert s.baseline_a_tokens_median == 0.0
    assert s.baseline_b_tokens_median == 0.0
    assert s.candidate_c_tokens_median == 0.0
    assert s.candidate_d_tokens_median == 0.0
    assert s.candidate_e_tokens_median == 0.0
    assert s.c_vs_a_reduction_median_pct == 0.0
    assert s.c_vs_b_reduction_median_pct == 0.0
    assert s.d_vs_a_reduction_median_pct == 0.0
    assert s.d_vs_b_reduction_median_pct == 0.0
    assert s.total_corpus_c_vs_b_reduction_pct == 0.0
    assert s.anomaly_count == 0
    assert s.schema_version == "1.0"


def test_category_summary_fields():
    cs = CategorySummary(category="prose", document_count=3)
    assert cs.baseline_a_tokens_median == 0.0
    assert cs.baseline_b_tokens_median == 0.0
    assert cs.candidate_c_tokens_median == 0.0
    assert cs.candidate_d_tokens_median == 0.0
    assert cs.candidate_e_tokens_median == 0.0
    assert cs.c_vs_b_reduction_median_pct == 0.0
    assert cs.d_vs_b_reduction_median_pct == 0.0
    assert cs.d_visual_assets_median == 0.0
    assert cs.preservation_ratio_median == 0.0


def test_anomaly_record_severity_values():
    for sev in ("info", "warning", "error"):
        rec = AnomalyRecord(
            document_id="doc-01",
            anomaly_type="test_anomaly",
            description="test",
            severity=sev,
        )
        assert rec.severity == sev

    rec_default = AnomalyRecord(
        document_id="doc-01",
        anomaly_type="test",
        description="test",
    )
    assert rec_default.severity == "info"


def test_held_out_run_lock_schema():
    lock = HeldOutRunLock(
        corpus_manifest_checksum="abc",
        document_ids=["doc-1", "doc-2"],
        code_commit="deadbeef",
        parser_version="1.0",
        policy_version="1.0",
        planner_version="1.0",
        payload_schema_version="1.0",
        tokenizer="heuristic",
        run_timestamp="2026-07-13T00:00:00+00:00",
    )
    assert lock.schema_version == "1.0"
    assert len(lock.document_ids) == 2


def test_compute_corpus_summary_with_captures():
    from benchmarks.document_package.harness import compute_corpus_summary
    caps = [
        _make_capture("doc-01", a=1000, b=800, c=750, d=770, e=790),
        _make_capture("doc-02", a=900, b=700, c=680, d=690, e=700),
    ]
    summary = compute_corpus_summary(caps, "dev_test", "dev", "abc", [])
    assert summary.successful_count == 2
    assert summary.document_count == 2
    assert summary.baseline_a_tokens_median > 0
    assert summary.baseline_b_tokens_median > 0


def test_compute_category_summaries_groups_by_category():
    from benchmarks.document_package.harness import compute_category_summaries
    caps = [
        _make_capture("doc-prose", a=1000, b=800, c=750, d=770, e=790),
        _make_capture("doc-table", a=900, b=700, c=680, d=690, e=700),
    ]
    entries = {
        "doc-prose": CorpusEntry(
            document_id="doc-prose", file_path="/x.pdf", file_type="pdf",
            split=DocumentSplit.DEV, categories=["prose"],
        ),
        "doc-table": CorpusEntry(
            document_id="doc-table", file_path="/y.pdf", file_type="pdf",
            split=DocumentSplit.DEV, categories=["table_heavy"],
        ),
    }
    summaries = compute_category_summaries(caps, entries)
    cat_names = [s.category for s in summaries]
    assert "prose" in cat_names
    assert "table_heavy" in cat_names


def test_token_savings_attribution_fields():
    attr = TokenSavingsAttribution(
        document_id="doc-01",
        baseline_a_tokens=1000,
        repeated_furniture_removed_tokens=100,
        duplicate_removed_tokens=0,
        structural_omission_tokens=50,
        caption_dedup_tokens=20,
        table_representation_delta=10,
        warning_added_tokens=5,
        other_delta=55,
        final_payload_tokens=770,
        reconciliation_residual=0,
    )
    assert attr.document_id == "doc-01"
    assert attr.baseline_a_tokens == 1000
    assert attr.final_payload_tokens == 770
    assert attr.reconciliation_residual == 0


# ── serialize_baseline_a v1.1 structural marker tests ─────────────────────────

def _mock_block_typed(content: str, btype, level=None) -> MagicMock:
    b = MagicMock()
    b.content = content
    b.type = btype
    b.level = level
    return b


def test_serialize_baseline_a_heading_produces_prefix():
    from benchmarks.document_package.harness import serialize_baseline_a
    from aksharamd.models.block import BlockType
    block = _mock_block_typed("Section Title", BlockType.HEADING, level=2)
    result = serialize_baseline_a([block])
    assert result == "## Section Title"


def test_serialize_baseline_a_heading_default_level_1():
    from benchmarks.document_package.harness import serialize_baseline_a
    from aksharamd.models.block import BlockType
    block = _mock_block_typed("Top Heading", BlockType.HEADING, level=None)
    result = serialize_baseline_a([block])
    assert result.startswith("# ")


def test_serialize_baseline_a_code_block_produces_fencing():
    from benchmarks.document_package.harness import serialize_baseline_a
    from aksharamd.models.block import BlockType
    block = _mock_block_typed("x = 1", BlockType.CODE_BLOCK)
    result = serialize_baseline_a([block])
    assert result.startswith("```")
    assert result.endswith("```")
    assert "x = 1" in result


def test_serialize_baseline_a_version_is_1_1():
    from benchmarks.document_package.harness import BASELINE_A_SERIALIZER_VERSION
    assert BASELINE_A_SERIALIZER_VERSION == "1.1"
