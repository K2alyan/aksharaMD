"""Stage 2 aggregator for Track A olmOCR-Bench scored results.

Reads all ``stage2_olmocr_result.json`` files produced by
``benchmarks.eval_v1.stage2.run_score_olmocr`` and computes:

Claim 1 — Detector agreement with benchmark evaluators
    Precision / Recall / FPR for each AksharaMD detector against its
    mapped olmOCR-Bench test type.  Computed only for parser_id ==
    "aksharamd-reference".  95% Wilson CI on each proportion.

Claim 2 — Fidelity-score monotonicity
    Spearman ρ(readiness_score, benchmark_pass_rate) per parser per
    category.  10,000-resample bootstrap 95% CI.

Track B Allocation Manifest
    Deterministic selection of (canonical_id, parser_id) pairs per
    readiness band for human usability review.  Uses the frozen seed
    from the Study Freeze Manifest V1.

Usage
-----
    python -m benchmarks.eval_v1.stage2.aggregate_olmocr
    python -m benchmarks.eval_v1.stage2.aggregate_olmocr \\
        --run-dir benchmarks/results/stage1-track-a-olmocr-2026-09-17
    python -m benchmarks.eval_v1.stage2.aggregate_olmocr \\
        --n-bootstrap 1000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent.parent

# ---------------------------------------------------------------------------
# Detector → GT mapping  (§7 of STUDY_FREEZE_MANIFEST_V1.md)
# "categories": list of canonical_id prefixes (None = any category)
# "test_types": list of olmOCR test type strings
# ---------------------------------------------------------------------------
DETECTOR_GT_MAPPING: dict[str, dict[str, Any]] = {
    "W_MULTICOLUMN_ORDER": {
        "categories": ["multi_column/"],
        "test_types": ["order"],
    },
    "W_ENCODING_ARTIFACTS": {
        "categories": ["old_scans/", "old_scans_math/"],
        "test_types": ["present", "absent"],
    },
    "W_DROPPED_CONTENT": {
        "categories": None,
        "test_types": ["present"],
    },
    "W_HEADER_FOOTER_TABLE_GARBLED": {
        "categories": ["headers_footers/"],
        "test_types": ["absent"],
    },
    "W_GIBBERISH": {
        "categories": None,
        "test_types": ["baseline"],
    },
    "W_TABLE_MISSING": {
        "categories": ["tables/"],
        "test_types": ["table"],
    },
}

# Frozen seed (§8 of STUDY_FREEZE_MANIFEST_V1.md)
FREEZE_SEED = "6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa"

TRACK_B_N_RECRUIT = 178
TRACK_B_N_TARGET = 151

BAND_HIGH = 0.85
BAND_OK = 0.70
BAND_RISKY = 0.50

RESULT_FILENAME = "stage2_olmocr_result.json"


# ---------------------------------------------------------------------------
# Statistics helpers.
# ---------------------------------------------------------------------------

def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman ρ via Pearson correlation on ranks (tie-safe).

    The d² shortcut formula (1 - 6Σd²/(n(n²-1))) gives incorrect results
    when ties are present — a constant predictor yields 0.5 instead of NaN.
    Readiness scores are discrete (0-100 integers), so ties are common.
    """
    n = len(xs)
    if n < 3:
        return float("nan")

    def _ranks(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda iv: iv[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx = _ranks(xs)
    ry = _ranks(ys)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = sum((r - mean_rx) ** 2 for r in rx) ** 0.5
    den_y = sum((r - mean_ry) ** 2 for r in ry) ** 0.5
    if den_x == 0 or den_y == 0:
        return float("nan")
    return num / (den_x * den_y)


def _bootstrap_spearman_ci(
    xs: list[float],
    ys: list[float],
    n_bootstrap: int = 10_000,
    seed: int = 42,
) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(xs)
    boot = []
    for _ in range(n_bootstrap):
        idx = [rng.randrange(n) for _ in range(n)]
        r = _spearman([xs[i] for i in idx], [ys[i] for i in idx])
        if not math.isnan(r):
            boot.append(r)
    boot.sort()
    if not boot:
        return (float("nan"), float("nan"))
    lo_i = int(0.025 * len(boot))
    hi_i = int(0.975 * len(boot))
    return (boot[lo_i], boot[min(hi_i, len(boot) - 1)])


# ---------------------------------------------------------------------------
# Data loading.
# ---------------------------------------------------------------------------

def _load_results(run_dir: Path) -> list[dict]:
    results = []
    for path in run_dir.rglob(RESULT_FILENAME):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN: could not read {path}: {exc}", file=sys.stderr)
    return results


def _band(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= BAND_HIGH:
        return "HIGH"
    if score >= BAND_OK:
        return "OK"
    if score >= BAND_RISKY:
        return "RISKY"
    return "POOR"


# ---------------------------------------------------------------------------
# Claim 1 — Detector precision / recall / FPR.
# ---------------------------------------------------------------------------

def _relevant_tests(
    test_results: list[dict],
    canonical_id: str,
    detector: str,
) -> list[dict]:
    mapping = DETECTOR_GT_MAPPING[detector]
    cats = mapping["categories"]
    types = set(mapping["test_types"])
    out = []
    for t in test_results:
        if cats is not None and not any(canonical_id.startswith(c) for c in cats):
            continue
        if t.get("test_type") not in types:
            continue
        out.append(t)
    return out


def compute_claim1(scored: list[dict]) -> dict:
    ref = [
        r for r in scored
        if r.get("parser_id") == "aksharamd-reference" and r.get("status") == "SCORED"
    ]
    results: dict[str, dict] = {}
    for detector in DETECTOR_GT_MAPPING:
        tp = fp = fn = tn = 0
        for r in ref:
            cid = r["canonical_id"]
            fired = detector in set(r.get("warning_codes") or [])
            tests = _relevant_tests(r.get("test_results") or [], cid, detector)
            if not tests:
                continue
            gt_failed = any(not t.get("passed", True) for t in tests)
            if fired and gt_failed:
                tp += 1
            elif fired and not gt_failed:
                fp += 1
            elif not fired and gt_failed:
                fn += 1
            else:
                tn += 1
        prec = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
        rec = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
        fpr = fp / (fp + tn) if (fp + tn) > 0 else float("nan")
        results[detector] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "n_in_scope": tp + fp + fn + tn,
            "precision": prec,
            "recall": rec,
            "fpr": fpr,
            "precision_ci95": list(_wilson_ci(tp, tp + fp)) if (tp + fp) > 0 else None,
            "recall_ci95": list(_wilson_ci(tp, tp + fn)) if (tp + fn) > 0 else None,
            "fpr_ci95": list(_wilson_ci(fp, fp + tn)) if (fp + tn) > 0 else None,
        }
    return results


# ---------------------------------------------------------------------------
# Claim 2 — Spearman ρ(readiness_score, benchmark_pass_rate).
# ---------------------------------------------------------------------------

def compute_claim2(scored: list[dict], n_bootstrap: int = 10_000) -> dict:
    groups: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    pooled: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in scored:
        if r.get("status") != "SCORED":
            continue
        rs = r.get("readiness_score")
        n_tests = r.get("n_tests", 0)
        n_passed = r.get("n_passed", 0)
        if rs is None or n_tests == 0:
            continue
        pass_rate = n_passed / n_tests
        parser_id = r["parser_id"]
        cid = r.get("canonical_id", "")
        category = cid.split("/")[0] if "/" in cid else cid
        groups[(parser_id, category)].append((rs, pass_rate))
        pooled[parser_id].append((rs, pass_rate))

    per_parser_per_category: dict[str, Any] = {}
    for (parser_id, category), pairs in sorted(groups.items()):
        if len(pairs) < 3:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        rho = _spearman(xs, ys)
        lo, hi = _bootstrap_spearman_ci(xs, ys, n_bootstrap=n_bootstrap)
        per_parser_per_category[f"{parser_id}/{category}"] = {
            "parser_id": parser_id,
            "category": category,
            "n": len(pairs),
            "spearman_rho": rho,
            "ci95_lo": lo,
            "ci95_hi": hi,
        }

    pooled_out: dict[str, Any] = {}
    for parser_id, pairs in sorted(pooled.items()):
        if len(pairs) < 3:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        rho = _spearman(xs, ys)
        lo, hi = _bootstrap_spearman_ci(xs, ys, n_bootstrap=n_bootstrap)
        pooled_out[parser_id] = {
            "parser_id": parser_id,
            "n": len(pairs),
            "spearman_rho": rho,
            "ci95_lo": lo,
            "ci95_hi": hi,
        }

    return {"per_parser_per_category": per_parser_per_category, "pooled": pooled_out}


# ---------------------------------------------------------------------------
# Track B Allocation Manifest.
# ---------------------------------------------------------------------------

def _pair_sort_key(canonical_id: str, parser_id: str) -> str:
    raw = f"{canonical_id}||{parser_id}||{FREEZE_SEED}".encode()
    return hashlib.sha256(raw).hexdigest()


def build_track_b_manifest(scored: list[dict]) -> dict:
    ref = [
        r for r in scored
        if r.get("parser_id") == "aksharamd-reference" and r.get("status") == "SCORED"
    ]
    bands: dict[str, list[dict]] = defaultdict(list)
    for r in ref:
        bands[_band(r.get("readiness_score"))].append(r)

    allocation: dict[str, Any] = {}
    for band_name in ["HIGH", "OK", "RISKY", "POOR"]:
        band_records = sorted(
            bands.get(band_name, []),
            key=lambda r: _pair_sort_key(r["canonical_id"], r["parser_id"]),
        )
        n_eligible = len(band_records)
        selected = band_records[:TRACK_B_N_RECRUIT]
        n_selected = len(selected)
        achievable_ci = (
            math.sqrt(1.96 ** 2 * 0.25 / n_selected) if n_selected >= 20 else None
        )
        allocation[band_name] = {
            "n_eligible": n_eligible,
            "n_selected": n_selected,
            "n_recruit_target": TRACK_B_N_RECRUIT,
            "n_target": TRACK_B_N_TARGET,
            "underpowered": n_selected < 20,
            "achievable_ci_halfwidth": achievable_ci,
            "sparse_band": n_eligible < TRACK_B_N_RECRUIT,
            "pairs": [
                {
                    "canonical_id": r["canonical_id"],
                    "parser_id": r["parser_id"],
                    "readiness_score": r.get("readiness_score"),
                    "sort_key": _pair_sort_key(r["canonical_id"], r["parser_id"]),
                }
                for r in selected
            ],
        }

    return {
        "schema_version": "1",
        "freeze_seed": FREEZE_SEED,
        "n_recruit_per_band": TRACK_B_N_RECRUIT,
        "n_target_per_band": TRACK_B_N_TARGET,
        "generated_at": datetime.now(UTC).isoformat(),
        "bands": allocation,
    }


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run-dir",
        default=str(
            ROOT / "benchmarks" / "results" / "stage1-track-a-olmocr-2026-09-17"
        ),
    )
    p.add_argument("--n-bootstrap", type=int, default=10_000)
    p.add_argument("--out-dir", default=str(ROOT / "benchmarks" / "results"))
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    print("=== STAGE 2 olmOCR AGGREGATOR ===")
    print(f"  run_dir : {run_dir}")
    print()

    print("[1/5] Loading stage2_olmocr_result.json files ...")
    all_results = _load_results(run_dir)
    scored = [r for r in all_results if r.get("status") == "SCORED"]
    statuses: dict[str, int] = defaultdict(int)
    for r in all_results:
        statuses[r.get("status", "UNKNOWN")] += 1
    for k, v in sorted(statuses.items()):
        print(f"  {k:20s}: {v}")
    print()

    if not scored:
        print("ERROR: no SCORED results found. Run run_score_olmocr first.", file=sys.stderr)
        return 1

    print("[2/5] Computing Claim 1 — detector precision/recall ...")
    claim1 = compute_claim1(scored)
    for det, m in claim1.items():
        prec_s = f"{m['precision']:.3f}" if not math.isnan(m["precision"]) else "n/a"
        rec_s = f"{m['recall']:.3f}" if not math.isnan(m["recall"]) else "n/a"
        print(f"  {det:40s}  P={prec_s}  R={rec_s}  n_scope={m['n_in_scope']}")
    print()

    print(f"[3/5] Computing Claim 2 — Spearman rho (n_bootstrap={args.n_bootstrap}) ...")
    claim2 = compute_claim2(scored, n_bootstrap=args.n_bootstrap)
    for parser_id, m in claim2["pooled"].items():
        rho_s = f"{m['spearman_rho']:.3f}" if not math.isnan(m["spearman_rho"]) else "n/a"
        lo_s = f"{m['ci95_lo']:.3f}" if not math.isnan(m["ci95_lo"]) else "n/a"
        hi_s = f"{m['ci95_hi']:.3f}" if not math.isnan(m["ci95_hi"]) else "n/a"
        print(f"  {parser_id:30s}  rho={rho_s}  CI95=[{lo_s},{hi_s}]  n={m['n']}")
    print()

    print("[4/5] Generating Track B Allocation Manifest ...")
    track_b = build_track_b_manifest(scored)
    for band_name, bdata in track_b["bands"].items():
        flags = ""
        if bdata["underpowered"]:
            flags += " [UNDERPOWERED]"
        if bdata["sparse_band"]:
            flags += " [SPARSE]"
        print(f"  {band_name:6s}: {bdata['n_selected']:3d}/{bdata['n_eligible']:3d} eligible{flags}")
    track_b_path = out_dir / f"track-b-allocation-manifest-{date_str}.json"
    track_b_path.write_text(json.dumps(track_b, indent=2), encoding="utf-8")
    print(f"  Written: {track_b_path}")
    print()

    print("[5/5] Writing aggregated output ...")
    out = {
        "schema_version": "1",
        "generated_at": datetime.now(UTC).isoformat(),
        "run_dir": str(run_dir),
        "n_total_results": len(all_results),
        "n_scored": len(scored),
        "claim_1_detector_precision_recall": claim1,
        "claim_2_spearman_rho": claim2,
        "track_b_allocation_manifest_path": str(track_b_path),
    }
    out_path = out_dir / f"stage2-olmocr-{date_str}-aggregated.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"  Written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
