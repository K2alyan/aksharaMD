"""Invariant tests for the PDF Benchmark v1 Phase 1 baseline harness
(Issue #68, follow-up to #50 pause).

Two kinds of tests:

1. **Pure tests** — exercise the corpus resolver, classifier, and
   aggregation helpers against synthetic inputs. No aksharamd
   compilation.
2. **Artifact tests** — assert the produced manifest / result JSON at
   ``benchmarks/pdf_benchmark_v1_manifest.json`` and
   ``benchmarks/PDF_BENCHMARK_V1_BASELINE_2026-07-19.json`` match
   the expected shape and top-line invariants. Skipped when the
   artifacts are absent.

No production code is imported.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.pdf_benchmark_v1 import (  # type: ignore
    Asset,
    RunResult,
    _aggregate,
    _class_counts,
    _classify_parsebench,
    _classify_public,
    _corpus_counts,
    _estimate_tokens,
    _pct,
    _repeat_content_ratio,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"
_RESULT = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_BASELINE_2026-07-19.json"


# ── Pure: token estimate ────────────────────────────────────────────────


def test_estimate_tokens_zero_on_empty_string():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_scales_roughly_with_chars():
    # 20 characters / 4 chars-per-token ≈ 5 tokens
    assert _estimate_tokens("x" * 20) == 5
    # 400 characters ≈ 100 tokens
    assert _estimate_tokens("x" * 400) == 100


# ── Pure: repeat-content ratio ──────────────────────────────────────────


def test_repeat_content_ratio_zero_on_clean_text():
    text = " ".join(f"unique{i}" for i in range(50))
    assert _repeat_content_ratio(text, ngram=4) == 0.0


def test_repeat_content_ratio_positive_on_repetition():
    text = "the quick brown fox " * 6  # every 4-gram is duplicated
    ratio = _repeat_content_ratio(text, ngram=4)
    assert ratio > 0.5


def test_repeat_content_ratio_zero_on_short_text():
    # Too few tokens to form multiple n-grams.
    assert _repeat_content_ratio("one two three", ngram=4) == 0.0


# ── Pure: percentiles ───────────────────────────────────────────────────


def test_pct_median():
    assert _pct([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)


def test_pct_edges():
    assert _pct([1.0, 2.0, 3.0], 0) == pytest.approx(1.0)
    assert _pct([1.0, 2.0, 3.0], 100) == pytest.approx(3.0)


def test_pct_empty_returns_zero():
    assert _pct([], 50) == 0.0


# ── Pure: classifiers ───────────────────────────────────────────────────


def test_classify_public_uses_labels_when_present():
    labels = {"foo.pdf": {"layout": "multicolumn"}}
    assert _classify_public("foo.pdf", labels) == "multicolumn"
    labels2 = {"foo.pdf": {"layout": "image_only"}}
    assert _classify_public("foo.pdf", labels2) == "image-only"
    labels3 = {"foo.pdf": {"layout": "encrypted"}}
    assert _classify_public("foo.pdf", labels3) == "malformed"


def test_classify_public_falls_back_on_name_heuristics():
    assert _classify_public("018-base64-image/base64image.pdf", {}) == "image-only"
    assert _classify_public("015-arabic/arabic.pdf", {}) == "multilingual"
    assert _classify_public("multicolumn.pdf", {}) == "multicolumn"
    assert _classify_public("010-forms/form.pdf", {}) == "malformed"
    assert _classify_public("some-random.pdf", {}) == "native-text"


def test_classify_parsebench_by_defect_kind_and_id():
    assert _classify_parsebench({"id": "letter3", "defect_kind": "non-multicolumn"}) == "image-only"
    assert _classify_parsebench({"id": "3colpres", "defect_kind": "mixed"}) == "multicolumn"
    assert _classify_parsebench({"id": "japanese_case", "defect_kind": "non-multicolumn"}) == "image-only"
    assert _classify_parsebench({"id": "text_dense__de", "defect_kind": "block-level"}) == "multilingual"
    assert _classify_parsebench({"id": "strikeUnderline", "defect_kind": "block-level"}) == "native-text"


# ── Pure: corpus + class counts ─────────────────────────────────────────


def _mk_asset(**kwargs) -> Asset:
    base: dict = {
        "asset_id": "x",
        "corpus_source": "public",
        "path_strategy": "on-disk",
        "pdf_path": Path("."),
        "sha256": "0" * 64,
        "size_bytes": 1,
        "page_count": 1,
        "document_class": "native-text",
        "ground_truth_available": False,
        "licensing": "",
        "eligibility": "eligible",
    }
    base.update(kwargs)
    return Asset(**base)


def test_corpus_counts_aggregate():
    assets = [
        _mk_asset(asset_id="a", corpus_source="public"),
        _mk_asset(asset_id="b", corpus_source="public"),
        _mk_asset(asset_id="c", corpus_source="parsebench"),
    ]
    assert _corpus_counts(assets) == {"public": 2, "parsebench": 1}


def test_class_counts_aggregate():
    assets = [
        _mk_asset(document_class="native-text"),
        _mk_asset(document_class="multicolumn"),
        _mk_asset(document_class="multicolumn"),
    ]
    assert _class_counts(assets) == {"native-text": 1, "multicolumn": 2}


# ── Pure: aggregation ───────────────────────────────────────────────────


def _mk_result(**kwargs) -> RunResult:
    base: dict = {
        "asset_id": "x",
        "corpus_source": "public",
        "document_class": "native-text",
        "execution_success": True,
        "exit_code": 0,
        "output_package_created": True,
        "content_extracted": True,
        "structurally_usable": True,
        "human_review_status": "not_reviewed",
        "human_usability": "not_reviewed",
        "human_review_evidence": "",
        "runtime_seconds": 1.0,
        "output_chars": 400,
        "estimated_tokens": 100,
        "output_size_inflation": 0.05,
        "deterministic": True,
        "page_count_pdf": 1,
        "page_count_output": 1,
        "missing_pages": False,
        "hidden_text_layer": True,
        "hidden_text_layer_chars": 400,
        "image_placeholder_ratio": None,
        "readiness_score": 85,
        "quality_band": "HIGH",
        "warning_codes": [],
        "informational": [],
        "repeat_content_ratio": 0.0,
        "low_text_density": False,
        "near_empty_output": False,
        "ocr_warning_emitted": False,
        "stdout_head": "",
        "stderr_head": "",
        "fidelity_flags": {},
    }
    base.update(kwargs)
    return RunResult(**base)


def test_aggregate_counts_success_and_bands():
    results = [
        _mk_result(asset_id="a", quality_band="HIGH"),
        _mk_result(asset_id="b", quality_band="OK"),
        _mk_result(asset_id="c", quality_band="HIGH", execution_success=False, exit_code=1,
                   output_package_created=False, content_extracted=False, structurally_usable=False),
    ]
    ag = _aggregate(results)
    assert ag["overall"]["n"] == 3
    assert ag["overall"]["execution_success_count"] == 2
    assert ag["overall"]["execution_success_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert ag["overall"]["quality_band_distribution"] == {"HIGH": 2, "OK": 1}


def test_aggregate_records_execution_failures_separately():
    results = [
        _mk_result(asset_id="a"),
        _mk_result(asset_id="b", execution_success=False, exit_code=1,
                   output_package_created=False, content_extracted=False,
                   structurally_usable=False, stderr_head="boom"),
    ]
    ag = _aggregate(results)
    assert len(ag["execution_failures"]) == 1
    (f,) = ag["execution_failures"]
    assert f["asset_id"] == "b"
    assert f["exit_code"] == 1
    assert "boom" in f["stderr_head"]


def test_aggregate_by_document_class_isolates_classes():
    results = [
        _mk_result(asset_id="a", document_class="multicolumn"),
        _mk_result(asset_id="b", document_class="multicolumn"),
        _mk_result(asset_id="c", document_class="image-only", ocr_warning_emitted=True),
    ]
    ag = _aggregate(results)
    assert ag["by_document_class"]["multicolumn"]["n"] == 2
    assert ag["by_document_class"]["image-only"]["ocr_warning_count"] == 1


def test_aggregate_reports_content_and_structural_failures():
    """Documents that ran but produced no meaningful content go into
    content_failures; those with content but not structurally usable
    go into structural_failures."""
    results = [
        _mk_result(asset_id="ok"),
        _mk_result(asset_id="near_empty", content_extracted=False,
                   structurally_usable=False, near_empty_output=True, output_chars=40),
        _mk_result(asset_id="damaged", structurally_usable=False,
                   repeat_content_ratio=0.85),
    ]
    ag = _aggregate(results)
    assert len(ag["content_failures"]) == 1
    assert ag["content_failures"][0]["asset_id"] == "near_empty"
    assert len(ag["structural_failures"]) == 1
    assert ag["structural_failures"][0]["asset_id"] == "damaged"


def test_aggregate_records_human_review_metrics():
    results = [
        _mk_result(asset_id="a", human_review_status="reviewed", human_usability="usable"),
        _mk_result(asset_id="b", human_review_status="reviewed", human_usability="materially_damaged"),
        _mk_result(asset_id="c"),  # not reviewed
    ]
    ag = _aggregate(results)
    assert ag["overall"]["human_reviewed_count"] == 2
    assert ag["overall"]["human_usable_count"] == 1
    assert ag["overall"]["human_materially_damaged_count"] == 1
    assert ag["overall"]["human_usable_rate"] == pytest.approx(0.5)


# ── Artifact tests (skipped if artifacts absent) ────────────────────────


def _load_manifest():
    if not _MANIFEST.exists():
        pytest.skip(f"manifest missing: {_MANIFEST}")
    with _MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_result():
    if not _RESULT.exists():
        pytest.skip(f"result missing: {_RESULT}")
    with _RESULT.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_shape():
    m = _load_manifest()
    for key in ("harness_version", "commit_under_evaluation", "python_version",
                "asset_count_total", "asset_count_eligible", "assets",
                "corpus_counts", "class_counts"):
        assert key in m, f"manifest missing key {key!r}"


def test_manifest_deterministic_asset_ordering():
    """Assets must be ordered by id (deterministic across re-runs)."""
    m = _load_manifest()
    ids = [a["asset_id"] for a in m["assets"]]
    assert ids == sorted(ids), "manifest assets are not sorted by id"


def test_manifest_covers_both_corpora():
    m = _load_manifest()
    sources = {a["corpus_source"] for a in m["assets"]}
    assert {"public", "parsebench"} <= sources


def test_result_carries_all_eligible_assets():
    m = _load_manifest()
    r = _load_result()
    eligible = [a for a in m["assets"] if a["eligibility"] == "eligible"]
    per_asset = r["per_asset"]
    # Result rows are one per eligible asset; identical id set.
    assert {a["asset_id"] for a in eligible} == {row["asset_id"] for row in per_asset}


def test_result_ordering_is_deterministic():
    r = _load_result()
    ids = [row["asset_id"] for row in r["per_asset"]]
    assert ids == sorted(ids)


def test_result_aggregate_matches_per_asset_counts():
    r = _load_result()
    per_asset = r["per_asset"]
    ov = r["aggregate"]["overall"]
    assert ov["n"] == len(per_asset)
    assert ov["execution_success_count"] == sum(1 for row in per_asset if row["execution_success"])
    assert ov["content_extracted_count"] == sum(1 for row in per_asset if row["content_extracted"])
    assert ov["structurally_usable_count"] == sum(1 for row in per_asset if row["structurally_usable"])


def test_result_records_no_network_or_scoring_change():
    """Sanity guard for the constraint header — commit id populated,
    dependency versions populated."""
    r = _load_result()
    assert isinstance(r.get("commit_under_evaluation"), str) and len(r["commit_under_evaluation"]) >= 7
    deps = r.get("dependencies") or {}
    assert "aksharamd" in deps


def test_baseline_result_no_execution_regression():
    """Lock the process-level reliability floor. Execution rate must
    stay near 100% (CLI does not crash).
    """
    r = _load_result()
    ov = r["aggregate"]["overall"]
    assert ov["execution_success_rate"] >= 0.98, (
        f"execution success rate dropped to {ov['execution_success_rate']}; "
        "investigate regression before merging."
    )


def test_baseline_headline_metrics_are_separated():
    """The four success levels must all appear in the aggregate. This
    guards against a regression where content and execution are silently
    collapsed back into one number.
    """
    r = _load_result()
    ov = r["aggregate"]["overall"]
    for k in ("execution_success_rate", "output_package_created_rate",
              "meaningful_content_rate", "structurally_usable_rate"):
        assert k in ov, f"headline metric {k!r} missing from aggregate"
    # The four rates must form a non-increasing sequence: content
    # extraction cannot exceed execution; structural usability cannot
    # exceed content.
    assert ov["execution_success_rate"] >= ov["meaningful_content_rate"]
    assert ov["meaningful_content_rate"] >= ov["structurally_usable_rate"]


def test_baseline_image_only_audit_present_for_every_image_only_asset():
    """Every image-only asset in the manifest must appear in the
    per-asset result, and each must carry hidden_text_layer + output_chars
    fields for the audit table.
    """
    m = _load_manifest()
    r = _load_result()
    img_ids = {a["asset_id"] for a in m["assets"]
               if a["document_class"] == "image-only" and a["eligibility"] == "eligible"}
    per_asset = {row["asset_id"]: row for row in r["per_asset"]}
    for aid in img_ids:
        assert aid in per_asset, f"image-only asset {aid!r} missing from result"
        row = per_asset[aid]
        for key in ("hidden_text_layer", "hidden_text_layer_chars", "output_chars",
                    "warning_codes", "quality_band"):
            assert key in row, f"{aid}: audit field {key!r} missing"


_PARITY = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_PARITY_AUDIT_2026-07-19.json"


def test_parity_audit_present_and_matching():
    """The known-case parity audit must have been run and all cases
    must match the shipped-detector output. Skipped when the audit JSON
    is absent (e.g., a developer running only the pure tests offline).
    """
    if not _PARITY.exists():
        pytest.skip(f"parity audit artifact missing: {_PARITY}")
    with _PARITY.open("r", encoding="utf-8") as f:
        p = json.load(f)
    assert p["all_match"] is True, (
        f"parity audit reported drift: {[r for r in p['audit_rows'] if r.get('drift')]}"
    )
    # Every asset in the known-case list must have been audited.
    audited_ids = {row["asset_id"] for row in p["audit_rows"]}
    expected_ids = set(p["known_cases"])
    assert audited_ids == expected_ids


def test_baseline_human_review_sample_is_stratified():
    """The stratified human-review sample must cover every primary
    slice with at least one review (the reviewer supplies the JSON).
    """
    r = _load_result()
    reviewed = [row for row in r["per_asset"] if row["human_review_status"] == "reviewed"]
    if not reviewed:
        pytest.skip("no human reviews supplied in this run")
    covered = {row["document_class"] for row in reviewed}
    # Every class that has AT LEAST one reviewed asset must show up.
    manifest_classes = {row["document_class"] for row in r["per_asset"]}
    # Require coverage of every class present in the corpus.
    assert covered == manifest_classes, (
        f"reviewer sample covers {covered} but corpus has {manifest_classes}"
    )
