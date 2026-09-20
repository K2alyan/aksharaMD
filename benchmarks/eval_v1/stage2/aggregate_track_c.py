"""Stage 2 aggregator for Track C (Consequential Validity) results.

Reads all ``track_c_result.json`` files produced by
``benchmarks.eval_v1.stage1.run_track_c`` and computes Claim 4:

  ρ(readiness_score, degradation) < 0

where:
  degradation = EM(aksharamd-reference) − EM(this_parser)
  (positive degradation = this parser did worse than the reference)

Analysis:
  - Spearman ρ per corpus (QASPER, TAT-DQA) and pooled.
  - Bootstrap 95% CI (10,000 resamples) via scipy.stats.spearmanr.
  - p-value from the parametric spearmanr test.
  - Flag if ρ > 0 (falsifying result per preregistered hypothesis).

Output:
  benchmarks/results/stage2-track-c-{date}-aggregated.json

Usage:
    python -m benchmarks.eval_v1.stage2.aggregate_track_c
    python -m benchmarks.eval_v1.stage2.aggregate_track_c \\
        --run-dir benchmarks/results/stage1-track-c-2026-09-17
    python -m benchmarks.eval_v1.stage2.aggregate_track_c \\
        --n-bootstrap 1000
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent.parent

MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"
DEFAULT_RUN_DIR = ROOT / "benchmarks" / "results" / "stage1-track-c-2026-09-17"

STAGE2_SCHEMA_VERSION = "2"
AGGREGATOR_VERSION = "2"

# Frozen primary reference: aksharamd-reference for both corpora.
# This matches STUDY_FREEZE_MANIFEST_V1.md §4: degradation = EM(reference) − EM(parsed).
PRIMARY_REFERENCE = "aksharamd-reference"

# Exploratory reference: corpus_gold for QASPER (gold-text ceiling),
# aksharamd-reference for TAT-DQA (no usable full_text).
# Only used for the exploratory primary_score analysis.
EXPLORATORY_REFERENCE_BY_CORPUS: dict[str, str] = {
    "qasper": "corpus_gold",
    "tat_dqa": "aksharamd-reference",
}

# Keep for backward compatibility in callers that import REFERENCE_PARSER/REFERENCE_BY_CORPUS.
REFERENCE_PARSER = PRIMARY_REFERENCE
REFERENCE_BY_CORPUS = EXPLORATORY_REFERENCE_BY_CORPUS

CORPORA = ["qasper", "tat_dqa"]

N_BOOTSTRAP_DEFAULT = 10_000


# ---------------------------------------------------------------------------
# Data loading.


def _load_run_dir(run_dir: Path) -> list[dict[str, Any]]:
    """Recursively find all track_c_result.json files and return their contents."""
    if not run_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in run_dir.rglob("track_c_result.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            results.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] could not parse {path}: {exc}", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Spearman correlation.


def _rank(values: list[float]) -> list[float]:
    """Return rank of each element (1-based, average ranks for ties)."""
    n = len(values)
    sorted_with_idx = sorted(enumerate(values), key=lambda x: x[1])
    ranks: list[float] = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and sorted_with_idx[j + 1][1] == sorted_with_idx[i][1]:
            j += 1
        # average rank for ties
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[sorted_with_idx[k][0]] = avg_rank
        i = j + 1
    return ranks


def _spearman_rho(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation coefficient."""
    n = len(xs)
    if n < 3:
        return float("nan")
    rx = _rank(xs)
    ry = _rank(ys)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = sum((r - mean_rx) ** 2 for r in rx) ** 0.5
    den_y = sum((r - mean_ry) ** 2 for r in ry) ** 0.5
    if den_x == 0 or den_y == 0:
        return float("nan")
    return num / (den_x * den_y)


