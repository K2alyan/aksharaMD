"""Smoke tests for the multicolumn candidate-replay artefacts (Issue #50 phase 2).

Validates that the shipped replay JSON and its supporting evidence
remain internally consistent so that a future change to the harness or
the rule definitions surfaces here rather than silently in a follow-up
implementation PR.

These tests do NOT re-invoke the AksharaMD CLI or the replay script.
They validate the bytes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARKS = _REPO_ROOT / "benchmarks"

_REPLAY = _BENCHMARKS / "MULTICOLUMN_CANDIDATE_REPLAY_2026-07-18.json"
_REPLAY_MD = _BENCHMARKS / "MULTICOLUMN_CANDIDATE_REPLAY_2026-07-18.md"
_PHASE_1_HARNESS = _BENCHMARKS / "MULTICOLUMN_RECALIBRATION_2026-07-18.json"


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"artefact not present: {path}")


def test_replay_json_shape() -> None:
    _skip_if_missing(_REPLAY)
    with _REPLAY.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for key in (
        "commit_under_evaluation",
        "rules",
        "confidence_caveats",
        "document_level_metrics",
        "page_level_metrics",
        "per_document",
        "per_page",
        "changed_document_decisions_vs_baseline",
        "changed_page_decisions_vs_baseline",
    ):
        assert key in doc, f"replay JSON missing top-level '{key}'"
    assert doc["commit_under_evaluation"] == "c4dfe86bb391727b5eef9ddd28bfd215d1c554c2"
    assert "baseline" in doc["rules"] and "C3+C4" in doc["rules"]


def test_replay_document_metrics_arithmetic() -> None:
    _skip_if_missing(_REPLAY)
    with _REPLAY.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    rules_seen = set()
    for m in doc["document_level_metrics"]:
        rules_seen.add(m["rule"])
        tp, fp, tn, fn = m["TP"], m["FP"], m["TN"], m["FN"]

        def _p(a: int, b: int) -> float:
            return round(a / (a + b), 3) if (a + b) else 0.0

        assert m["precision"] == _p(tp, fp), f"{m['rule']} precision"
        assert m["recall"] == _p(tp, fn), f"{m['rule']} recall"
        assert m["false_positive_rate"] == _p(fp, tn), f"{m['rule']} FPR"
        p = m["precision"]
        r = m["recall"]
        expected_f1 = round(2 * p * r / (p + r), 3) if (p + r) else 0.0
        assert m["f1"] == expected_f1
    assert rules_seen == {"baseline", "C3", "C4", "C3+C4"}, (
        f"unexpected rule set in replay metrics: {rules_seen}"
    )


def test_replay_no_candidate_changes_document_decisions() -> None:
    """Phase 2 lock-in: on the current public corpus, C3, C4, and C3+C4 all
    leave every document-level decision identical to baseline. If a future
    corpus change makes any candidate move a document from FP to TN (or
    vice versa) that is a meaningful signal worth surfacing.
    """
    _skip_if_missing(_REPLAY)
    with _REPLAY.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for rule_name, changed in doc["changed_document_decisions_vs_baseline"].items():
        assert changed == [], (
            f"{rule_name} now moves {len(changed)} document(s) vs baseline — "
            f"re-run the replay and refresh the report."
        )


def test_replay_c3_and_c4_and_c3c4_reduce_page_firings() -> None:
    """Sanity: every candidate must silence at least one page compared to
    baseline. If a candidate silences none, either the rule is wrong or the
    corpus no longer contains the FP class this replay measured against.
    """
    _skip_if_missing(_REPLAY)
    with _REPLAY.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    baseline_total = next(m["page_firings_total"] for m in doc["page_level_metrics"] if m["rule"] == "baseline")
    for rule in ("C3", "C4", "C3+C4"):
        total = next(m["page_firings_total"] for m in doc["page_level_metrics"] if m["rule"] == rule)
        assert total < baseline_total, (
            f"{rule} did not reduce page firings vs baseline "
            f"(baseline={baseline_total}, {rule}={total})"
        )


def test_replay_preserves_the_labelled_TP_under_all_rules() -> None:
    """The single labelled positive document in the public corpus must
    still fire under every candidate rule. If any candidate silences it,
    the candidate is unshippable regardless of its page-level noise
    reduction.
    """
    _skip_if_missing(_REPLAY)
    with _REPLAY.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    tp_rows = [r for r in doc["per_document"] if r["asset"] == "multicolumn.pdf"]
    assert tp_rows, "multicolumn.pdf missing from replay per_document"
    (row,) = tp_rows
    for rule in ("baseline", "C3", "C4", "C3+C4"):
        assert row[f"{rule}_warn_doc"], (
            f"{rule} silenced the labelled TP multicolumn.pdf; regression."
        )


def test_replay_markdown_reflects_confidence_caveats() -> None:
    """The report must prominently document that the corpus has only one
    labelled positive and that recall = 1.000 is coincidental. If the
    body no longer mentions these limitations, downstream readers may
    misinterpret the metrics.
    """
    _skip_if_missing(_REPLAY_MD)
    body = _REPLAY_MD.read_text(encoding="utf-8")
    required_phrases = [
        "only one labelled positive",
        "Recall",
        "coincidental",
        "ParseBench",
        "SCORING_POLICY_VERSION",
    ]
    for phrase in required_phrases:
        assert phrase in body, (
            f"replay report is missing required caveat phrase: {phrase!r}"
        )
