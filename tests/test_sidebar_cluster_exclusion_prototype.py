"""Invariant tests for the sidebar cluster-exclusion prototype and
fixture generator (Issue #50, follow-up to PR #66).

Analysis-only phase: no production code is imported. The prototype's
reimplementation of ``_analyse_page`` is validated by a reference-check
that computes signals on the exact block positions dumped from a real
compilation of ``strikeUnderline`` at commit ``71c4916``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.sidebar_cluster_exclusion_prototype import (  # type: ignore
    _compute_baseline_signals,
    _cross_cluster_metrics,
    _find_column_gap,
    _matches_h6_signature,
    _positional_blocks,
    evaluate_page,
    replay_fixtures,
)
from benchmarks.sidebar_multicolumn_fixtures import (  # type: ignore
    FIXTURES,
    mixed_multicolumn_and_sidebar_page,
    sidebar_only_page,
    single_column_control,
    true_three_column_page,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REPORT_JSON = _REPO_ROOT / "benchmarks" / "SIDEBAR_FIXTURES_REPORT_2026-07-19.json"


# ── Reference blocks captured from real compilation ──────────────────────


# strikeUnderline p1 blocks, captured from `aksharamd compile` on the
# cache at commit 71c4916. Used as a ground-truth reference for
# validating _compute_baseline_signals.
_STRIKEUNDERLINE_REAL_BLOCKS = [
    {"index": 0, "type": "paragraph", "x0": 58.32, "y0": 144.9, "content": "x " * 6},
    {"index": 1, "type": "heading", "x0": 404.58, "y0": 144.9, "content": "§"},
    {"index": 2, "type": "paragraph", "x0": 66.30, "y0": 156.48, "content": "x " * 39},
    {"index": 3, "type": "heading", "x0": 404.58, "y0": 223.02, "content": "§"},
    {"index": 4, "type": "paragraph", "x0": 66.30, "y0": 234.60, "content": "x " * 129},
    {"index": 5, "type": "heading", "x0": 404.58, "y0": 420.42, "content": "§"},
    {"index": 6, "type": "paragraph", "x0": 66.30, "y0": 432.00, "content": "x " * 154},
    {"index": 7, "type": "heading", "x0": 404.58, "y0": 617.82, "content": "§"},
    {"index": 8, "type": "paragraph", "x0": 66.30, "y0": 629.34, "content": "x " * 13},
]

# Reference signals from the shipped detector on this exact page (from
# multicolumn_diagnostics.page_analyses at commit 71c4916):
_STRIKEUNDERLINE_REFERENCE = {
    "gap_size": 338.3,      # 404.58 - 66.30 ≈ 338.28
    "gap_rel": 0.98,        # 338.28 / (404.58 - 58.32) ≈ 0.977
    "transition_rate": 1.00,  # every adjacent pair alternates
    "large_y_drops": 0,
    "short_frac_bound": (0.55, 0.60),  # implemented short_frac tolerance
    "warn": True,
}


def test_reimplementation_matches_strikeunderline_reference():
    """Cross-check: my reimplementation of _analyse_page produces
    approximately the same signals as the shipped validator on real
    strikeUnderline p1 block positions.
    """
    sig = _compute_baseline_signals(_STRIKEUNDERLINE_REAL_BLOCKS, page_width=612.0)
    ref = _STRIKEUNDERLINE_REFERENCE
    assert sig["warn"] is True
    assert sig["gap_size"] == pytest.approx(ref["gap_size"], abs=1.0)
    assert sig["gap_rel"] == pytest.approx(ref["gap_rel"], abs=0.02)
    assert sig["transition_rate"] == pytest.approx(ref["transition_rate"], abs=0.01)
    assert sig["large_y_drops"] == ref["large_y_drops"]
    lo, hi = ref["short_frac_bound"]
    assert lo <= sig["short_frac"] <= hi, (
        f"short_frac {sig['short_frac']} out of expected range {ref['short_frac_bound']}"
    )


# ── Pure: _find_column_gap ───────────────────────────────────────────────


def test_find_column_gap_biggest_gap_wins():
    xs = [10.0, 20.0, 30.0, 400.0, 410.0]
    gap, mid, xr = _find_column_gap(xs)
    assert gap == pytest.approx(370.0)
    assert mid == pytest.approx(215.0)
    assert xr == pytest.approx(400.0)


def test_find_column_gap_short_input():
    assert _find_column_gap([]) == (0.0, 0.0, 0.0)
    assert _find_column_gap([50.0]) == (0.0, 0.0, 0.0)


# ── Pure: _positional_blocks filters excluded types ─────────────────────


def test_positional_blocks_filters_excluded_types():
    blocks = [
        {"type": "paragraph", "x0": 10, "y0": 100, "content": "x " * 10},
        {"type": "table", "x0": 10, "y0": 200, "content": "x"},  # filtered
        {"type": "image", "x0": 20, "y0": 300, "content": "x"},  # filtered
        {"type": "footnote", "x0": 20, "y0": 400, "content": "x"},  # filtered
        {"type": "paragraph", "x0": 20, "y0": 500, "content": "x " * 10},
    ]
    kept = _positional_blocks(blocks)
    assert len(kept) == 2
    assert all(b["type"] == "paragraph" for b in kept)


def test_positional_blocks_filters_missing_coords():
    blocks = [
        {"type": "paragraph", "x0": None, "y0": 100, "content": "x"},
        {"type": "paragraph", "x0": 10, "y0": None, "content": "x"},
        {"type": "paragraph", "x0": 10, "y0": 100, "content": "x"},
    ]
    kept = _positional_blocks(blocks)
    assert len(kept) == 1


def test_compute_baseline_signals_needs_at_least_five_blocks():
    """The shipped validator returns silent early if <5 positional
    blocks. My reimplementation must match.
    """
    blocks = [
        {"type": "paragraph", "x0": 10, "y0": 100, "content": "x " * 10},
        {"type": "paragraph", "x0": 300, "y0": 200, "content": "x " * 10},
        {"type": "paragraph", "x0": 10, "y0": 300, "content": "x " * 10},
        {"type": "paragraph", "x0": 300, "y0": 400, "content": "x " * 10},
    ]
    sig = _compute_baseline_signals(blocks, page_width=612.0)
    assert sig["warn"] is False
    assert sig["gap_size"] == 0.0


# ── Pure: H6 signature ───────────────────────────────────────────────────


def test_h6_matches_thin_tall_no_alternation():
    cc = {"text_share_smaller": 0.003, "smaller_y_coverage_frac": 0.60,
          "alternations_substantial": 0}
    assert _matches_h6_signature(cc) is True


def test_h6_rejects_high_alternations():
    cc = {"text_share_smaller": 0.003, "smaller_y_coverage_frac": 0.60,
          "alternations_substantial": 1}
    assert _matches_h6_signature(cc) is False


def test_h6_rejects_low_coverage():
    cc = {"text_share_smaller": 0.003, "smaller_y_coverage_frac": 0.09,
          "alternations_substantial": 0}
    assert _matches_h6_signature(cc) is False


def test_h6_rejects_high_share():
    cc = {"text_share_smaller": 0.100, "smaller_y_coverage_frac": 0.60,
          "alternations_substantial": 0}
    assert _matches_h6_signature(cc) is False


def test_h6_rejects_missing_data():
    assert _matches_h6_signature({}) is False
    assert _matches_h6_signature({"text_share_smaller": 0.003}) is False


# ── Fixture invariants ───────────────────────────────────────────────────


def test_fixtures_registry_contains_all_four():
    assert len(FIXTURES) == 4
    names = {f().get("name") for f in FIXTURES}
    assert names == {
        "sidebar_only_page",
        "true_three_column_page",
        "mixed_multicolumn_and_sidebar_page",
        "single_column_control",
    }


def test_sidebar_only_fixture_matches_strikeunderline_shape():
    p = sidebar_only_page()
    result = evaluate_page(p)
    # Baseline warns (like the real strikeUnderline).
    assert result["baseline"]["warn"] is True
    # H6 matches.
    assert result["h6_matches"] is True
    # Blanket suppression silences.
    assert result["blanket_suppression_warn"] is False
    # Cluster exclusion also silences (via recomputation).
    assert result["cluster_exclusion_warn"] is False
    # Blocks that were excluded correspond to the sidebar (x=404 uniform).
    excluded_ids = set(result["excluded_block_ids"])
    excluded_blocks = [b for b in p["blocks"] if b["index"] in excluded_ids]
    assert len(excluded_blocks) == 4
    assert all(b["x0"] == 404.0 for b in excluded_blocks)


def test_true_three_column_fixture_preserved_by_cluster_exclusion():
    """The 3colpres surrogate: baseline warns, H6 does NOT match (cov
    too low), cluster exclusion is not applied, baseline verdict is
    preserved."""
    p = true_three_column_page()
    result = evaluate_page(p)
    assert result["baseline"]["warn"] is True
    assert result["h6_matches"] is False
    assert result["cluster_exclusion_warn"] is True
    assert result["excluded_block_ids"] == []


def test_mixed_case_discriminates_blanket_vs_cluster_exclusion():
    """The critical fixture: blanket suppression silences a genuine
    warning; cluster exclusion preserves it."""
    p = mixed_multicolumn_and_sidebar_page()
    result = evaluate_page(p)
    assert result["baseline"]["warn"] is True, "baseline should warn on genuine 2-column body + sidebar"
    assert result["h6_matches"] is True, (
        "H6 must match — sidebar is thin, tall, and has no substantial alternations. "
        "If this fails, the fixture design is broken."
    )
    # Blanket suppression: silences (WRONG — genuine warning silenced).
    assert result["blanket_suppression_warn"] is False, (
        "blanket suppression must silence to demonstrate the failure mode"
    )
    # Cluster exclusion: preserves (RIGHT — genuine warning kept).
    assert result["cluster_exclusion_warn"] is True, (
        "cluster exclusion must preserve the warning after removing the sidebar"
    )
    # Excluded blocks must be the sidebar (x=500).
    excluded_ids = set(result["excluded_block_ids"])
    excluded_blocks = [b for b in p["blocks"] if b["index"] in excluded_ids]
    assert len(excluded_blocks) == 4
    assert all(b["x0"] == 500.0 for b in excluded_blocks)


def test_single_column_control_untouched():
    p = single_column_control()
    result = evaluate_page(p)
    assert result["baseline"]["warn"] is False
    assert result["blanket_suppression_warn"] is False
    assert result["cluster_exclusion_warn"] is False


def test_replay_acceptance_gate_passes():
    fixtures = [f() for f in FIXTURES]
    result = replay_fixtures(fixtures)
    assert result["acceptance_gate_pass"] is True, (
        f"acceptance gate failed: {result['acceptance_gate_reasons']}"
    )


# ── Artifact tests ───────────────────────────────────────────────────────


def _load_report():
    if not _REPORT_JSON.exists():
        pytest.skip(f"report JSON missing: {_REPORT_JSON}")
    with _REPORT_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_report_carries_all_fixtures():
    r = _load_report()
    names = {row["name"] for row in r["results"]}
    assert names == {
        "sidebar_only_page",
        "true_three_column_page",
        "mixed_multicolumn_and_sidebar_page",
        "single_column_control",
    }


def test_report_acceptance_gate_passed():
    r = _load_report()
    assert r["acceptance_gate_pass"] is True, r["acceptance_gate_reasons"]


def test_report_mixed_case_is_discriminative():
    """Lock the fixture invariants that make the report meaningful: on
    the mixed case, blanket suppression silences (fails) while cluster
    exclusion preserves (succeeds).
    """
    r = _load_report()
    (mixed,) = [row for row in r["results"] if row["name"] == "mixed_multicolumn_and_sidebar_page"]
    assert mixed["baseline"]["warn"] is True
    assert mixed["h6_matches"] is True
    assert mixed["blanket_suppression_warn"] is False
    assert mixed["cluster_exclusion_warn"] is True
    assert len(mixed["excluded_block_ids"]) == 4


# ── Cross-cluster metrics ────────────────────────────────────────────────


def test_cross_cluster_returns_empty_when_only_one_cluster():
    """Single-column control: all blocks in one cluster; no cross-cluster
    metrics can be computed. Prototype must return empty dict."""
    cc = _cross_cluster_metrics(single_column_control()["blocks"], 612.0, 792.0)
    # All blocks are on the same side of the biggest gap, so cluster
    # partition yields a single non-empty cluster — the function returns {}.
    assert cc == {}


def test_cross_cluster_smaller_is_sidebar_on_sidebar_only_fixture():
    p = sidebar_only_page()
    cc = _cross_cluster_metrics(p["blocks"], p["page_width"], p["page_height"])
    # Sidebar is 4 short marker blocks; body has 4 real paragraphs.
    # Smaller cluster (by chars) MUST be the sidebar.
    smaller_ids = set(cc["smaller_cluster_block_ids"])
    smaller_blocks = [b for b in p["blocks"] if b["index"] in smaller_ids]
    assert len(smaller_blocks) == 4
    assert all(b["x0"] == 404.0 for b in smaller_blocks)
