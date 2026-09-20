"""Stage 2 aggregator for DocLayNet Track A results.

Reads all ``stage2_doclaynet_result.json`` files produced by
``run_track_a_doclaynet`` and computes:

Per-parser table-detection metrics (precision, recall, FPR) with 95%
Wilson confidence intervals.

Detector validation for W_TABLE_MISSING and W_HEADER_FOOTER_TABLE_GARBLED:
- W_TABLE_MISSING precision/recall against GT table presence.
  True positive: detector fires AND GT has a table AND parser failed to
  extract it (i.e., the warning is a correct alarm).
  False positive: detector fires but GT has no table (incorrect alarm).
- W_HEADER_FOOTER_TABLE_GARBLED: treated as a page-level table region
  heuristic; same TP/FP logic as above.

Spearman ρ(readiness_score, gt_table_extracted) — tests whether a
higher readiness score predicts correct table extraction.

Writes ``benchmarks/results/stage2-doclaynet-{date}-aggregated.json``.

Usage:
    python -m benchmarks.eval_v1.stage2.aggregate_doclaynet
    python -m benchmarks.eval_v1.stage2.aggregate_doclaynet \\
        --run-dir benchmarks/results/stage1-track-a-doclaynet-2026-09-17
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent

DEFAULT_RUN_DIR = ROOT / "benchmarks" / "results" / "stage1-track-a-doclaynet-2026-09-17"
MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"

STAGE2_SCHEMA_VERSION = "1"
AGGREGATOR_VERSION = "1"

# Detector codes of interest for detector-validation analysis.
DETECTOR_W_TABLE_MISSING = "W_TABLE_MISSING"
DETECTOR_W_HFTG = "W_HEADER_FOOTER_TABLE_GARBLED"


# ---------------------------------------------------------------------------
# Wilson 95% confidence interval for a proportion.


def _wilson_ci(k: int, n: int) -> tuple[float, float]:
    """Return (lower, upper) 95% Wilson score CI for k successes out of n."""
    if n == 0:
        return 0.0, 1.0
    z = 1.959964  # 97.5th percentile of standard normal
    p_hat = k / n
    centre = (p_hat + z * z / (2 * n)) / (1 + z * z / n)
    margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / (
        1 + z * z / n
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


# ---------------------------------------------------------------------------
# Spearman correlation.


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Return Spearman ρ for paired sequences, or None if n < 3."""
    n = len(xs)
    if n < 3:
        return None
    # Rank with average-rank ties.
    def _rank(seq: list[float]) -> list[float]:
        indexed = sorted(enumerate(seq), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg
            i = j + 1
        return ranks

    rx = _rank(xs)
    ry = _rank(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((rx[i] - mean_x) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((ry[i] - mean_y) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


# ---------------------------------------------------------------------------
# Loading result files.


def _load_results(run_dir: Path) -> list[dict]:
    results = []
    for path in sorted(run_dir.rglob("stage2_doclaynet_result.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("stage2_schema_version") == STAGE2_SCHEMA_VERSION:
                results.append(d)
        except Exception:  # noqa: BLE001
            pass
    return results


# ---------------------------------------------------------------------------
# Per-parser table detection metrics.


def _table_detection_metrics(
    results: list[dict], parser_id: str
) -> dict:
    """Precision, recall, FPR for table extraction by a single parser."""
    tp = fp = fn = tn = 0
    for r in results:
        if r["parser_id"] != parser_id:
            continue
        if r.get("execution_status") != "EXECUTED":
            continue
        gt = r["gt_has_table"]
        pred = r["parser_extracted_table"]
        if gt and pred:
            tp += 1
        elif not gt and pred:
            fp += 1
        elif gt and not pred:
            fn += 1
        else:
            tn += 1

    n_pos = tp + fn      # GT-positive pages
    n_neg = fp + tn      # GT-negative pages
    n_pred_pos = tp + fp # predicted-positive pages

    prec = tp / n_pred_pos if n_pred_pos > 0 else None
    rec = tp / n_pos if n_pos > 0 else None
    fpr = fp / n_neg if n_neg > 0 else None

    prec_ci = _wilson_ci(tp, n_pred_pos) if n_pred_pos > 0 else (None, None)
    rec_ci = _wilson_ci(tp, n_pos) if n_pos > 0 else (None, None)
    fpr_ci = _wilson_ci(fp, n_neg) if n_neg > 0 else (None, None)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "n_pos_gt": n_pos,
        "n_neg_gt": n_neg,
        "precision": prec,
        "precision_ci95": list(prec_ci),
        "recall": rec,
        "recall_ci95": list(rec_ci),
        "fpr": fpr,
        "fpr_ci95": list(fpr_ci),
    }


# ---------------------------------------------------------------------------
# Detector validation metrics.


def _detector_validation_metrics(
    results: list[dict],
    parser_id: str,
    detector_code: str,
) -> dict:
    """Precision and recall for a warning detector relative to GT.

    Definitions used here:
    - Detector fires: the detector_code appears in warning_codes.
    - True positive: detector fires AND gt_has_table=True AND
      parser_extracted_table=False (the warning correctly flagged a
      missed table).
    - False positive: detector fires AND gt_has_table=False (the
      warning fired on a page with no GT table — spurious alarm).
    - False negative: detector did NOT fire AND gt_has_table=True AND
      parser_extracted_table=False (missed-table case the detector
      failed to catch).
    """
    tp = fp = fn = 0
    n_eligible = 0  # pages where EXECUTED and readiness scorer ran

    for r in results:
        if r["parser_id"] != parser_id:
            continue
        if r.get("execution_status") != "EXECUTED":
            continue
        if r.get("warning_codes") is None:
            continue
        n_eligible += 1
        gt = r["gt_has_table"]
        extracted = r["parser_extracted_table"]
        fired = detector_code in (r.get("warning_codes") or [])
        missed_table = gt and not extracted

        if fired and missed_table:
            tp += 1
        elif fired and not gt:
            fp += 1
        elif not fired and missed_table:
            fn += 1

    n_fire = tp + fp
    n_miss = tp + fn

    prec = tp / n_fire if n_fire > 0 else None
    rec = tp / n_miss if n_miss > 0 else None
    prec_ci = _wilson_ci(tp, n_fire) if n_fire > 0 else (None, None)
    rec_ci = _wilson_ci(tp, n_miss) if n_miss > 0 else (None, None)

    return {
        "detector_code": detector_code,
        "n_eligible": n_eligible,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "n_fires": n_fire,
        "n_missed_tables": n_miss,
        "precision": prec,
        "precision_ci95": list(prec_ci),
        "recall": rec,
        "recall_ci95": list(rec_ci),
    }


# ---------------------------------------------------------------------------
# Spearman ρ analysis.


def _spearman_analysis(
    results: list[dict],
    parser_id: str,
) -> dict:
    """Spearman ρ between readiness_score and correct table extraction."""
    scores: list[float] = []
    extracted_flags: list[float] = []

    for r in results:
        if r["parser_id"] != parser_id:
            continue
        if r.get("execution_status") != "EXECUTED":
            continue
        if r.get("readiness_score") is None:
            continue
        scores.append(float(r["readiness_score"]))
        # gt_table_extracted: 1.0 if GT has table and parser extracted it,
        # 0.0 otherwise (including pages with no GT table and no extraction).
        extracted_flags.append(
            1.0
            if (r["gt_has_table"] and r["parser_extracted_table"])
            else 0.0
        )

    rho = _spearman(scores, extracted_flags) if scores else None
    return {
        "n_pairs": len(scores),
        "spearman_rho": rho,
        "interpretation": (
            "Higher readiness_score predicts correct table extraction (rho>0 = positive)."
        ),
    }


# ---------------------------------------------------------------------------
# Per-category breakdown.


def _category_breakdown(results: list[dict], parser_id: str) -> dict:
    by_cat: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    )
    for r in results:
        if r["parser_id"] != parser_id:
            continue
        if r.get("execution_status") != "EXECUTED":
            continue
        cat = r.get("doc_category", "unknown")
        gt = r["gt_has_table"]
        pred = r["parser_extracted_table"]
        if gt and pred:
            by_cat[cat]["tp"] += 1
        elif not gt and pred:
            by_cat[cat]["fp"] += 1
        elif gt and not pred:
            by_cat[cat]["fn"] += 1
        else:
            by_cat[cat]["tn"] += 1
    return dict(by_cat)


# ---------------------------------------------------------------------------
# Main aggregation.


def aggregate(run_dir: Path) -> dict:
    results = _load_results(run_dir)
    if not results:
        raise RuntimeError(
            f"No stage2_doclaynet_result.json files found under {run_dir}"
        )

    parsers = sorted({r["parser_id"] for r in results})
    n_total = len(results)
    n_executed = sum(1 for r in results if r.get("execution_status") == "EXECUTED")
    n_defect = sum(1 for r in results if r.get("execution_status") == "DEFECT")

    per_parser: dict[str, dict] = {}
    for parser_id in parsers:
        parser_results = [r for r in results if r["parser_id"] == parser_id]
        per_parser[parser_id] = {
            "n_results": len(parser_results),
            "n_executed": sum(
                1 for r in parser_results if r.get("execution_status") == "EXECUTED"
            ),
            "table_detection": _table_detection_metrics(results, parser_id),
            "detector_validation": {
                DETECTOR_W_TABLE_MISSING: _detector_validation_metrics(
                    results, parser_id, DETECTOR_W_TABLE_MISSING
                ),
                DETECTOR_W_HFTG: _detector_validation_metrics(
                    results, parser_id, DETECTOR_W_HFTG
                ),
            },
            "spearman_readiness_vs_table_extraction": _spearman_analysis(
                results, parser_id
            ),
            "by_doc_category": _category_breakdown(results, parser_id),
        }

    return {
        "aggregator_version": AGGREGATOR_VERSION,
        "stage2_schema_version": STAGE2_SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "aggregated_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "n_result_files": n_total,
        "n_executed": n_executed,
        "n_defect": n_defect,
        "parsers": parsers,
        "per_parser": per_parser,
    }


# ---------------------------------------------------------------------------
# Output path.


def _output_path() -> Path:
    date_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    out_dir = ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"stage2-doclaynet-{date_str}-aggregated.json"


# ---------------------------------------------------------------------------
# CLI.


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_RUN_DIR,
        help=f"Path to the Stage 1 run directory (default: {DEFAULT_RUN_DIR})",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override the output JSON path.",
    )
    args = p.parse_args(argv)

    run_dir: Path = args.run_dir
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not run_dir.exists():
        print(f"ERROR: run_dir not found: {run_dir}", file=sys.stderr)
        return 1

    print(f"Aggregating Stage 2 DocLayNet results from: {run_dir}")
    try:
        agg = aggregate(run_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_path: Path = args.output if args.output else _output_path()
    out_path.write_text(json.dumps(agg, indent=2), encoding="utf-8")
    print(f"Written: {out_path}")

    # Print a brief human-readable summary.
    print()
    print("=== Stage 2 DocLayNet Summary ===")
    print(f"  n_result_files : {agg['n_result_files']}")
    print(f"  n_executed     : {agg['n_executed']}")
    print(f"  n_defect       : {agg['n_defect']}")
    print()
    for pid in agg["parsers"]:
        td = agg["per_parser"][pid]["table_detection"]
        prec = td["precision"]
        rec = td["recall"]
        fpr = td["fpr"]
        rho_data = agg["per_parser"][pid]["spearman_readiness_vs_table_extraction"]
        rho = rho_data.get("spearman_rho")
        print(
            f"  {pid:30s}  prec={_fmt(prec)}  rec={_fmt(rec)}"
            f"  fpr={_fmt(fpr)}  rho={_fmt(rho)}"
        )
        # Detector validation quick-print.
        for det_code in (DETECTOR_W_TABLE_MISSING, DETECTOR_W_HFTG):
            dv = agg["per_parser"][pid]["detector_validation"][det_code]
            d_prec = dv["precision"]
            d_rec = dv["recall"]
            n_fires = dv["n_fires"]
            print(
                f"    {det_code:45s}  fires={n_fires:4d}"
                f"  prec={_fmt(d_prec)}  rec={_fmt(d_rec)}"
            )
    return 0


def _fmt(v: float | None) -> str:
    if v is None:
        return "N/A "
    return f"{v:.3f}"


if __name__ == "__main__":
    sys.exit(main())
