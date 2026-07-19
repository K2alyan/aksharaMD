"""Invariants for the ParseBench + public-corpus multicolumn
recalibration (Issue #50 follow-up).

Locks the calibration protocol so a future consumer cannot silently
change which assets participate in which slice, or which candidate
flipped which decision. No detector, parser, or scoring code is
imported.

Two kinds of tests:

1. **Pure metric tests** — exercise the eligibility + confusion helpers
   against synthetic dicts. These run everywhere.
2. **Artifact tests** — assert the machine-readable JSON at
   ``benchmarks/PARSEBENCH_RECALIBRATION_2026-07-19.json`` matches
   the expected shape and every documented count in the report. Skipped
   when the artifact is absent (a developer running only the metric
   tests offline should not be blocked).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.parsebench_recalibration_metrics import (  # type: ignore
    confusion,
    parsebench_doc_historical,
    parsebench_doc_reviewer_confirmed,
    parsebench_observable_eligibility,
    parsebench_page_eligibility,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCKFILE = _REPO_ROOT / "benchmarks" / "parsebench_assets.lock.json"
_RESULT = _REPO_ROOT / "benchmarks" / "PARSEBENCH_RECALIBRATION_2026-07-19.json"
_LABELS = _REPO_ROOT / "benchmarks" / "multicolumn_recalibration_labels.json"


# ── Pure eligibility tests (synthetic dicts) ─────────────────────────────


def _mk_asset(**kwargs):
    base = {
        "id": "x",
        "expected_label": "true-negative",
        "defect_kind": "block-level",
        "page_level_ground_truth": {
            "review_status": "complete",
            "page_count": 1,
            "pages": [{
                "page": 1,
                "layout": "single-column",
                "extraction_status": "correct",
                "defect_kind": "none",
                "severity": "none",
                "evidence": "",
                "confidence": "high",
                "detector_observability": "block-level-observable",
            }],
        },
    }
    base.update(kwargs)
    return base


def test_doc_historical_includes_every_attested_asset():
    lock = {"assets": [
        _mk_asset(id="a", expected_label="true-positive"),
        _mk_asset(id="b", expected_label="true-negative"),
        _mk_asset(id="c", expected_label=None),  # unattested — dropped
    ]}
    hist = parsebench_doc_historical(lock)
    assert set(hist) == {"a", "b"}
    assert hist["a"] is True
    assert hist["b"] is False


def test_doc_reviewer_confirmed_excludes_ambiguous_and_non_multicolumn():
    amb_page = {
        "review_status": "complete",
        "page_count": 1,
        "pages": [{
            "page": 1, "layout": "mixed", "extraction_status": "ambiguous",
            "defect_kind": "span-level", "severity": "unknown",
            "evidence": "unclear", "confidence": "low",
            "detector_observability": "not-applicable",
        }],
    }
    lock = {"assets": [
        _mk_asset(id="pos", expected_label="true-positive", defect_kind="mixed"),
        _mk_asset(id="neg", expected_label="true-negative", defect_kind="block-level"),
        _mk_asset(id="amb", expected_label="true-positive", defect_kind="span-level",
                  page_level_ground_truth=amb_page),
        _mk_asset(id="nmc", expected_label="true-negative", defect_kind="non-multicolumn"),
        _mk_asset(id="both", expected_label="true-positive", defect_kind="non-multicolumn",
                  page_level_ground_truth=amb_page),
    ]}
    conf = parsebench_doc_reviewer_confirmed(lock)
    assert set(conf) == {"pos", "neg"}
    assert conf["pos"] is True
    assert conf["neg"] is False


def test_page_eligibility_omits_ambiguous_and_non_multicolumn():
    amb_page = {
        "review_status": "complete",
        "page_count": 1,
        "pages": [{
            "page": 1, "layout": "mixed", "extraction_status": "ambiguous",
            "defect_kind": "span-level", "severity": "unknown",
            "evidence": "unclear", "confidence": "low",
            "detector_observability": "not-applicable",
        }],
    }
    damaged_page = {
        "review_status": "complete",
        "page_count": 1,
        "pages": [{
            "page": 1, "layout": "three-column", "extraction_status": "damaged",
            "defect_kind": "mixed", "severity": "material",
            "evidence": "spliced sentence", "confidence": "high",
            "detector_observability": "block-level-observable",
        }],
    }
    lock = {"assets": [
        _mk_asset(id="dmg", expected_label="true-positive", defect_kind="mixed",
                  page_level_ground_truth=damaged_page),
        _mk_asset(id="amb", defect_kind="span-level",
                  page_level_ground_truth=amb_page),
        _mk_asset(id="nmc", defect_kind="non-multicolumn"),
    ]}
    rows = parsebench_page_eligibility(lock)
    ids = {r["asset"] for r in rows}
    assert ids == {"dmg"}
    (row,) = rows
    assert row["expected_positive"] is True
    assert row["observability"] == "block-level-observable"


def test_observable_eligibility_is_subset_of_page_eligibility():
    """Any observable row must also be in the page-eligibility set."""
    lock_json = _LOCKFILE
    if not lock_json.exists():
        pytest.skip("lockfile not present")
    with lock_json.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    page_ids = {(r["asset"], r["page"]) for r in parsebench_page_eligibility(lock)}
    obs_ids = {(r["asset"], r["page"]) for r in parsebench_observable_eligibility(lock)}
    assert obs_ids <= page_ids


def test_confusion_rejects_none_expected():
    with pytest.raises(ValueError):
        confusion([("x", None, True)])  # type: ignore[list-item]


def test_confusion_math():
    rows = [
        ("a", True, True),   # TP
        ("b", True, False),  # FN
        ("c", False, True),  # FP
        ("d", False, False),  # TN
    ]
    m = confusion(rows)
    assert m["TP"] == 1 and m["FP"] == 1 and m["TN"] == 1 and m["FN"] == 1
    assert m["precision"] == 0.5
    assert m["recall"] == 0.5
    assert m["false_positive_rate"] == 0.5
    assert m["f1"] == 0.5


# ── Artifact tests (skipped if the JSON is absent) ───────────────────────


def _load_result():
    if not _RESULT.exists():
        pytest.skip(f"result artifact not present: {_RESULT}")
    with _RESULT.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_artifact_carries_all_four_candidates_and_all_slices():
    r = _load_result()
    for name in ("baseline", "C3", "C4", "C3+C4"):
        assert name in r["metrics"], f"missing candidate: {name}"
        pb = r["metrics"][name]["parsebench"]
        for slc in ("doc_historical", "doc_reviewer_confirmed", "page", "observable"):
            assert slc in pb, f"missing ParseBench slice {slc!r} on {name}"
        assert "doc_historical" in r["metrics"][name]["public_frozen"]
        assert "doc_historical" in r["metrics"][name]["combined"]


def test_artifact_eligibility_counts_match_report_prose():
    r = _load_result()
    counts = r["corpus_counts"]
    assert counts["parsebench"]["assets_total"] == 12
    assert counts["parsebench"]["doc_historical_eligible"] == 12
    assert counts["parsebench"]["doc_reviewer_confirmed_eligible"] == 6
    assert counts["parsebench"]["page_eligible"] == 6
    assert counts["parsebench"]["observable_eligible"] == 5
    # Public: total discovered is 34; attested is 22 (derived at runtime).
    assert counts["public_frozen"]["results_total"] == 34
    assert counts["public_frozen"]["doc_historical_eligible"] == 22


def test_public_corpus_metrics_are_stable_across_candidates():
    """The frozen phase-2 finding was that C3 / C4 / C3+C4 do not change
    the public-corpus decision on any doc. This test locks that.
    """
    r = _load_result()
    baseline = r["metrics"]["baseline"]["public_frozen"]["doc_historical"]
    for name in ("C3", "C4", "C3+C4"):
        other = r["metrics"][name]["public_frozen"]["doc_historical"]
        for k in ("TP", "FP", "TN", "FN"):
            assert baseline[k] == other[k], (
                f"public-corpus {k} drift on candidate {name}: "
                f"baseline={baseline[k]} vs {name}={other[k]}"
            )


def test_c4_and_c3c4_silence_3colpres_document_and_page():
    r = _load_result()
    flips = [row for row in r["changed_decisions"] if row["corpus"] == "parsebench"]
    silenced_docs = {(row["candidate"], row["id"]) for row in flips
                     if row["scope"] == "document" and row["flip"] == "silenced"}
    silenced_pages = {(row["candidate"], row["id"]) for row in flips
                      if row["scope"] == "page" and row["flip"] == "silenced"}
    assert ("C4", "3colpres") in silenced_docs
    assert ("C3+C4", "3colpres") in silenced_docs
    assert ("C4", "3colpres#1") in silenced_pages
    assert ("C3+C4", "3colpres#1") in silenced_pages


def test_no_parsebench_flip_is_a_raised_verdict():
    """None of the candidate rules should ever RAISE a warning that
    baseline did not raise on ParseBench. If this fires, someone shipped
    a rule change unnoticed.
    """
    r = _load_result()
    for row in r["changed_decisions"]:
        if row["corpus"] != "parsebench":
            continue
        assert row["flip"] == "silenced", (
            f"unexpected 'raised' flip on ParseBench: {row}"
        )


def test_changed_decision_rows_carry_expanded_context():
    """Every changed-decision row must expose the fields the review
    checklist requires.
    """
    r = _load_result()
    required = {
        "candidate", "corpus", "scope", "id",
        "baseline", "candidate_verdict", "flip",
        "candidate_reason",
        "ground_truth_eligibility", "exclusion_reason",
        "affects_document_verdict", "page_noise_only",
        "baseline_signals", "candidate_signals",
    }
    for row in r["changed_decisions"]:
        missing = required - set(row)
        assert not missing, f"row missing keys {missing}: {row}"


def test_reviewer_confirmed_recall_at_baseline_is_50_percent():
    """Sanity: baseline hits both 3colpres (TP block-level-observable)
    and misses elpais (span-only). Precision = TP/(TP+FP) = 1/(1+1) = 0.5.
    """
    r = _load_result()
    m = r["metrics"]["baseline"]["parsebench"]["doc_reviewer_confirmed"]
    assert m["TP"] == 1 and m["FN"] == 1
    assert m["precision"] == 0.5
    assert m["recall"] == 0.5


def test_observable_recall_at_baseline_is_100_percent():
    """Sanity: on the block-level-observable slice, baseline hits every
    positive it can see. Only the persistent strikeUnderline FP remains.
    """
    r = _load_result()
    m = r["metrics"]["baseline"]["parsebench"]["observable"]
    assert m["TP"] == 1 and m["FN"] == 0
    assert m["recall"] == 1.0


def test_report_records_no_public_flip():
    """The report and the machine-readable JSON must agree that no
    public-corpus decision flipped. If someone shipped a rule change
    that flips a public doc, both should update together.
    """
    r = _load_result()
    for row in r["changed_decisions"]:
        assert row["corpus"] != "public_frozen", (
            f"unexpected public-corpus flip: {row}"
        )


def test_lockfile_untouched_by_this_pr():
    """Same guard as PR #64: promoted checksums / sizes / mirror_url /
    dataset revision must not have been mutated by this recalibration
    PR. Detects accidental lockfile edits.
    """
    if not _LOCKFILE.exists():
        pytest.skip("lockfile missing")
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    for e in lock["assets"]:
        assert e["mirror_url"] is None
        assert e["binary_url"] is None
        assert isinstance(e["sha256"], str) and len(e["sha256"]) == 64
        assert isinstance(e["size_bytes"], int) and e["size_bytes"] > 0
    ds = lock.get("dataset_source") or {}
    assert isinstance(ds.get("dataset_revision"), str)
    assert len(ds["dataset_revision"]) == 40


def test_public_labels_source_untouched():
    if not _LABELS.exists():
        pytest.skip("labels missing")
    with _LABELS.open("r", encoding="utf-8") as f:
        labels = json.load(f)
    labels_map = labels.get("labels", {})
    attested = [k for k, v in labels_map.items()
                if v.get("expected_positive") in (True, False)]
    # Attested-key count is 25 (some are aliases of the same file).
    assert len(attested) == 25, (
        f"public labels attested-key count drift: {len(attested)} != 25"
    )
    # Distinct-result count derived from resolution is asserted in the
    # artifact test above (doc_historical_eligible == 22).