def _spearman_pvalue(rho: float, n: int) -> float:
    """Two-tailed p-value for Spearman rho using t approximation."""
    import math
    if n < 3 or not (rho == rho):  # nan check
        return float("nan")
    if abs(rho) >= 1.0:
        return 0.0
    t_stat = rho * math.sqrt((n - 2) / (1 - rho ** 2))
    # Two-tailed p-value from t distribution with df=n-2.
    # Use scipy if available; otherwise use a normal approximation for large n.
    try:
        from scipy import stats as _stats
        return float(2 * _stats.t.sf(abs(t_stat), df=n - 2))
    except ImportError:
        import math as _math
        # Normal approximation (valid for n > ~30).
        return float(2 * (1 - _erf_cdf(abs(t_stat) / _math.sqrt(2))))


def _erf_cdf(z: float) -> float:
    """Approximation to the standard normal CDF using math.erf."""
    import math
    return (1 + math.erf(z / math.sqrt(2))) / 2


def _bootstrap_ci_clustered(
    pairs: list[dict[str, Any]],
    *,
    n_bootstrap: int = N_BOOTSTRAP_DEFAULT,
    seed: int = 42,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Document-clustered bootstrap (1-alpha)×100% CI for Spearman rho.

    Resamples documents (not pairs) with replacement so that the correlation
    structure among multiple parser arms scored on the same document is
    preserved. Pair-level resampling would overstate independence.

    Returns (lower, upper) bounds.  Returns (nan, nan) if fewer than 3 unique
    documents are present.
    """
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for p in pairs:
        doc = p["canonical_id"]
        if doc not in by_doc:
            by_doc[doc] = []
        by_doc[doc].append(p)

    docs = list(by_doc.keys())
    n_docs = len(docs)
    if n_docs < 3:
        return float("nan"), float("nan")

    rng = random.Random(seed)
    boot_rhos: list[float] = []

    for _ in range(n_bootstrap):
        sampled_docs = [rng.choice(docs) for _ in docs]
        bx: list[float] = []
        by_: list[float] = []
        for doc in sampled_docs:
            for p in by_doc[doc]:
                bx.append(p["readiness_score"])
                by_.append(p["degradation"])
        rho = _spearman_rho(bx, by_)
        if rho == rho:  # not nan
            boot_rhos.append(rho)

    if not boot_rhos:
        return float("nan"), float("nan")

    boot_rhos.sort()
    lo_idx = int(alpha / 2 * len(boot_rhos))
    hi_idx = min(int((1 - alpha / 2) * len(boot_rhos)), len(boot_rhos) - 1)
    return boot_rhos[lo_idx], boot_rhos[hi_idx]


# ---------------------------------------------------------------------------
# Analysis logic.


def _get_score(rec: dict[str, Any], field: str) -> float | None:
    """Return the named score field as float, or None if absent/null."""
    v = rec.get(field)
    return float(v) if v is not None else None


def _build_pairs(
    records: list[dict[str, Any]],
    corpus: str | None = None,
    *,
    score_field: str,
    reference_parser: str,
    reference_by_corpus: dict[str, str] | None = None,
    exclude_parsers: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Build (readiness_score, degradation) pairs for non-reference parsers.

    score_field     : which record field to use for scoring ("em_score" or "primary_score")
    reference_parser: default reference arm (used when reference_by_corpus has no entry)
    reference_by_corpus: optional per-corpus override for the reference arm
    exclude_parsers : parser IDs to exclude from the output pairs (e.g. post-freeze arms
                      that must not appear in the frozen primary analysis)

    degradation = score(reference) − score(this_parser)
    """
    _exclude = exclude_parsers or frozenset()

    def _ref_arm(corp: str) -> str:
        if reference_by_corpus:
            return reference_by_corpus.get(corp, reference_parser)
        return reference_parser

    # Build reference score lookup: (corpus, canonical_id) -> score.
    reference_score: dict[tuple[str, str], float] = {}
    for rec in records:
        corp = rec.get("corpus", "")
        if corpus is not None and corp != corpus:
            continue
        if rec.get("parser_id") != _ref_arm(corp):
            continue
        if rec.get("execution_status") != "EXECUTED":
            continue
        s = _get_score(rec, score_field)
        if s is None:
            continue
        reference_score[(corp, rec.get("canonical_id", ""))] = s

    pairs: list[dict[str, Any]] = []
    for rec in records:
        corp = rec.get("corpus", "")
        parser_id = rec.get("parser_id", "")
        if corpus is not None and corp != corpus:
            continue
        if parser_id == _ref_arm(corp):
            continue
        if parser_id in _exclude:
            continue
        if rec.get("execution_status") != "EXECUTED":
            continue
        s = _get_score(rec, score_field)
        rs = rec.get("readiness_score")
        if s is None or rs is None:
            continue
        cid = rec.get("canonical_id", "")
        ref_s = reference_score.get((corp, cid))
        if ref_s is None:
            continue
        pairs.append({
            "corpus": corp,
            "canonical_id": cid,
            "parser_id": parser_id,
            "reference_parser": _ref_arm(corp),
            "readiness_score": float(rs),
            "score": s,
            "score_reference": ref_s,
            "degradation": ref_s - s,
        })

    return pairs


def _analyse_corpus(
    records: list[dict[str, Any]],
    corpus: str | None,
    *,
    n_bootstrap: int,
    score_field: str,
    reference_parser: str,
    reference_by_corpus: dict[str, str] | None = None,
    exclude_parsers: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Compute Spearman rho(readiness_score, degradation) for one corpus or pooled."""
    pairs = _build_pairs(
        records,
        corpus=corpus,
        score_field=score_field,
        reference_parser=reference_parser,
        reference_by_corpus=reference_by_corpus,
        exclude_parsers=exclude_parsers,
    )
    n = len(pairs)

    if n < 3:
        return {
            "n_pairs": n,
            "rho": None,
            "p_value": None,
            "ci_lower": None,
            "ci_upper": None,
            "n_bootstrap": n_bootstrap,
            "n_docs": len({p["canonical_id"] for p in pairs}),
            "hypothesis_falsified": None,
            "note": "insufficient data (n < 3)",
        }

    xs = [p["readiness_score"] for p in pairs]
    ys = [p["degradation"] for p in pairs]
    rho = _spearman_rho(xs, ys)
    p_value = _spearman_pvalue(rho, n)
    ci_lo, ci_hi = _bootstrap_ci_clustered(pairs, n_bootstrap=n_bootstrap)
    hypothesis_falsified = bool(rho > 0) if (rho == rho) else None

    return {
        "n_pairs": n,
        "n_docs": len({p["canonical_id"] for p in pairs}),
        "rho": round(rho, 6) if (rho == rho) else None,
        "p_value": round(p_value, 6) if (p_value == p_value) else None,
        "ci_lower": round(ci_lo, 6) if (ci_lo == ci_lo) else None,
        "ci_upper": round(ci_hi, 6) if (ci_hi == ci_hi) else None,
        "n_bootstrap": n_bootstrap,
        "hypothesis_falsified": hypothesis_falsified,
    }


def _run_analysis(
    records: list[dict[str, Any]],
    *,
    n_bootstrap: int,
    score_field: str,
    reference_parser: str,
    reference_by_corpus: dict[str, str] | None = None,
    exclude_parsers: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Run full primary or exploratory analysis across all corpora."""
    kw = dict(
        n_bootstrap=n_bootstrap,
        score_field=score_field,
        reference_parser=reference_parser,
        reference_by_corpus=reference_by_corpus,
        exclude_parsers=exclude_parsers,
    )
    return {
        "corpora": {
            corp: _analyse_corpus(records, corp, **kw)
            for corp in CORPORA
        },
        "pooled": _analyse_corpus(records, None, **kw),
    }


def _score_summary(
    records: list[dict[str, Any]],
    corpus: str,
) -> dict[str, Any]:
    """Per-parser mean em_score and primary_score for a corpus."""
    em_by: dict[str, list[float]] = defaultdict(list)
    ps_by: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        if rec.get("corpus") != corpus:
            continue
        if rec.get("execution_status") != "EXECUTED":
            continue
        pid = rec.get("parser_id", "")
        em = _get_score(rec, "em_score")
        ps = _get_score(rec, "primary_score")
        if em is not None:
            em_by[pid].append(em)
        if ps is not None:
            ps_by[pid].append(ps)

    all_pids = sorted(set(em_by) | set(ps_by))
    summary: dict[str, Any] = {}
    for pid in all_pids:
        ems = em_by.get(pid, [])
        pss = ps_by.get(pid, [])
        summary[pid] = {
            "n_docs": max(len(ems), len(pss)),
            "mean_em_score": round(sum(ems) / len(ems), 4) if ems else None,
            "mean_primary_score": round(sum(pss) / len(pss), 4) if pss else None,
        }
    return summary


# ---------------------------------------------------------------------------
# Main aggregation.


def aggregate(
    run_dir: Path,
    *,
    n_bootstrap: int = N_BOOTSTRAP_DEFAULT,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Load all track_c_result.json files and compute Claim 4 statistics."""
    print(f"Loading results from: {run_dir}")
    records = _load_run_dir(run_dir)
    print(f"  Loaded {len(records)} result records")

    corpus_counts: dict[str, int] = defaultdict(int)
    for rec in records:
        corpus_counts[rec.get("corpus", "unknown")] += 1
    for corp, cnt in sorted(corpus_counts.items()):
        print(f"  {corp}: {cnt} records")

    print()
    print(f"Computing Spearman correlations (n_bootstrap={n_bootstrap}) ...")

    # Primary analysis: frozen EM endpoint, aksharamd-reference for both corpora.
    # corpus_gold is excluded — it is a post-freeze arm and must not appear in the
    # frozen V1 primary endpoint (only in exploratory analysis).
    print("  [primary] em_score, reference=aksharamd-reference, excluding corpus_gold ...")
    primary = _run_analysis(
        records,
        n_bootstrap=n_bootstrap,
        score_field="em_score",
        reference_parser=PRIMARY_REFERENCE,
        exclude_parsers=frozenset({"corpus_gold"}),
    )

    # Exploratory analysis: corpus-appropriate metric, corpus_gold reference for QASPER.
    print("  [exploratory] primary_score, QASPER ref=corpus_gold, TAT-DQA ref=aksharamd-reference ...")
    exploratory = _run_analysis(
        records,
        n_bootstrap=n_bootstrap,
        score_field="primary_score",
        reference_parser=PRIMARY_REFERENCE,
        reference_by_corpus=EXPLORATORY_REFERENCE_BY_CORPUS,
    )

    qasper_summary = _score_summary(records, "qasper")
    tatdqa_summary = _score_summary(records, "tat_dqa")

    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    run_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    result: dict[str, Any] = {
        "schema_version": STAGE2_SCHEMA_VERSION,
        "aggregator_version": AGGREGATOR_VERSION,
        "aggregated_at": now,
        "run_dir": str(run_dir),
        "claim": "Claim 4: rho(readiness_score, degradation) < 0",
        "hypothesis": "Higher readiness_score predicts less degradation (negative rho).",
        "falsifying_condition": "rho > 0",
        "primary_analysis": {
            "metric": "em_score",
            "metric_note": (
                "Frozen V1 endpoint per STUDY_FREEZE_MANIFEST_V1.md §4. "
                "degradation = EM(aksharamd-reference) - EM(parser). "
                "If EM is near-zero for all parsers, record Claim 4 as NOT ESTABLISHED under V1."
            ),
            "reference_parser": PRIMARY_REFERENCE,
            **primary,
            "score_by_parser": {
                "qasper": {
                    pid: {"n_docs": v["n_docs"], "mean_em_score": v["mean_em_score"]}
                    for pid, v in qasper_summary.items()
                },
                "tat_dqa": {
                    pid: {"n_docs": v["n_docs"], "mean_em_score": v["mean_em_score"]}
                    for pid, v in tatdqa_summary.items()
                },
            },
        },
        "exploratory_analysis": {
            "metric": "primary_score",
            "metric_note": (
                "EXPLORATORY — NOT the V1 primary endpoint. Added post-observation. "
                "QASPER: max-annotator token F1. TAT-DQA: numeric-normalized EM. "
                "Reference: corpus_gold for QASPER (gold-text ceiling), "
                "aksharamd-reference for TAT-DQA. "
                "See V1_PROTOCOL_DEVIATIONS.md D-001."
            ),
            "reference_by_corpus": EXPLORATORY_REFERENCE_BY_CORPUS,
            **exploratory,
            "score_by_parser": {
                "qasper": {
                    pid: {"n_docs": v["n_docs"], "mean_primary_score": v["mean_primary_score"]}
                    for pid, v in qasper_summary.items()
                },
                "tat_dqa": {
                    pid: {"n_docs": v["n_docs"], "mean_primary_score": v["mean_primary_score"]}
                    for pid, v in tatdqa_summary.items()
                },
            },
        },
        "mmlong_bench_doc": {
            "status": "NOT_EXECUTABLE",
            "reason": "No MMLongBench-Doc adapter in V1.",
        },
    }

    if output_path is None:
        results_dir = ROOT / "benchmarks" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        output_path = results_dir / f"stage2-track-c-{run_date}-aggregated.json"

    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote: {output_path}")
    return result


def _print_spearman(sp: dict[str, Any], indent: str = "    ") -> None:
    n = sp.get("n_pairs")
    if n is not None and n >= 3:
        rho = sp.get("rho")
        p = sp.get("p_value")
        ci_lo = sp.get("ci_lower")
        ci_hi = sp.get("ci_upper")
        falsified = sp.get("hypothesis_falsified")
        verdict = (
            "FALSIFIED (rho > 0)" if falsified
            else ("CONSISTENT (rho <= 0)" if falsified is not None else "UNDETERMINED")
        )
        print(f"{indent}n_pairs  : {n}")
        print(f"{indent}rho      : {rho}")
        print(f"{indent}p_value  : {p}")
        print(f"{indent}95% CI   : [{ci_lo}, {ci_hi}]")
        print(f"{indent}verdict  : {verdict}")
    else:
        print(f"{indent}INSUFFICIENT DATA (n={n}): {sp.get('note', '')}")


def _print_report(result: dict[str, Any]) -> None:
    """Print a human-readable summary to stdout."""
    print()
    print("=== TRACK C AGGREGATION REPORT ===")
    print(f"  Claim 4  : {result['claim']}")
    print()

    for section_key, label in [
        ("primary_analysis", "PRIMARY (frozen EM — V1 endpoint)"),
        ("exploratory_analysis", "EXPLORATORY (corpus-appropriate metric — NOT V1 endpoint)"),
    ]:
        section = result.get(section_key, {})
        print(f"  ── {label} ──")
        print(f"  metric      : {section.get('metric')}")
        print(f"  note        : {section.get('metric_note', '')[:120]}...")
        print()

        corpora_data = section.get("corpora", {})
        for corpus_name in CORPORA:
            sp = corpora_data.get(corpus_name, {})
            print(f"    [{corpus_name.upper()}]")
            _print_spearman(sp, indent="      ")

            score_by = section.get("score_by_parser", {}).get(corpus_name, {})
            score_key = "mean_em_score" if section_key == "primary_analysis" else "mean_primary_score"
            if score_by:
                print(f"      Scores by parser:")
                for pid in sorted(score_by.keys()):
                    info = score_by[pid]
                    print(f"        {pid:30s}: {score_key}={info.get(score_key)}  n={info.get('n_docs')}")
            print()

        sp_pooled = section.get("pooled", {})
        print(f"    [POOLED]")
        _print_spearman(sp_pooled, indent="      ")
        print()

    mm = result.get("mmlong_bench_doc", {})
    print(f"  MMLongBench-Doc: {mm.get('status')} -- {mm.get('reason')}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run-dir",
        default=str(DEFAULT_RUN_DIR),
        help=f"Path to Stage 1 Track C run directory (default: {DEFAULT_RUN_DIR})",
    )
    p.add_argument(
        "--n-bootstrap",
        type=int,
        default=N_BOOTSTRAP_DEFAULT,
        help=f"Number of bootstrap resamples for CI (default: {N_BOOTSTRAP_DEFAULT})",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: benchmarks/results/stage2-track-c-{date}-aggregated.json)",
    )
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"ERROR: run directory not found: {run_dir}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else None
    result = aggregate(run_dir, n_bootstrap=args.n_bootstrap, output_path=output_path)
    _print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
