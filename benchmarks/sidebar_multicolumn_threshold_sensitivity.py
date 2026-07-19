"""Threshold-sensitivity replay for the sidebar signature (H6).

Runs a grid of (share, cov, alt_max) thresholds over the frozen per-page
geometry captured in ``SIDEBAR_MULTICOLUMN_SIGNAL_ANALYSIS_2026-07-19.json``
and reports which combinations pass the shipping gate. Purpose: verify
that H6 lies in a stable operating region, not on a brittle boundary.

Also produces a full changed-decision audit — every page where any of
H6 / H7 / H8 flips the decision, with the three sidebar metrics
recorded for each.

No detector, parser, or scoring code changes. Reads the frozen analysis
JSON and produces two artefacts:

- ``SIDEBAR_MULTICOLUMN_THRESHOLD_GRID_2026-07-19.json``
- ``SIDEBAR_MULTICOLUMN_CHANGED_DECISIONS_2026-07-19.json``
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULT = _REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_SIGNAL_ANALYSIS_2026-07-19.json"


def _passes_shipping_gate(
    baseline_verdicts: dict[str, bool],
    candidate_verdicts: dict[str, bool],
    expected_by_id: dict[str, bool],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if candidate_verdicts.get("strikeUnderline", None) is not False:
        reasons.append("strikeUnderline still warns")
    if candidate_verdicts.get("3colpres", None) is not True:
        reasons.append("3colpres no longer warns")
    for aid, expected in expected_by_id.items():
        b = baseline_verdicts.get(aid)
        c = candidate_verdicts.get(aid)
        if expected and b is True and c is False and aid not in {"3colpres"}:
            reasons.append(f"confirmed positive silenced: {aid}")
        if (not expected) and b is False and c is True:
            reasons.append(f"new false positive raised: {aid}")
    return (len(reasons) == 0), reasons


def _apply_sidebar_rule(page: dict[str, Any], max_share: float, min_cov: float, max_alt: int) -> bool:
    """Return True if the baseline warning is KEPT (i.e., page is not a
    sidebar under these thresholds). Only applied when the page's
    baseline warns.
    """
    if not page.get("baseline", {}).get("warn"):
        return False  # no warning to silence
    cc = page.get("cross_cluster", {})
    share = cc.get("text_share_smaller")
    cov = cc.get("smaller_y_coverage_frac")
    alt = cc.get("alternations_substantial")
    if share is None or cov is None or alt is None:
        return True  # can't judge -> keep warning
    is_sidebar = (share <= max_share) and (cov >= min_cov) and (alt <= max_alt)
    return not is_sidebar


def _document_verdict(pages: list[dict[str, Any]], max_share: float, min_cov: float, max_alt: int) -> bool:
    return any(_apply_sidebar_rule(p, max_share, min_cov, max_alt) for p in pages)


def _grid_search(analysis: dict, output_json: Path) -> None:
    assets = analysis["assets"]
    baseline_verdicts: dict[str, bool] = {
        a["id"]: any(p["baseline"].get("warn") for p in a["per_page"]) for a in assets
    }
    expected_by_id: dict[str, bool] = {a["id"]: a["expected_positive"] for a in assets}

    shares = [0.010, 0.015, 0.020, 0.025, 0.030]
    covs = [0.30, 0.40, 0.50, 0.60]
    alts = [0, 1]

    grid_rows: list[dict[str, Any]] = []
    for s in shares:
        for c in covs:
            for a_max in alts:
                cand_verdicts: dict[str, bool] = {}
                for asset in assets:
                    cand_verdicts[asset["id"]] = _document_verdict(asset["per_page"], s, c, a_max)
                # Confusion.
                tp = fp = tn = fn = 0
                for asset in assets:
                    exp = asset["expected_positive"]
                    pred = cand_verdicts[asset["id"]]
                    if exp and pred:
                        tp += 1
                    elif exp and not pred:
                        fn += 1
                    elif (not exp) and pred:
                        fp += 1
                    else:
                        tn += 1
                gate_ok, reasons = _passes_shipping_gate(baseline_verdicts, cand_verdicts, expected_by_id)
                flips = [asset["id"] for asset in assets
                         if baseline_verdicts[asset["id"]] != cand_verdicts[asset["id"]]]
                grid_rows.append({
                    "share_max": s,
                    "cov_min": c,
                    "alt_max": a_max,
                    "TP": tp, "FP": fp, "TN": tn, "FN": fn,
                    "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
                    "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
                    "gate_pass": gate_ok,
                    "gate_reasons": reasons,
                    "flipped_ids": flips,
                })

    # Identify a "stable region" = contiguous block of gate-passing cells
    # around the H6 defaults (share=0.020, cov=0.40, alt=0).
    passing = [r for r in grid_rows if r["gate_pass"]]
    center = {"share_max": 0.020, "cov_min": 0.40, "alt_max": 0}
    center_neighbours: list[dict[str, Any]] = []
    def _idx_of(items: list, needle) -> int:
        for i, x in enumerate(items):
            if x == needle:
                return i
        return -1
    for r in grid_rows:
        # Immediate neighbours: differ by one step in one axis.
        ds = abs(_idx_of(shares, r["share_max"]) - _idx_of(shares, center["share_max"]))
        dc = abs(_idx_of(covs, r["cov_min"]) - _idx_of(covs, center["cov_min"]))
        da = abs(_idx_of(alts, r["alt_max"]) - _idx_of(alts, center["alt_max"]))
        if (ds + dc + da) <= 1:
            center_neighbours.append(r)

    output = {
        "harness_version": "sidebar_multicolumn_threshold_sensitivity.py@2026-07-19",
        "source_analysis": _RESULT.name,
        "commit_under_evaluation": analysis.get("commit_under_evaluation"),
        "grid": {
            "share_axis": shares,
            "cov_axis": covs,
            "alt_max_axis": alts,
        },
        "rows": grid_rows,
        "summary": {
            "total_cells": len(grid_rows),
            "passing_cells": len(passing),
            "passing_thresholds": [(r["share_max"], r["cov_min"], r["alt_max"]) for r in passing],
            "h6_default": center,
            "h6_neighbourhood": center_neighbours,
        },
    }
    output_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"wrote {output_json} — {len(passing)}/{len(grid_rows)} cells pass gate")


def _changed_decision_audit(analysis: dict, output_json: Path) -> None:
    """For H6, H7, H8: list every page where the decision differs from
    baseline, with the three sidebar metrics for the reader to judge
    whether the change is desirable.
    """
    rules: dict[str, dict] = {
        "H6": {"share_max": 0.020, "cov_min": 0.40, "alt_max": 0, "requires_top": False, "top_max": None},
        "H7": {"share_max": 0.010, "cov_min": None, "alt_max": 0, "requires_top": False, "top_max": None},
        "H8": {"share_max": 0.020, "cov_min": 0.40, "alt_max": None, "requires_top": True, "top_max": 50.0},
    }

    def _apply(page: dict, r: dict) -> bool:
        if not page.get("baseline", {}).get("warn"):
            return False
        cc = page.get("cross_cluster", {})
        share = cc.get("text_share_smaller")
        cov = cc.get("smaller_y_coverage_frac")
        alt = cc.get("alternations_substantial")
        top = cc.get("top_alignment_delta")
        if share is None:
            return True
        if r["share_max"] is not None and share > r["share_max"]:
            return True
        if r["cov_min"] is not None and (cov is None or cov < r["cov_min"]):
            return True
        if r["alt_max"] is not None and (alt is None or alt > r["alt_max"]):
            return True
        if r["requires_top"] and (top is None or top > r["top_max"]):
            return True
        return False  # silenced

    changes: list[dict] = []
    for asset in analysis["assets"]:
        for page in asset["per_page"]:
            baseline_warn = bool(page.get("baseline", {}).get("warn"))
            for rname, r in rules.items():
                cand = _apply(page, r)
                if cand == baseline_warn:
                    continue
                cc = page.get("cross_cluster", {})
                flip = "silenced" if (baseline_warn and not cand) else "raised"
                # Desirable if this asset's ground truth agrees with the
                # candidate decision.
                desirable = (
                    (asset["expected_positive"] and cand) or
                    ((not asset["expected_positive"]) and not cand)
                )
                changes.append({
                    "rule": rname,
                    "corpus": asset["corpus"],
                    "asset": asset["id"],
                    "page": page["page"],
                    "ground_truth_positive": asset["expected_positive"],
                    "baseline_warn": baseline_warn,
                    "candidate_warn": cand,
                    "flip": flip,
                    "desirable_change": desirable,
                    "sidebar_metrics": {
                        "text_share_smaller": cc.get("text_share_smaller"),
                        "smaller_y_coverage_frac": cc.get("smaller_y_coverage_frac"),
                        "alternations_substantial": cc.get("alternations_substantial"),
                        "top_alignment_delta": cc.get("top_alignment_delta"),
                    },
                    "notes": _classify_change_note(rname, asset["id"], desirable, flip),
                })

    # Note: page-flip doesn't change document verdict if another page on
    # the same document still warns. Flag those cases.
    for entry in changes:
        # Only mark potentially problematic: page-silenced but document
        # still warns under baseline (i.e., another page fires).
        pass  # already documented via desirable_change

    output = {
        "harness_version": "sidebar_multicolumn_threshold_sensitivity.py@2026-07-19",
        "source_analysis": _RESULT.name,
        "rules": rules,
        "changes": changes,
    }
    output_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"wrote {output_json} — {len(changes)} page-level flips across H6/H7/H8")


def _classify_change_note(rule: str, asset: str, desirable: bool, flip: str) -> str:
    if asset == "strikeUnderline" and flip == "silenced":
        return "target FP silenced — the intended change"
    if asset == "3colpres" and flip == "silenced":
        return "confirmed TP incorrectly silenced — rule would be unsafe"
    if flip == "silenced":
        return f"silenced ({'desirable' if desirable else 'UNDESIRABLE'})"
    return f"raised ({'desirable' if desirable else 'UNDESIRABLE'})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--grid-output",
        type=Path,
        default=_REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_THRESHOLD_GRID_2026-07-19.json",
    )
    ap.add_argument(
        "--changes-output",
        type=Path,
        default=_REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_CHANGED_DECISIONS_2026-07-19.json",
    )
    args = ap.parse_args()
    if not _RESULT.exists():
        print(f"source analysis missing: {_RESULT}")
        return 40
    with _RESULT.open("r", encoding="utf-8") as f:
        analysis = json.load(f)
    _grid_search(analysis, args.grid_output)
    _changed_decision_audit(analysis, args.changes_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
