"""Invariant tests for the MarkItDown adapter (Phase 2, second competitor,
Issue #68).

Same pattern as ``tests/test_pdf_benchmark_pymupdf4llm.py``: pure metric
tests + artifact tests. No AksharaMD production code imported.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.pdf_benchmark_adapters.markitdown_adapter import (  # type: ignore
    RunResult,
    _bucket,
    _estimate_tokens,
    _image_placeholder_ratio,
    _pct,
    _repeat_content_ratio,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULT = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_MARKITDOWN_2026-07-20.json"
_MANIFEST = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"
_AKSHARAMD_REVIEWS = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_human_reviews.json"
_PYMUPDF4LLM_REVIEWS = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_pymupdf4llm_human_reviews.json"


# ── Pure metric helpers ─────────────────────────────────────────────────


def test_estimate_tokens_zero_on_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_linear():
    assert _estimate_tokens("x" * 200) == 50


def test_repeat_content_ratio_zero_on_clean():
    text = " ".join(f"tok{i}" for i in range(60))
    assert _repeat_content_ratio(text, ngram=4) == 0.0


def test_repeat_content_ratio_high_on_repetition():
    text = "the quick brown fox " * 10
    assert _repeat_content_ratio(text, ngram=4) > 0.7


def test_image_placeholder_ratio_empty_returns_none():
    assert _image_placeholder_ratio("") is None


def test_pct_median():
    assert _pct([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)


def test_pct_empty():
    assert _pct([], 90) == 0.0


# ── Pure bucket / aggregate ─────────────────────────────────────────────


def _mk(**kwargs) -> RunResult:
    base: dict = {
        "asset_id": "x",
        "corpus_source": "public",
        "document_class": "native-text",
        "execution_success": True,
        "exception": "",
        "output_package_created": True,
        "content_extracted": True,
        "structurally_usable": True,
        "human_review_status": "not_reviewed",
        "human_usability": "not_reviewed",
        "human_review_evidence": "",
        "runtime_seconds": 0.1,
        "output_chars": 400,
        "non_whitespace_chars": 380,
        "estimated_tokens": 100,
        "output_size_inflation": 0.05,
        "deterministic": True,
        "page_count_pdf": 1,
        "hidden_text_layer": True,
        "hidden_text_layer_chars": 400,
        "image_placeholder_ratio": None,
        "repeat_content_ratio": 0.0,
        "near_empty_equivalent": False,
        "low_density_equivalent": False,
        "tool_signals": {},
    }
    base.update(kwargs)
    return RunResult(**base)


def test_bucket_counts_four_success_levels():
    rows = [
        _mk(asset_id="a"),
        _mk(asset_id="b", structurally_usable=False),
        _mk(asset_id="c", structurally_usable=False, content_extracted=False),
        _mk(asset_id="d", structurally_usable=False, content_extracted=False,
            output_package_created=False, execution_success=False, exception="boom"),
    ]
    b = _bucket(rows)
    assert b["n"] == 4
    assert b["execution_success_count"] == 3
    assert b["output_package_created_count"] == 3
    assert b["content_extracted_count"] == 2
    assert b["structurally_usable_count"] == 1


# ── Artifact tests ──────────────────────────────────────────────────────


def _load_result():
    if not _RESULT.exists():
        pytest.skip(f"result missing: {_RESULT}")
    with _RESULT.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_manifest():
    if not _MANIFEST.exists():
        pytest.skip(f"manifest missing: {_MANIFEST}")
    with _MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_artifact_shape():
    r = _load_result()
    for key in ("adapter_target", "adapter_target_version", "adapter_configuration",
                "manifest_source", "dependencies", "aggregate", "per_asset",
                "evaluation_semantics_notes"):
        assert key in r, f"missing key {key!r}"


def test_artifact_reports_markitdown_version():
    r = _load_result()
    assert r["adapter_target"] == "markitdown"
    assert isinstance(r["adapter_target_version"], str)
    assert r["adapter_target_version"] != "unknown"


def test_artifact_offline_configuration():
    """MarkItDown must be configured with no LLM client and no OCR/vision
    extras. This is the offline guarantee for benchmark reproducibility.
    """
    r = _load_result()
    cfg = r["adapter_configuration"]
    assert cfg["llm_client"] is None
    assert cfg["ocr_enabled"] is False
    assert cfg["vision_enabled"] is False
    assert cfg["document_intelligence_enabled"] is False


def test_artifact_declares_tool_neutral_semantics():
    r = _load_result()
    n = r["evaluation_semantics_notes"]
    assert n["aksharamd_readiness_score_used"] is False
    assert n["aksharamd_warning_codes_used"] is False
    assert n["no_cross_parser_ranking"] is True


def test_artifact_no_aksharamd_specific_fields():
    r = _load_result()
    forbidden = {"readiness_score", "quality_band", "warning_codes"}
    for row in r["per_asset"]:
        leaked = forbidden & set(row.keys())
        assert not leaked, f"{row['asset_id']}: forbidden AksharaMD field: {leaked}"


def test_artifact_same_corpus_as_aksharamd_phase1():
    r = _load_result()
    m = _load_manifest()
    eligible = {a["asset_id"] for a in m["assets"] if a["eligibility"] == "eligible"}
    result_ids = {row["asset_id"] for row in r["per_asset"]}
    assert result_ids == eligible


def test_artifact_deterministic_ordering():
    r = _load_result()
    ids = [row["asset_id"] for row in r["per_asset"]]
    assert ids == sorted(ids)


def test_artifact_four_success_levels_are_monotone():
    r = _load_result()
    for row in r["per_asset"]:
        if not row["execution_success"]:
            assert not row["output_package_created"]
        if not row["output_package_created"]:
            assert not row["content_extracted"]
        if not row["content_extracted"]:
            assert not row["structurally_usable"]


def test_artifact_headline_rates_monotone_decreasing():
    r = _load_result()
    ov = r["aggregate"]["overall"]
    assert ov["execution_success_rate"] >= ov["output_package_created_rate"]
    assert ov["output_package_created_rate"] >= ov["meaningful_content_rate"]
    assert ov["meaningful_content_rate"] >= ov["structurally_usable_rate"]


# ── Matched sample + three-way parity ───────────────────────────────────


def test_matched_sample_vs_aksharamd_present_and_consistent():
    r = _load_result()
    mp = r["aggregate"]["matched_sample_vs_aksharamd_phase1"]
    assert "error" not in mp
    total = (mp["both_usable"] + len(mp["aksharamd_only_usable"])
             + len(mp["markitdown_only_usable"]) + len(mp["neither_usable"]))
    assert total == mp["matched_sample_size"]
    assert mp["aksharamd_usable_count"] == mp["both_usable"] + len(mp["aksharamd_only_usable"])
    assert mp["markitdown_usable_count"] == mp["both_usable"] + len(mp["markitdown_only_usable"])


def test_matched_sample_vs_pymupdf4llm_present_and_consistent():
    r = _load_result()
    mp = r["aggregate"]["matched_sample_vs_pymupdf4llm"]
    assert "error" not in mp
    total = (mp["both_usable"] + len(mp["pymupdf4llm_only_usable"])
             + len(mp["markitdown_only_usable"]) + len(mp["neither_usable"]))
    assert total == mp["matched_sample_size"]


def test_three_way_paired_bucket_consistency():
    r = _load_result()
    tw = r["aggregate"]["three_way_paired_vs_aksharamd_and_pymupdf4llm"]
    assert "error" not in tw
    counts = [
        tw["all_three_usable_count"],
        tw["aksharamd_and_pymupdf4llm_only_count"],
        tw["aksharamd_and_markitdown_only_count"],
        tw["pymupdf4llm_and_markitdown_only_count"],
        tw["only_aksharamd_usable_count"],
        tw["only_pymupdf4llm_usable_count"],
        tw["only_markitdown_usable_count"],
        tw["none_usable_count"],
    ]
    assert sum(counts) == tw["three_way_matched_sample_size"]


def test_three_way_matched_size_is_intersection():
    """Sanity: three-way matched size must equal the intersection of the
    three reviewer sets (AksharaMD, PyMuPDF4LLM, MarkItDown).
    """
    if not (_AKSHARAMD_REVIEWS.exists() and _PYMUPDF4LLM_REVIEWS.exists()):
        pytest.skip("supporting reviews missing")
    r = _load_result()
    tw = r["aggregate"]["three_way_paired_vs_aksharamd_and_pymupdf4llm"]
    with _AKSHARAMD_REVIEWS.open("r", encoding="utf-8") as f:
        ax = {k for k in json.load(f) if not k.startswith("_")}
    with _PYMUPDF4LLM_REVIEWS.open("r", encoding="utf-8") as f:
        pm = {k for k in json.load(f) if not k.startswith("_")}
    md = {row["asset_id"] for row in r["per_asset"]
          if row["human_review_status"] == "reviewed"}
    expected = ax & pm & md
    assert tw["three_way_matched_sample_size"] == len(expected)
