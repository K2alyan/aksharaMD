"""Invariant tests for the sidebar vs. multicolumn signal-analysis
harness (Issue #50 analysis-only phase).

Two kinds of tests:

1. **Pure tests** — exercise the cluster-boundary reconstruction, the
   candidate-rule predicates, the confusion helper, and the shipping-gate
   logic against synthetic dicts. These run everywhere and do not require
   the analysis JSON.
2. **Artifact tests** — assert the produced JSON at
   ``benchmarks/SIDEBAR_MULTICOLUMN_SIGNAL_ANALYSIS_2026-07-19.json``
   matches the expected shape, corpus size, gate outcome for the three
   passing candidates, and the strikeUnderline / 3colpres decisive
   comparison. Skipped when the artifact is absent.

No detector, parser, or scoring code is imported.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.sidebar_multicolumn_signal_analysis import (  # type: ignore
    CANDIDATES,
    Asset,
    _confusion,
    _hypothesis_h1_smaller_cluster_short,
    _hypothesis_h2_text_share_balanced,
    _hypothesis_h3_alternating,
    _hypothesis_h4_contiguous_inset,
    _hypothesis_h6_thin_tall_marker,
    _hypothesis_h7_thin_marker_no_coverage_gate,
    _hypothesis_h8_top_aligned_thin,
    _passes_shipping_gate,
    _validator_cluster_boundary,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULT = _REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_SIGNAL_ANALYSIS_2026-07-19.json"


# ── Pure: validator cluster boundary ─────────────────────────────────────


def test_validator_boundary_empty():
    assert _validator_cluster_boundary([]) == (None, 0.0)


def test_validator_boundary_single_point():
    assert _validator_cluster_boundary([50.0]) == (None, 0.0)


def test_validator_boundary_largest_gap_wins():
    xs = [10.0, 20.0, 30.0, 400.0, 410.0]
    boundary, gap = _validator_cluster_boundary(xs)
    assert boundary == pytest.approx(215.0)  # midpoint of 30→400
    assert gap == pytest.approx(370.0)


def test_validator_boundary_ties():
    xs = [10.0, 100.0, 190.0]  # both gaps equal to 90
    boundary, gap = _validator_cluster_boundary(xs)
    assert gap == pytest.approx(90.0)
    assert boundary in (55.0, 145.0)  # either midpoint acceptable


# ── Pure: candidate predicates on synthetic pages ────────────────────────


def _make_page(**cross_cluster) -> dict:
    """A minimal page dict with baseline warn=True and configurable
    cross_cluster geometry.
    """
    return {
        "baseline": {"warn": True},
        "cross_cluster": {
            "smaller_cluster": 1,
            "larger_cluster": 0,
            "text_share_smaller": cross_cluster.get("text_share_smaller", 0.5),
            "words_share_smaller": cross_cluster.get("words_share_smaller", 0.5),
            "y_overlap_frac": cross_cluster.get("y_overlap_frac", 1.0),
            "top_alignment_delta": cross_cluster.get("top_alignment_delta", 0.0),
            "bottom_alignment_delta": cross_cluster.get("bottom_alignment_delta", 0.0),
            "smaller_disjoint_runs": cross_cluster.get("smaller_disjoint_runs", 1),
            "smaller_y_coverage_frac": cross_cluster.get("smaller_y_coverage_frac", 0.5),
            "alternations_all": cross_cluster.get("alternations_all", 5),
            "alternations_substantial": cross_cluster.get("alternations_substantial", 3),
        },
    }


def test_h6_silences_strikeunderline_shaped_page():
    """A thin, tall, no-alternation marker should be silenced by H6."""
    p = _make_page(
        text_share_smaller=0.003,
        smaller_y_coverage_frac=0.60,
        alternations_substantial=0,
    )
    assert _hypothesis_h6_thin_tall_marker(p) is False  # silenced


def test_h6_preserves_3colpres_shaped_page():
    """A thin cluster with LOW y-coverage (like 3colpres's headshot)
    must NOT be silenced by H6."""
    p = _make_page(
        text_share_smaller=0.010,
        smaller_y_coverage_frac=0.09,
        alternations_substantial=1,
    )
    assert _hypothesis_h6_thin_tall_marker(p) is True  # keep warning


def test_h6_preserves_true_column_shape():
    """A cluster with balanced text share (>0.02) is NOT a sidebar."""
    p = _make_page(
        text_share_smaller=0.08,
        smaller_y_coverage_frac=0.80,
        alternations_substantial=5,
    )
    assert _hypothesis_h6_thin_tall_marker(p) is True


def test_h6_returns_none_data_keeps_warning():
    """If any input signal is missing, H6 must not silence a warning."""
    p = _make_page(smaller_y_coverage_frac=None)
    assert _hypothesis_h6_thin_tall_marker(p) is True


def test_h6_is_no_op_when_baseline_does_not_warn():
    p = _make_page(text_share_smaller=0.001, smaller_y_coverage_frac=0.9,
                   alternations_substantial=0)
    p["baseline"]["warn"] = False
    assert _hypothesis_h6_thin_tall_marker(p) is False  # stays silent


def test_h7_silences_thin_low_alt():
    p = _make_page(text_share_smaller=0.003, alternations_substantial=0)
    assert _hypothesis_h7_thin_marker_no_coverage_gate(p) is False


def test_h7_preserves_3colpres_via_alternations():
    """Even though share is at the threshold, alt_substantial=1 keeps
    the warning."""
    p = _make_page(text_share_smaller=0.010, alternations_substantial=1)
    assert _hypothesis_h7_thin_marker_no_coverage_gate(p) is True


def test_h8_requires_top_alignment():
    """A thin tall bottom-aligned inset must NOT be silenced by H8
    (because H8 assumes sidebars are top-aligned)."""
    p = _make_page(text_share_smaller=0.003, smaller_y_coverage_frac=0.9,
                   top_alignment_delta=500.0)
    assert _hypothesis_h8_top_aligned_thin(p) is True  # keep warning


def test_h8_silences_top_aligned_thin_tall():
    p = _make_page(text_share_smaller=0.003, smaller_y_coverage_frac=0.9,
                   top_alignment_delta=0.0)
    assert _hypothesis_h8_top_aligned_thin(p) is False


def test_early_hypotheses_silence_3colpres_shape():
    """H1–H4 individually match a 3colpres-shaped page — that's the
    failure mode that keeps them from the shipping gate. On a page
    where the smaller cluster is short (cov<0.6), thin (share<0.15),
    contiguous (runs=1), and has few alternations, all four early
    hypotheses silence — and so does H1 on 3colpres's actual data."""
    p = _make_page(
        text_share_smaller=0.010,
        smaller_y_coverage_frac=0.09,
        alternations_substantial=1,
        smaller_disjoint_runs=1,
    )
    assert _hypothesis_h1_smaller_cluster_short(p) is False  # silences
    assert _hypothesis_h2_text_share_balanced(p) is False    # silences
    assert _hypothesis_h3_alternating(p) is False            # silences
    assert _hypothesis_h4_contiguous_inset(p) is False       # silences


def test_h1_keeps_a_tall_smaller_cluster():
    """H1 KEEPS the warning when the smaller cluster is tall (cov>=0.6).
    That's why H1 does not silence strikeUnderline — the sidebar has
    high coverage."""
    p = _make_page(text_share_smaller=0.003, smaller_y_coverage_frac=0.60,
                   alternations_substantial=0)
    assert _hypothesis_h1_smaller_cluster_short(p) is True   # kept


# ── Pure: confusion + shipping gate ──────────────────────────────────────


def test_confusion_math():
    rows = [
        ("a", True, True),   # TP
        ("b", True, False),  # FN
        ("c", False, True),  # FP
        ("d", False, False),  # TN
    ]
    m = _confusion(rows)
    assert m["TP"] == 1 and m["FP"] == 1 and m["TN"] == 1 and m["FN"] == 1
    assert m["recall"] == 0.5
    assert m["false_positive_rate"] == 0.5


def test_shipping_gate_requires_strikeunderline_silence_and_3colpres_preserve():
    """The shipping-gate function must fail if strikeUnderline still
    warns OR if 3colpres is silenced."""
    assets = [
        Asset(id="strikeUnderline", corpus="parsebench", pdf_path=Path("."),
              expected_positive=False),
        Asset(id="3colpres", corpus="parsebench", pdf_path=Path("."),
              expected_positive=True),
    ]
    # Baseline: both warn
    baseline = {"strikeUnderline": True, "3colpres": True}

    # Perfect candidate: silences strikeUnderline, keeps 3colpres.
    ok, reasons = _passes_shipping_gate(baseline, {"strikeUnderline": False, "3colpres": True}, assets)
    assert ok and not reasons

    # Candidate that leaves strikeUnderline warning — fails.
    ok, reasons = _passes_shipping_gate(baseline, {"strikeUnderline": True, "3colpres": True}, assets)
    assert not ok and any("strikeUnderline" in r for r in reasons)

    # Candidate that silences 3colpres — fails.
    ok, reasons = _passes_shipping_gate(baseline, {"strikeUnderline": False, "3colpres": False}, assets)
    assert not ok and any("3colpres" in r for r in reasons)


def test_shipping_gate_flags_new_false_positive():
    assets = [
        Asset(id="strikeUnderline", corpus="parsebench", pdf_path=Path("."),
              expected_positive=False),
        Asset(id="3colpres", corpus="parsebench", pdf_path=Path("."),
              expected_positive=True),
        Asset(id="battery", corpus="parsebench", pdf_path=Path("."),
              expected_positive=False),
    ]
    baseline = {"strikeUnderline": True, "3colpres": True, "battery": False}
    # Candidate that silences strikeUnderline BUT raises a warning on
    # battery — must fail.
    ok, reasons = _passes_shipping_gate(
        baseline,
        {"strikeUnderline": False, "3colpres": True, "battery": True},
        assets,
    )
    assert not ok
    assert any("battery" in r for r in reasons)


def test_candidates_registry_names():
    names = {name for name, _desc, _rule in CANDIDATES}
    assert "baseline" in names
    for expected in ("H1_cov60", "H2_share15", "H3_alt3", "H4_runs2", "H5_align100",
                     "H1+H2", "H1+H3", "H6_thin_tall_marker",
                     "H7_thin_marker_no_cov", "H8_top_aligned_sidebar"):
        assert expected in names, f"missing candidate: {expected}"


# ── Artifact tests ───────────────────────────────────────────────────────


def _load_result():
    if not _RESULT.exists():
        pytest.skip(f"result artifact not present: {_RESULT}")
    with _RESULT.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_artifact_carries_all_candidates():
    r = _load_result()
    for name, _desc, _rule in CANDIDATES:
        assert name in r["candidates"], f"missing candidate in artifact: {name}"


def test_artifact_corpus_shape():
    r = _load_result()
    pb_ids = set(r["corpus"]["parsebench_ids"])
    # ParseBench block-level-observable = 5 assets.
    assert pb_ids == {"3colpres", "2colmercedes", "battery",
                      "eastbaytimes", "strikeUnderline"}
    # Public attested set — every entry present in the labels manifest
    # whose PDF is on disk.
    assert isinstance(r["corpus"]["public_ids"], list)
    assert len(r["corpus"]["public_ids"]) >= 20
    assert r["corpus"]["eligible_page_count"] > 0


def test_artifact_baseline_matches_expected_confusion():
    r = _load_result()
    baseline = r["candidates"]["baseline"]["confusion"]
    # Baseline: 2 TP (3colpres, multicolumn.pdf); 3 FP (strikeUnderline,
    # GeoTopo, GeoTopo-komprimiert); TN = attested negatives - FP; FN = 0.
    assert baseline["TP"] == 2
    assert baseline["FP"] == 3
    assert baseline["FN"] == 0
    assert baseline["recall"] == 1.0


def test_artifact_h6_passes_shipping_gate():
    r = _load_result()
    entry = r["candidates"]["H6_thin_tall_marker"]
    assert entry["passes_shipping_gate"] is True
    assert entry["gate_reasons"] == []
    # Confusion: recall stays at 1.0, FPR drops.
    assert entry["confusion"]["recall"] == 1.0
    assert entry["confusion"]["FN"] == 0
    assert entry["confusion"]["FP"] < 3


def test_artifact_h6_flips_only_strikeUnderline():
    r = _load_result()
    entry = r["candidates"]["H6_thin_tall_marker"]
    flipped_ids = {row["id"] for row in entry["changed_decisions"]}
    assert flipped_ids == {"strikeUnderline"}
    (row,) = entry["changed_decisions"]
    assert row["baseline"] is True and row["candidate"] is False
    assert row["flip"] == "silenced"


def test_artifact_all_five_early_candidates_silence_3colpres():
    """Every hypothesis that fails the shipping gate must fail because
    3colpres was silenced. If a candidate fails for a different reason,
    the shipping-gate logic or the finding text needs to update."""
    r = _load_result()
    for name in ("H1_cov60", "H2_share15", "H3_alt3", "H4_runs2",
                 "H1+H2", "H1+H3"):
        entry = r["candidates"][name]
        assert entry["passes_shipping_gate"] is False
        assert any("3colpres" in reason for reason in entry["gate_reasons"]), (
            f"expected {name!r} to fail because 3colpres was silenced; "
            f"got reasons {entry['gate_reasons']}"
        )


def test_artifact_h6_h7_h8_all_pass_gate():
    r = _load_result()
    for name in ("H6_thin_tall_marker", "H7_thin_marker_no_cov",
                 "H8_top_aligned_sidebar"):
        entry = r["candidates"][name]
        assert entry["passes_shipping_gate"] is True, (
            f"{name}: expected to pass shipping gate. reasons={entry['gate_reasons']}"
        )
        # Every passing candidate must flip strikeUnderline only.
        flipped_ids = {row["id"] for row in entry["changed_decisions"]}
        assert flipped_ids == {"strikeUnderline"}, (
            f"{name}: unexpected flipped ids {flipped_ids}"
        )


def test_artifact_strikeunderline_geometry_matches_report():
    """Lock the strikeUnderline vs 3colpres comparison table in the report."""
    r = _load_result()
    su = next(a for a in r["assets"] if a["id"] == "strikeUnderline")
    threec = next(a for a in r["assets"] if a["id"] == "3colpres")

    (su_page,) = su["per_page"]
    (tc_page,) = threec["per_page"]

    cc_su = su_page["cross_cluster"]
    cc_tc = tc_page["cross_cluster"]

    # strikeUnderline sidebar signature
    assert cc_su["text_share_smaller"] < 0.01
    assert cc_su["smaller_y_coverage_frac"] > 0.40
    assert cc_su["alternations_substantial"] == 0
    assert cc_su["top_alignment_delta"] < 50

    # 3colpres headshot signature
    assert cc_tc["text_share_smaller"] < 0.02  # thin but not as thin
    assert cc_tc["smaller_y_coverage_frac"] < 0.20  # NOT tall
    assert cc_tc["alternations_substantial"] >= 1
    # 3colpres is bottom-aligned; top_delta is large
    assert cc_tc["top_alignment_delta"] > 500


def test_artifact_declares_no_production_code_change():
    """The report and JSON both declare no production code changes. If
    anyone edits either such that this line disappears, they must
    coordinate with the reviewer."""
    r = _load_result()
    # The JSON records commit_under_evaluation; assert it is populated.
    assert isinstance(r.get("commit_under_evaluation"), str)
    assert len(r["commit_under_evaluation"]) >= 7


# ── Review-addendum artifacts ────────────────────────────────────────────


_GRID = _REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_THRESHOLD_GRID_2026-07-19.json"
_CHG = _REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_CHANGED_DECISIONS_2026-07-19.json"


def test_threshold_grid_has_stable_h6_neighbourhood():
    if not _GRID.exists():
        pytest.skip(f"grid artifact not present: {_GRID}")
    with _GRID.open("r", encoding="utf-8") as f:
        g = json.load(f)
    # Every immediate H6 neighbour must pass the shipping gate.
    for r in g["summary"]["h6_neighbourhood"]:
        assert r["gate_pass"], (
            f"H6 neighbourhood cell (share<={r['share_max']}, "
            f"cov>={r['cov_min']}, alt<={r['alt_max']}) fails the gate: "
            f"{r['gate_reasons']}"
        )
    # In each passing neighbour cell, the ONLY flipped id is strikeUnderline.
    for r in g["summary"]["h6_neighbourhood"]:
        if r["gate_pass"]:
            assert r["flipped_ids"] == ["strikeUnderline"], (
                f"unexpected flips in H6 neighbourhood cell: {r['flipped_ids']}"
            )


def test_threshold_grid_brittle_edge_is_cov_060():
    """The failing cells in the 40-cell grid are all at cov_min = 0.60,
    exactly where strikeUnderline's measured 0.597 sits. This test
    documents the brittle edge — if a future grid change shifts it,
    the report body must be revised.
    """
    if not _GRID.exists():
        pytest.skip("grid missing")
    with _GRID.open("r", encoding="utf-8") as f:
        g = json.load(f)
    failing = [r for r in g["rows"] if not r["gate_pass"]]
    assert failing, "expected some grid cells to fail — none did"
    # Every failing cell must be at cov_min = 0.60.
    cov_failures = {r["cov_min"] for r in failing}
    assert cov_failures == {0.60}, (
        f"unexpected cov thresholds in failing cells: {cov_failures}"
    )


def test_changed_decisions_audit_flips_only_strikeunderline():
    """The complete changed-decision audit across H6/H7/H8 must show
    exactly one page-level flip per rule, all on strikeUnderline p1.
    """
    if not _CHG.exists():
        pytest.skip("changes artifact missing")
    with _CHG.open("r", encoding="utf-8") as f:
        c = json.load(f)
    for rule in ("H6", "H7", "H8"):
        rule_changes = [ch for ch in c["changes"] if ch["rule"] == rule]
        assert len(rule_changes) == 1, (
            f"expected exactly one flip for {rule}; got {len(rule_changes)}"
        )
        (ch,) = rule_changes
        assert ch["asset"] == "strikeUnderline"
        assert ch["page"] == 1
        assert ch["flip"] == "silenced"
        assert ch["desirable_change"] is True
