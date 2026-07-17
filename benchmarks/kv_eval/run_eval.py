"""Run the KV detection evaluation against the dev corpus.

Usage:
    python -m benchmarks.kv_eval.run_eval [--output OUTPUT_DIR]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from benchmarks.kv_eval.ground_truth import CorpusMetrics


def run_evaluation(output_dir: Path) -> dict:
    """Run full dev-corpus evaluation. Returns summary dict."""
    from aksharamd.scoring.key_value_config import KeyValueDetectionProfile
    from benchmarks.kv_eval.corpus import abergowrie_case, load_dev_corpus
    from benchmarks.kv_eval.detector_lock import build_lock, build_lock_v2
    from benchmarks.kv_eval.evaluator import (
        compute_corpus_metrics,
        evaluate_adjacent_case,
        evaluate_html_case,
        evaluate_text_case,
        simulate_adjacent_threshold,
        simulate_adjacent_threshold_real,
    )
    from benchmarks.kv_eval.ground_truth import (
        PathMaturityLabels,
    )
    from benchmarks.kv_eval.repeated_record_qa import (
        run_qa_comparison,
        summarize_qa_results,
    )
    from benchmarks.kv_eval.token_comparison import compare_tokens

    output_dir.mkdir(parents=True, exist_ok=True)

    corpus = load_dev_corpus()
    lock = build_lock()
    lock_v2 = build_lock_v2()

    # --- Profiles ---
    default_profile = KeyValueDetectionProfile()
    experimental_profile = KeyValueDetectionProfile.experimental()

    inline_cases = corpus.get("heuristic_inline", [])
    negative_cases = corpus.get("negative_control", [])
    all_inline_cases = inline_cases + negative_cases

    # --- v1 inline metrics (no profile → legacy path) ---
    v1_outcomes = [
        evaluate_text_case(c.text, c.ground_truth) for c in all_inline_cases
    ]
    v1_gt = {c.ground_truth.case_id: c.ground_truth for c in all_inline_cases}
    inline_metrics = compute_corpus_metrics(v1_outcomes, v1_gt, "heuristic_inline_v1")

    # --- v2 inline metrics (experimental profile → new classifier) ---
    v2_outcomes = [
        evaluate_text_case(c.text, c.ground_truth, profile=experimental_profile)
        for c in all_inline_cases
    ]
    inline_v2_metrics = compute_corpus_metrics(
        v2_outcomes, v1_gt, "heuristic_inline_v2"
    )

    # --- default profile metrics (heuristics disabled) ---
    v2_default_outcomes = [
        evaluate_text_case(c.text, c.ground_truth, profile=default_profile)
        for c in all_inline_cases
    ]
    default_metrics = compute_corpus_metrics(
        v2_default_outcomes, v1_gt, "default_profile"
    )

    # --- HTML DL native evaluation ---
    html_cases = corpus.get("native_html_dl", [])
    html_outcomes = [evaluate_html_case(c.html, c.ground_truth) for c in html_cases]
    html_gt = {c.ground_truth.case_id: c.ground_truth for c in html_cases}
    html_metrics = compute_corpus_metrics(html_outcomes, html_gt, "native_html_dl")

    # --- XLSX KV native evaluation ---
    xlsx_cases = corpus.get("native_xlsx_kv", [])
    xlsx_metrics = _evaluate_xlsx_cases(xlsx_cases)

    # --- Adjacent threshold simulation (text-approx on combined inline set) ---
    adj_sim = simulate_adjacent_threshold(
        all_inline_cases, v1_gt, min_blocks_options=[2, 3, 4, 5]
    )

    # --- Adjacent real evaluation (using actual Block objects) ---
    adj_cases = corpus.get("adjacent_block", [])
    adjacent_real_metrics_v1 = None
    adjacent_real_metrics_v2 = None
    adjacent_threshold_real = None
    if adj_cases:
        # v1: legacy — no profile is passed to the promoter so it uses v1
        # via the default profile (heuristics disabled). Note the promoter
        # is off in v1 baseline because the default profile has adjacent
        # disabled.
        v1_adj = [
            evaluate_adjacent_case(c.blocks, c.ground_truth)
            for c in adj_cases
        ]
        adj_gt = {c.ground_truth.case_id: c.ground_truth for c in adj_cases}
        adjacent_real_metrics_v1 = compute_corpus_metrics(
            v1_adj, adj_gt, "heuristic_adjacent_real_v1"
        )
        # v2 with experimental profile (heuristics enabled)
        v2_adj = [
            evaluate_adjacent_case(c.blocks, c.ground_truth, profile=experimental_profile)
            for c in adj_cases
        ]
        adjacent_real_metrics_v2 = compute_corpus_metrics(
            v2_adj, adj_gt, "heuristic_adjacent_real_v2"
        )
        adjacent_threshold_real = simulate_adjacent_threshold_real(
            adj_cases, adj_gt, min_blocks_options=[2, 3, 4, 5],
            profile=experimental_profile,
        )

    # --- Hard-negative evaluation (v1 + v2) ---
    hard_neg_cases = corpus.get("hard_negative", [])
    hard_negative_metrics = None
    hard_negative_v2_metrics = None
    hard_negative_categories = {}
    if hard_neg_cases:
        hn_gt = {c.ground_truth.case_id: c.ground_truth for c in hard_neg_cases}
        hn_v1 = [evaluate_text_case(c.text, c.ground_truth) for c in hard_neg_cases]
        hard_negative_metrics = compute_corpus_metrics(
            hn_v1, hn_gt, "hard_negative_v1"
        )
        hn_v2 = [
            evaluate_text_case(c.text, c.ground_truth, profile=experimental_profile)
            for c in hard_neg_cases
        ]
        hard_negative_v2_metrics = compute_corpus_metrics(
            hn_v2, hn_gt, "hard_negative_v2"
        )
        # Per-category breakdown
        hard_negative_categories = _per_category_metrics(hard_neg_cases, hn_v2)

    # --- Token comparison on promoted inline cases (v2) ---
    token_results = []
    tsv_count = 0
    md_count = 0
    for case in all_inline_cases:
        from aksharamd.scoring.key_value_detection import detect_key_value_entries
        result = detect_key_value_entries(
            case.text, page=1, profile=experimental_profile
        )
        if result.group is not None:
            try:
                tc = compare_tokens(case.ground_truth.case_id, case.text, result.group)
                token_results.append(tc.model_dump())
                if tc.selected_format == "tsv":
                    tsv_count += 1
                else:
                    md_count += 1
            except Exception:
                pass

    total_token = tsv_count + md_count
    token_format_pct = {
        "tsv_count": tsv_count,
        "markdown_count": md_count,
        "total": total_token,
        "tsv_pct": round(tsv_count / total_token * 100, 1) if total_token > 0 else 0.0,
        "markdown_pct": round(md_count / total_token * 100, 1) if total_token > 0 else 0.0,
    }

    # --- Repeated-record structural QA ---
    qa_results = run_qa_comparison()
    qa_summary = summarize_qa_results(qa_results)

    # --- Abergowrie regression ---
    ab_case = abergowrie_case()
    ab_outcome = evaluate_text_case(
        ab_case.text, ab_case.ground_truth, profile=experimental_profile
    )

    # --- Per-section counts ---
    corpus_counts = {k: len(v) for k, v in corpus.items()}

    summary = {
        "lock": lock.model_dump(),
        "detector_v2": {
            "version": "kv_promoter/v2",
            "default_heuristics_enabled": False,
            "lock_v2": lock_v2.model_dump(),
            "inline_v2_metrics": inline_v2_metrics.model_dump(),
            "hard_negative_v2_metrics": (
                hard_negative_v2_metrics.model_dump()
                if hard_negative_v2_metrics else None
            ),
            "hard_negative_v2_by_category": hard_negative_categories,
            "adjacent_real_v2_metrics": (
                adjacent_real_metrics_v2.model_dump()
                if adjacent_real_metrics_v2 else None
            ),
            "default_profile_metrics": default_metrics.model_dump(),
        },
        "timestamp": datetime.now(UTC).isoformat(),
        "corpus_counts": corpus_counts,
        "path_maturity": PathMaturityLabels().model_dump(),
        "inline_metrics": inline_metrics.model_dump(),
        "inline_v2_metrics": inline_v2_metrics.model_dump(),
        "default_profile_metrics": default_metrics.model_dump(),
        "html_metrics": html_metrics.model_dump(),
        "xlsx_metrics": xlsx_metrics.model_dump() if xlsx_metrics else None,
        "adjacent_threshold_simulation": {
            str(k): v.model_dump() for k, v in adj_sim.items()
        },
        "adjacent_real_metrics": (
            adjacent_real_metrics_v1.model_dump()
            if adjacent_real_metrics_v1 else None
        ),
        "adjacent_real_v2_metrics": (
            adjacent_real_metrics_v2.model_dump()
            if adjacent_real_metrics_v2 else None
        ),
        "adjacent_threshold_real": {
            str(k): v.model_dump() for k, v in (adjacent_threshold_real or {}).items()
        },
        "hard_negative_metrics": (
            hard_negative_metrics.model_dump() if hard_negative_metrics else None
        ),
        "hard_negative_v2_metrics": (
            hard_negative_v2_metrics.model_dump()
            if hard_negative_v2_metrics else None
        ),
        "hard_negative_by_category": hard_negative_categories,
        "token_results": token_results,
        "token_format_summary": token_format_pct,
        "qa_summary": qa_summary,
        "qa_results": [r.model_dump() for r in qa_results],
        "abergowrie_regression": {
            "predicted_is_kv": ab_outcome.predicted_is_kv,
            "predicted_record_count": ab_outcome.predicted_record_count,
            "pass": ab_outcome.predicted_is_kv and ab_outcome.predicted_record_count == 2,
        },
    }

    (output_dir / "kv_eval_results.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (output_dir / "detector_lock_v2.json").write_text(
        json.dumps(lock_v2.model_dump(), indent=2), encoding="utf-8"
    )

    return summary


def _per_category_metrics(cases, outcomes) -> dict:
    """Bucket cases by negative_reason and return per-category counts."""
    by_reason: dict[str, dict] = {}
    idx = {o.case_id: o for o in outcomes}
    for c in cases:
        gt = c.ground_truth
        reason = gt.negative_reason or ("promoted" if gt.is_key_value_group else "unspecified")
        bucket = by_reason.setdefault(reason, {"total": 0, "predicted_pos": 0, "predicted_neg": 0})
        bucket["total"] += 1
        o = idx.get(gt.case_id)
        if o and o.predicted_is_kv:
            bucket["predicted_pos"] += 1
        else:
            bucket["predicted_neg"] += 1
    return by_reason


def _evaluate_xlsx_cases(xlsx_cases) -> CorpusMetrics | None:
    if not xlsx_cases:
        return None
    from benchmarks.kv_eval.evaluator import compute_corpus_metrics, evaluate_xlsx_case
    outcomes = []
    gt_map = {}
    for case in xlsx_cases:
        try:
            outcome = evaluate_xlsx_case(case.xlsx_path, case.ground_truth)
            outcomes.append(outcome)
            gt_map[case.ground_truth.case_id] = case.ground_truth
        except Exception:
            pass
    if not outcomes:
        return None
    return compute_corpus_metrics(outcomes, gt_map, "native_xlsx_kv")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="benchmarks/kv_eval/results")
    args = parser.parse_args()
    summary = run_evaluation(Path(args.output))
    output = {
        "inline_v1": {
            "precision": round(summary["inline_metrics"]["precision"], 3),
            "recall": round(summary["inline_metrics"]["recall"], 3),
            "f1": round(summary["inline_metrics"]["f1"], 3),
            "fpr": round(summary["inline_metrics"]["fpr"], 3),
        },
        "inline_v2": {
            "precision": round(summary["inline_v2_metrics"]["precision"], 3),
            "recall": round(summary["inline_v2_metrics"]["recall"], 3),
            "f1": round(summary["inline_v2_metrics"]["f1"], 3),
            "fpr": round(summary["inline_v2_metrics"]["fpr"], 3),
        },
        "default_profile": {
            "precision": round(summary["default_profile_metrics"]["precision"], 3),
            "recall": round(summary["default_profile_metrics"]["recall"], 3),
            "fpr": round(summary["default_profile_metrics"]["fpr"], 3),
        },
        "html": {
            "tp": summary["html_metrics"]["tp"],
            "fp": summary["html_metrics"]["fp"],
            "fn": summary["html_metrics"]["fn"],
            "tn": summary["html_metrics"]["tn"],
            "precision": round(summary["html_metrics"]["precision"], 3),
            "recall": round(summary["html_metrics"]["recall"], 3),
        },
        "abergowrie_pass": summary["abergowrie_regression"]["pass"],
        "token_format_summary": summary.get("token_format_summary", {}),
    }
    if summary.get("hard_negative_metrics"):
        hn = summary["hard_negative_metrics"]
        output["hard_negative_v1"] = {
            "tp": hn["tp"], "fp": hn["fp"], "fn": hn["fn"], "tn": hn["tn"],
            "precision": round(hn["precision"], 3),
            "recall": round(hn["recall"], 3),
            "fpr": round(hn["fpr"], 3),
        }
    if summary.get("hard_negative_v2_metrics"):
        hn = summary["hard_negative_v2_metrics"]
        output["hard_negative_v2"] = {
            "tp": hn["tp"], "fp": hn["fp"], "fn": hn["fn"], "tn": hn["tn"],
            "precision": round(hn["precision"], 3),
            "recall": round(hn["recall"], 3),
            "fpr": round(hn["fpr"], 3),
        }
    if summary.get("adjacent_real_metrics"):
        ar = summary["adjacent_real_metrics"]
        output["adjacent_real_v1"] = {
            "tp": ar["tp"], "fp": ar["fp"], "fn": ar["fn"], "tn": ar["tn"],
            "precision": round(ar["precision"], 3),
            "recall": round(ar["recall"], 3),
        }
    if summary.get("adjacent_real_v2_metrics"):
        ar = summary["adjacent_real_v2_metrics"]
        output["adjacent_real_v2"] = {
            "tp": ar["tp"], "fp": ar["fp"], "fn": ar["fn"], "tn": ar["tn"],
            "precision": round(ar["precision"], 3),
            "recall": round(ar["recall"], 3),
        }
    if summary.get("adjacent_threshold_real"):
        output["adjacent_threshold_real"] = {
            k: {
                "tp": v["tp"], "fp": v["fp"], "fn": v["fn"], "tn": v["tn"],
                "precision": round(v["precision"], 3),
                "recall": round(v["recall"], 3),
            } for k, v in summary["adjacent_threshold_real"].items()
        }
    if summary.get("xlsx_metrics"):
        xm = summary["xlsx_metrics"]
        output["xlsx"] = {
            "tp": xm["tp"], "fp": xm["fp"], "fn": xm["fn"], "tn": xm["tn"],
            "precision": round(xm["precision"], 3),
            "recall": round(xm["recall"], 3),
        }
    if summary.get("qa_summary"):
        output["qa_summary"] = summary["qa_summary"]
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
