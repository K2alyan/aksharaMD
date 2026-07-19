"""Invariant tests for the PyMuPDF4LLM adapter (Phase 2 of Issue #68).

Two kinds of tests:

1. **Pure tests** — exercise the aggregation helpers and tool-neutral
   metric predicates against synthetic dicts. No PyMuPDF4LLM call,
   no file I/O.
2. **Artifact tests** — assert the produced JSON at
   ``benchmarks/PDF_BENCHMARK_V1_PYMUPDF4LLM_2026-07-19.json`` matches
   the expected shape and the AksharaMD Phase-1 corpus identity.
   Skipped when the artifact is absent.

No AksharaMD production code is imported.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.pdf_benchmark_adapters.pymupdf4llm_adapter import (  # type: ignore
    RunResult,
    _aggregate,
    _bucket,
    _estimate_tokens,
    _image_placeholder_ratio,
    _pct,
    _repeat_content_ratio,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULT = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_PYMUPDF4LLM_2026-07-19.json"
_MANIFEST = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"


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


def test_image_placeholder_ratio_counts_markdown_images():
    text = "line one\n![](img1.png)\n![alt](img2.jpg)\nplain line"
    r = _image_placeholder_ratio(text)
    assert r == 0.5  # 2 of 4 lines are images


def test_pct_median():
    assert _pct([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)


def test_pct_empty():
    assert _pct([], 90) == 0.0


# ── Bucket + aggregate ──────────────────────────────────────────────────


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
        "runtime_seconds": 0.5,
        "output_chars": 400,
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
            output_package_created=False, execution_success=False, exception="Boom"),
    ]
    b = _bucket(rows)
    assert b["n"] == 4
    assert b["execution_success_count"] == 3
    assert b["output_package_created_count"] == 3
    assert b["content_extracted_count"] == 2
    assert b["structurally_usable_count"] == 1


def test_aggregate_execution_failures_include_exception():
    rows = [
        _mk(asset_id="a"),
        _mk(asset_id="b", execution_success=False, exception="IndexError",
            output_package_created=False, content_extracted=False,
            structurally_usable=False),
    ]
    ag = _aggregate(rows)
    assert len(ag["execution_failures"]) == 1
    (f,) = ag["execution_failures"]
    assert f["asset_id"] == "b"
    assert "IndexError" in f["exception"]


def test_aggregate_content_and_structural_failures_separate():
    rows = [
        _mk(asset_id="ok"),
        _mk(asset_id="near_empty", content_extracted=False,
            structurally_usable=False, near_empty_equivalent=True,
            output_chars=20),
        _mk(asset_id="damaged", structurally_usable=False,
            repeat_content_ratio=0.85),
    ]
    ag = _aggregate(rows)
    assert len(ag["content_failures"]) == 1
    assert ag["content_failures"][0]["asset_id"] == "near_empty"
    assert len(ag["structural_failures"]) == 1
    assert ag["structural_failures"][0]["asset_id"] == "damaged"


def test_aggregate_human_review_counts():
    rows = [
        _mk(asset_id="a", human_review_status="reviewed", human_usability="usable"),
        _mk(asset_id="b", human_review_status="reviewed", human_usability="unusable"),
        _mk(asset_id="c"),
    ]
    ag = _aggregate(rows)
    assert ag["overall"]["human_reviewed_count"] == 2
    assert ag["overall"]["human_usable_count"] == 1
    assert ag["overall"]["human_unusable_count"] == 1
    assert ag["overall"]["human_usable_rate"] == pytest.approx(0.5)


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
    for key in ("adapter_target", "adapter_target_version", "manifest_source",
                "dependencies", "aggregate", "per_asset",
                "evaluation_semantics_notes"):
        assert key in r, f"missing key {key!r}"


def test_artifact_declares_tool_neutral_semantics():
    r = _load_result()
    n = r["evaluation_semantics_notes"]
    assert n["aksharamd_readiness_score_used"] is False
    assert n["aksharamd_warning_codes_used"] is False
    assert n["no_cross_parser_ranking"] is True


def test_artifact_evaluates_same_corpus_as_aksharamd_phase1():
    r = _load_result()
    m = _load_manifest()
    eligible_ids = {a["asset_id"] for a in m["assets"] if a["eligibility"] == "eligible"}
    result_ids = {row["asset_id"] for row in r["per_asset"]}
    assert result_ids == eligible_ids, (
        "PyMuPDF4LLM adapter must evaluate the same 45 eligible assets "
        "as the AksharaMD Phase 1 baseline"
    )


def test_artifact_deterministic_ordering():
    r = _load_result()
    ids = [row["asset_id"] for row in r["per_asset"]]
    assert ids == sorted(ids)


def test_artifact_four_success_levels_are_monotone():
    """execution >= output_package >= content_extracted >= structurally_usable
    must hold on every per-asset row."""
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


def test_artifact_no_aksharamd_specific_fields():
    """The per-asset record must NOT carry AksharaMD-specific keys
    like readiness_score, quality_band, or warning_codes."""
    r = _load_result()
    forbidden = {"readiness_score", "quality_band", "warning_codes"}
    for row in r["per_asset"]:
        leaked = forbidden & set(row.keys())
        assert not leaked, f"{row['asset_id']}: forbidden AksharaMD field: {leaked}"


def test_artifact_reports_pymupdf4llm_version():
    r = _load_result()
    assert r["adapter_target"] == "pymupdf4llm"
    assert isinstance(r["adapter_target_version"], str)
    assert r["adapter_target_version"] != "unknown"


def test_artifact_records_execution_failure_when_present():
    """PyMuPDF4LLM's known execution failure on this corpus is the
    unreadablemetadata.pdf IndexError. The failure MUST be captured as
    a non-empty exception field and NOT silently succeeded."""
    r = _load_result()
    fails = [row for row in r["per_asset"] if not row["execution_success"]]
    # If no execution failure was observed, that's fine (corpus/tool
    # may have changed). If any was observed, its exception field must
    # be populated.
    for row in fails:
        assert row["exception"], (
            f"{row['asset_id']}: execution_success=False but exception is empty"
        )
