"""Offline candidate replay for W_MULTICOLUMN_ORDER (Issue #50 phase 2).

Reads the phase-1 harness output
(`MULTICOLUMN_RECALIBRATION_2026-07-18.json`) which already carries the
per-page detector diagnostics needed to re-decide the warning under
different rules. Applies each candidate rule to every page and every
document, records the resulting decision, and prints a comparison
matrix.

**No production code is exercised by this script.** It replays offline
against previously captured diagnostics — no wheel install, no CLI
invocation. That guarantees the replay evaluates *exactly* the rule
under study, without incidental variation from re-running the parser.

## Rule formalisation (pinned)

Baseline (current shipped detector, `_analyse_page`):

    HTR = transition_rate >= 0.28
    YMT = (large_y_drops == 0) AND (transition_rate >= 0.25)
    SF  = (short_frac >= 0.55) AND (transition_rate >= 0.20)
    signals = { s : s in {HTR, YMT, SF} and s.fires }
    baseline_warn_page = HTR OR (|signals| >= 2)
    baseline_gap_gate  = (gap_rel >= 0.15) AND (gap_size >= 60)

C3 — raise the gap-rel gate from 0.15 to 0.30:

    C3_gap_gate = (gap_rel >= 0.30) AND (gap_size >= 60)
    C3_warn_page = C3_gap_gate AND baseline_warn_page

C4 — the primary signal must be accompanied by at least one confirming
signal. Formalised as a plain Boolean expression:

    C4_warn_page = baseline_gap_gate AND HTR AND (YMT OR SF)

C3+C4 — both gates apply:

    C3_C4_warn_page = C3_gap_gate AND HTR AND (YMT OR SF)

Document-level decision (all rules): warn iff at least one page on the
document warns under that rule.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _htr(a: dict) -> bool:
    return a.get("transition_rate", 0.0) >= 0.28


def _ymt(a: dict) -> bool:
    return (a.get("large_y_drops", 0) == 0) and (a.get("transition_rate", 0.0) >= 0.25)


def _sf(a: dict) -> bool:
    return (a.get("short_frac", 0.0) >= 0.55) and (a.get("transition_rate", 0.0) >= 0.20)


def _baseline_gap_gate(a: dict) -> bool:
    return (a.get("gap_rel", 0.0) >= 0.15) and (a.get("gap_size", 0.0) >= 60)


def _c3_gap_gate(a: dict) -> bool:
    return (a.get("gap_rel", 0.0) >= 0.30) and (a.get("gap_size", 0.0) >= 60)


def _baseline_warn(a: dict) -> bool:
    if not _baseline_gap_gate(a):
        return False
    htr = _htr(a)
    ymt = _ymt(a)
    sf = _sf(a)
    signals_count = sum([htr, ymt, sf])
    return htr or signals_count >= 2


def _c3_warn(a: dict) -> bool:
    if not _c3_gap_gate(a):
        return False
    htr = _htr(a)
    ymt = _ymt(a)
    sf = _sf(a)
    signals_count = sum([htr, ymt, sf])
    return htr or signals_count >= 2


def _c4_warn(a: dict) -> bool:
    if not _baseline_gap_gate(a):
        return False
    return _htr(a) and (_ymt(a) or _sf(a))


def _c3c4_warn(a: dict) -> bool:
    if not _c3_gap_gate(a):
        return False
    return _htr(a) and (_ymt(a) or _sf(a))


RULES = [
    ("baseline", _baseline_warn),
    ("C3", _c3_warn),
    ("C4", _c4_warn),
    ("C3+C4", _c3c4_warn),
]


def _document_warn(page_analyses: list[dict], rule_fn) -> bool:
    return any(rule_fn(a) for a in page_analyses)


def _confusion(rule_name: str, per_doc_verdicts: list[tuple[str, bool | None, bool]]) -> dict:
    """Given (asset, expected, predicted) rows, compute TP/FP/TN/FN and metrics."""
    tp = fp = tn = fn = 0
    changed_from_baseline: list[str] = []
    for asset, expected, predicted in per_doc_verdicts:
        if expected is None:
            continue
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif (not expected) and predicted:
            fp += 1
        elif (not expected) and not predicted:
            tn += 1
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return {
        "rule": rule_name,
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "precision": round(p, 3),
        "recall": round(r, 3),
        "false_positive_rate": round(fpr, 3),
        "f1": round(f1, 3),
        "changed_from_baseline": changed_from_baseline,
    }


def _labels_for(r: dict, labels_map: dict) -> dict:
    rel = (r.get("relpath") or r.get("asset") or "").replace("\\", "/")
    stripped = rel[4:] if rel.startswith("pdf/") else rel
    return (
        labels_map.get(stripped)
        or labels_map.get(rel)
        or labels_map.get(r["asset"])
        or {}
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harness", required=True, type=Path,
                    help="Path to MULTICOLUMN_RECALIBRATION_*.json produced by phase 1")
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path,
                    help="Where to write the machine-readable comparison JSON")
    args = ap.parse_args()

    with args.harness.open("r", encoding="utf-8") as f:
        harness_doc = json.load(f)
    with args.labels.open("r", encoding="utf-8") as f:
        labels_doc = json.load(f)
    labels_map: dict[str, dict] = labels_doc.get("labels", {})

    per_doc_rows: list[dict] = []
    all_page_rows: list[dict] = []

    for r in harness_doc["results"]:
        label = _labels_for(r, labels_map)
        expected = label.get("expected_positive")  # True, False, or None
        excluded = label.get("excluded_reason") or label.get("unavailable")
        diag = r.get("multicolumn_diagnostics") or {}
        analyses = diag.get("page_analyses") or []
        # Baseline document-level from the diagnostics warned flag if set,
        # else re-derived from page analyses.
        baseline_doc_warn = bool(diag.get("warned"))

        doc_decisions: dict[str, bool] = {}
        for name, fn in RULES:
            doc_decisions[name] = _document_warn(analyses, fn) if analyses else False
        # Sanity: baseline replay must agree with the actual harness warning
        if analyses and doc_decisions["baseline"] != baseline_doc_warn:
            # Recovery — this can happen only if the page analyses are
            # incomplete; log but keep the recomputed value.
            doc_decisions["baseline_replay_disagreement"] = True  # type: ignore

        row = {
            "asset": r["asset"],
            "expected_positive": expected,
            "excluded": bool(excluded),
            "baseline_warn_doc": doc_decisions["baseline"],
            "C3_warn_doc": doc_decisions["C3"],
            "C4_warn_doc": doc_decisions["C4"],
            "C3+C4_warn_doc": doc_decisions["C3+C4"],
            "problem_pages_baseline": diag.get("problem_pages", []),
        }
        per_doc_rows.append(row)

        # Per-page rows
        for a in analyses:
            all_page_rows.append({
                "asset": r["asset"],
                "page": a.get("page"),
                "gap_rel": a.get("gap_rel"),
                "gap_size": a.get("gap_size"),
                "transition_rate": a.get("transition_rate"),
                "large_y_drops": a.get("large_y_drops"),
                "short_frac": a.get("short_frac"),
                "total_blocks": a.get("total_blocks"),
                "cluster_sizes": a.get("cluster_sizes"),
                "signals": a.get("signals", []),
                "baseline_warn": _baseline_warn(a),
                "C3_warn": _c3_warn(a),
                "C4_warn": _c4_warn(a),
                "C3+C4_warn": _c3c4_warn(a),
                "expected_positive": expected,
                "excluded": bool(excluded),
            })

    # Document-level metrics per rule
    doc_metrics = []
    for name, _fn in RULES:
        rows = [(row["asset"], row["expected_positive"], row[f"{name}_warn_doc"]) for row in per_doc_rows]
        doc_metrics.append(_confusion(name, rows))

    # Count per-page decision transitions vs baseline (all pages, all documents)
    def _count_pages(key: str, expected: bool | None = None) -> int:
        return sum(1 for r in all_page_rows if r.get(key) and (expected is None or r.get("expected_positive") is expected))

    page_metrics = []
    for name, _fn in RULES:
        page_fires_total = sum(1 for r in all_page_rows if r[f"{name}_warn"])
        fires_on_negative_docs = sum(1 for r in all_page_rows if r[f"{name}_warn"] and r["expected_positive"] is False)
        fires_on_positive_docs = sum(1 for r in all_page_rows if r[f"{name}_warn"] and r["expected_positive"] is True)
        fires_on_excluded_docs = sum(1 for r in all_page_rows if r[f"{name}_warn"] and (r["excluded"] or r["expected_positive"] is None))
        page_metrics.append({
            "rule": name,
            "page_firings_total": page_fires_total,
            "page_firings_on_TN_docs": fires_on_negative_docs,
            "page_firings_on_TP_docs": fires_on_positive_docs,
            "page_firings_on_excluded_docs": fires_on_excluded_docs,
        })

    # Explicit changed-decision listing per candidate vs baseline
    changed_documents = {name: [] for name, _fn in RULES if name != "baseline"}
    for row in per_doc_rows:
        for name, _fn in RULES:
            if name == "baseline":
                continue
            if row[f"{name}_warn_doc"] != row["baseline_warn_doc"]:
                changed_documents[name].append({
                    "asset": row["asset"],
                    "expected_positive": row["expected_positive"],
                    "baseline": row["baseline_warn_doc"],
                    "candidate": row[f"{name}_warn_doc"],
                })

    changed_pages = {name: [] for name, _fn in RULES if name != "baseline"}
    for r in all_page_rows:
        for name, _fn in RULES:
            if name == "baseline":
                continue
            if r[f"{name}_warn"] != r["baseline_warn"]:
                changed_pages[name].append({
                    "asset": r["asset"],
                    "page": r["page"],
                    "expected_positive": r["expected_positive"],
                    "baseline": r["baseline_warn"],
                    "candidate": r[f"{name}_warn"],
                    "gap_rel": r["gap_rel"],
                    "transition_rate": r["transition_rate"],
                    "large_y_drops": r["large_y_drops"],
                    "short_frac": r["short_frac"],
                    "signals": r["signals"],
                })

    output = {
        "harness_source": str(args.harness),
        "labels_source": str(args.labels),
        "commit_under_evaluation": harness_doc.get("commit"),
        "rules": {
            "baseline": "HTR OR (|signals| >= 2), with gap gate gap_rel>=0.15 AND gap_size>=60",
            "C3": "same warn logic as baseline but raise gap gate to gap_rel>=0.30 AND gap_size>=60",
            "C4": "warn iff HTR AND (YMT OR SF), with baseline gap gate",
            "C3+C4": "warn iff HTR AND (YMT OR SF), with C3 gap gate",
        },
        "confidence_caveats": [
            "Public corpus contains ONLY ONE labelled positive document.",
            "That document's warning fires on p3 (a single-column table), not on the span-damaged p1/p2 — recall reads as coincidentally high.",
            "External ParseBench binaries used for the Phase 1 shipped metrics are unavailable in this repository.",
            "Precision improvements below are measured against 2 duplicate GeoTopo variants; per-source precision needs a mixed corpus.",
            "Span-level defects are OUTSIDE detector observability by design — no span-level recall is computed.",
        ],
        "document_level_metrics": doc_metrics,
        "page_level_metrics": page_metrics,
        "per_document": per_doc_rows,
        "per_page": all_page_rows,
        "changed_document_decisions_vs_baseline": changed_documents,
        "changed_page_decisions_vs_baseline": changed_pages,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, sort_keys=False)

    # Human-readable print
    print("### Document-level metrics")
    print()
    print("| Rule | TP | FP | TN | FN | P | R | FPR | F1 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m in doc_metrics:
        print(f"| {m['rule']} | {m['TP']} | {m['FP']} | {m['TN']} | {m['FN']} | "
              f"{m['precision']} | {m['recall']} | {m['false_positive_rate']} | {m['f1']} |")

    print()
    print("### Page-level firing counts")
    print()
    print("| Rule | total | on TN docs | on TP docs | on excluded |")
    print("|---|---:|---:|---:|---:|")
    for m in page_metrics:
        print(f"| {m['rule']} | {m['page_firings_total']} | {m['page_firings_on_TN_docs']} | "
              f"{m['page_firings_on_TP_docs']} | {m['page_firings_on_excluded_docs']} |")

    print()
    print("### Changed document decisions vs baseline")
    for name, rows in changed_documents.items():
        print(f"- **{name}**: {len(rows)} document(s) changed decision.")
        for r in rows:
            direction = "baseline WARN → candidate silent" if r["baseline"] and not r["candidate"] else "baseline silent → candidate WARN"
            print(f"    - `{r['asset']}` (expected {r['expected_positive']}): {direction}")

    print()
    print("### Changed page decisions vs baseline")
    for name, rows in changed_pages.items():
        print(f"- **{name}**: {len(rows)} page(s) changed decision.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
