"""Smoke tests for the multicolumn recalibration artefacts (Issue #50, phase 1).

Verifies that the shipped machine-readable evidence files stay internally
consistent:

- the labels file is valid JSON with the expected top-level shape,
- the harness result JSON references the same commit and detector,
- the metrics summary JSON's confusion matrix arithmetic is correct.

These do NOT re-invoke the AksharaMD CLI. They validate the shipped
evidence bytes so that a future change to the labels or the harness
schema surfaces here rather than silently in a follow-up recalibration.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARKS = _REPO_ROOT / "benchmarks"

_LABELS = _BENCHMARKS / "multicolumn_recalibration_labels.json"
_HARNESS = _BENCHMARKS / "MULTICOLUMN_RECALIBRATION_2026-07-18.json"
_METRICS = _BENCHMARKS / "MULTICOLUMN_RECALIBRATION_METRICS_2026-07-18.json"


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"artefact not present: {path}")


def test_labels_file_shape() -> None:
    _skip_if_missing(_LABELS)
    with _LABELS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    assert "protocol" in doc, "labels file missing top-level 'protocol'"
    assert "labels" in doc, "labels file missing top-level 'labels'"
    protocol = doc["protocol"]
    assert protocol.get("issue") == 50
    assert protocol.get("detector") == "W_MULTICOLUMN_ORDER"
    assert protocol.get("penalty_at_evaluation") == 0
    # every label entry must carry a defined expected_positive (bool or null)
    labels = doc["labels"]
    assert len(labels) > 20, "expected at least 20 labelled assets"
    for name, entry in labels.items():
        assert "expected_positive" in entry, f"{name} missing expected_positive"
        assert entry["expected_positive"] in (True, False, None), name


def test_harness_result_matches_commit_and_detector() -> None:
    _skip_if_missing(_HARNESS)
    with _HARNESS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("commit") == "c4dfe86bb391727b5eef9ddd28bfd215d1c554c2", (
        "harness result was captured on a different commit than the labels file "
        "pins; re-run the harness against c4dfe86 or update the labels."
    )
    detector = doc.get("detector") or {}
    assert "W_MULTICOLUMN_ORDER" in (detector.get("name") or ""), (
        "harness result does not reference the W_MULTICOLUMN_ORDER detector"
    )
    assert detector.get("penalty") == 0
    assert detector.get("maturity") == "candidate"
    assert len(doc.get("results") or []) >= 30, "expected at least 30 corpus assets"


def test_metrics_confusion_matrix_arithmetic() -> None:
    _skip_if_missing(_METRICS)
    with _METRICS.open("r", encoding="utf-8") as f:
        m = json.load(f)
    tp = m["labelled_TP"]
    fp = m["labelled_FP"]
    tn = m["labelled_TN"]
    fn = m["labelled_FN"]
    # Precision, recall, F1, FPR arithmetic must be internally consistent
    def _p(a: int, b: int) -> float:
        return round(a / (a + b), 3) if (a + b) else 0.0

    assert m["precision"] == _p(tp, fp), "precision doesn't match TP/(TP+FP)"
    assert m["recall"] == _p(tp, fn), "recall doesn't match TP/(TP+FN)"
    assert m["false_positive_rate"] == _p(fp, tn), "FPR doesn't match FP/(FP+TN)"

    # F1
    p = m["precision"]
    r = m["recall"]
    expected_f1 = round(2 * p * r / (p + r), 3) if (p + r) else 0.0
    assert m["f1"] == expected_f1, "F1 doesn't match 2PR/(P+R)"

    # There must be at least one labelled positive in the corpus (multicolumn.pdf)
    # and at least one FP against which the detection-improvement candidates
    # are proposed. If those change, someone reran the harness intentionally
    # and this test should fail loudly.
    assert tp >= 1, "labels no longer contain the primary TP"
    assert fp + tn >= 10, "labelled-negative corpus shrank unexpectedly"
