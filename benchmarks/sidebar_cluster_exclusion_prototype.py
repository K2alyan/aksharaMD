"""Offline cluster-exclusion + baseline recomputation prototype (Issue #50).

Analysis-only. **No production code changes.** Reimplements the
block-level multicolumn detector's ``_analyse_page`` in benchmark
code so the analysis can run in three modes on the same page:

1. **Baseline** — exactly what the shipped detector computes.
2. **Blanket suppression under H6** — if the page's smaller cluster
   satisfies the H6 sidebar signature, force ``warn=False`` for the
   whole page. This is the UNSAFE rule the review addendum rejected.
3. **Cluster exclusion + recomputation** — if the smaller cluster
   satisfies the H6 sidebar signature, drop only those blocks and
   re-run the baseline signals on the remaining blocks. Warn only
   if the recomputed baseline still fires.

The reimplemented ``_analyse_page`` (see ``_compute_baseline_signals``)
mirrors ``aksharamd/plugins/validators/multicolumn.py :: _analyse_page``
at commit ``71c4916`` line-for-line so the analysis is faithful to
what the shipped detector would see. It is validated against the
frozen phase-1 output in ``tests/test_sidebar_cluster_exclusion_prototype.py``.

Input page format (matching the fixture generator):

    {
      "name": str,
      "page_width": float,
      "page_height": float,
      "blocks": [
          {"index": int, "type": str, "x0": float | None, "y0": float | None,
           "chars": int, "words": int, "content": str},
          ...
      ],
    }

The prototype never modifies its input.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ── Reimplemented baseline signals ──────────────────────────────────────


# BlockType values the shipped validator excludes from positional analysis.
_EXCLUDED_TYPES = {"table", "image", "footnote"}


def _find_column_gap(x_vals: list[float]) -> tuple[float, float, float]:
    """Return (gap_size, gap_midpoint, x_range). Matches the shipped
    validator's ``_find_column_gap`` semantics: sort x values and pick
    the biggest successive delta.
    """
    if len(x_vals) < 2:
        return 0.0, 0.0, 0.0
    xs = sorted(x_vals)
    x_range = xs[-1] - xs[0]
    biggest_gap = 0.0
    gap_mid = xs[0]
    for a, b in zip(xs[:-1], xs[1:]):
        gap = b - a
        if gap > biggest_gap:
            biggest_gap = gap
            gap_mid = (a + b) / 2.0
    return biggest_gap, gap_mid, x_range


def _positional_blocks(blocks: list[dict]) -> list[dict]:
    return [
        b for b in blocks
        if b.get("x0") is not None and b.get("y0") is not None
        and b.get("type") not in _EXCLUDED_TYPES
    ]


def _compute_baseline_signals(blocks: list[dict], page_width: float) -> dict[str, Any]:
    """Reimplements ``aksharamd/plugins/validators/multicolumn.py::_analyse_page``
    on a plain block dict list. The intent is faithful reproduction —
    if the shipped algorithm changes upstream, this function must be
    updated in lockstep. Verified against the frozen phase-1 output.
    """
    positional = _positional_blocks(blocks)
    result: dict[str, Any] = {
        "total_blocks": len(positional),
        "gap_size": 0.0,
        "gap_rel": 0.0,
        "transition_rate": 0.0,
        "large_y_drops": 0,
        "short_frac": 0.0,
        "signals": [],
        "warn": False,
    }
    if len(positional) < 5:
        return result

    x_vals = [b["x0"] for b in positional]
    y_vals = [b["y0"] for b in positional]

    gap_size, gap_mid, x_range = _find_column_gap(x_vals)
    gap_rel = gap_size / x_range if x_range > 0 else 0.0
    result["gap_size"] = round(gap_size, 1)
    result["gap_rel"] = round(gap_rel, 2)

    if gap_rel < 0.15 or gap_size < 60:
        return result

    clusters = [0 if b["x0"] < gap_mid else 1 for b in positional]
    transitions = sum(1 for i in range(1, len(clusters)) if clusters[i] != clusters[i - 1])
    transition_rate = transitions / max(len(clusters) - 1, 1)
    result["transition_rate"] = round(transition_rate, 2)

    y_diffs = [y_vals[i + 1] - y_vals[i] for i in range(len(y_vals) - 1)]
    large_drops = sum(1 for d in y_diffs if d < -40)
    result["large_y_drops"] = large_drops

    short = sum(1 for b in positional if len((b.get("content") or "").split()) < 8)
    short_frac = short / len(positional)
    result["short_frac"] = round(short_frac, 2)

    signals: list[str] = []
    if transition_rate >= 0.28:
        signals.append(f"high_transition_rate={transition_rate:.2f}")
    if large_drops == 0 and transition_rate >= 0.25:
        signals.append("y_monotonic_with_transitions")
    if short_frac >= 0.55 and transition_rate >= 0.20:
        signals.append(f"short_frac={short_frac:.2f}")
    result["signals"] = signals

    if any(s.startswith("high_transition_rate") for s in signals):
        result["warn"] = True
    elif len(signals) >= 2:
        result["warn"] = True
    return result


# ── Cluster geometry (mirrors sidebar_multicolumn_signal_analysis.py) ────


def _cross_cluster_metrics(blocks: list[dict], page_width: float, page_height: float) -> dict[str, Any]:
    positional = _positional_blocks(blocks)
    if len(positional) < 2:
        return {}
    x_vals = [b["x0"] for b in positional]
    _gap, gap_mid, _range = _find_column_gap(x_vals)
    clusters: dict[int, list[dict]] = {0: [], 1: []}
    for b in positional:
        c = 0 if b["x0"] < gap_mid else 1
        clusters[c].append(b)
    if not clusters[0] or not clusters[1]:
        return {}
    # Smaller cluster = fewer chars.
    per_cluster_chars = {c: sum(b["chars"] for b in blks) for c, blks in clusters.items()}
    smaller = 0 if per_cluster_chars[0] <= per_cluster_chars[1] else 1
    larger = 1 - smaller
    total_chars = sum(per_cluster_chars.values()) or 0
    total_words = sum(b["words"] for b in positional) or 0
    s_chars = per_cluster_chars[smaller]
    s_words = sum(b["words"] for b in clusters[smaller])
    text_share_smaller = (s_chars / total_chars) if total_chars else 0.0
    words_share_smaller = (s_words / total_words) if total_words else 0.0
    # Vertical coverage of smaller cluster (y0 range / page_height).
    s_ys = sorted(b["y0"] for b in clusters[smaller])
    l_ys = sorted(b["y0"] for b in clusters[larger])
    s_range = (s_ys[-1] - s_ys[0]) if len(s_ys) >= 2 else 0.0
    l_range = (l_ys[-1] - l_ys[0]) if len(l_ys) >= 2 else 0.0
    cov = (s_range / page_height) if page_height > 0 else 0.0
    top_delta = abs(s_ys[0] - l_ys[0]) if s_ys and l_ys else None
    bot_delta = abs(s_ys[-1] - l_ys[-1]) if s_ys and l_ys else None
    y_overlap = 0.0
    if s_ys and l_ys and min(s_range, l_range) > 0:
        lo = max(s_ys[0], l_ys[0])
        hi = min(s_ys[-1], l_ys[-1])
        overlap = max(0.0, hi - lo)
        y_overlap = overlap / min(s_range, l_range)
    # Substantial alternations: filter to "substantial" text blocks
    # (>=5 words, meaningful type).
    substantial_types = {"paragraph", "list", "blockquote", "table", "code_block", "math", "key_value_group"}
    ordered = sorted(positional, key=lambda b: (b["y0"], b["x0"]))
    substantial = [b for b in ordered
                   if b["type"] in substantial_types and b["words"] >= 5]
    seq = [0 if b["x0"] < gap_mid else 1 for b in substantial]
    alt_substantial = sum(1 for a, b in zip(seq[:-1], seq[1:]) if a != b)
    return {
        "smaller_cluster": smaller,
        "larger_cluster": larger,
        "text_share_smaller": text_share_smaller,
        "words_share_smaller": words_share_smaller,
        "smaller_y_coverage_frac": cov,
        "y_overlap_frac": y_overlap,
        "top_alignment_delta": top_delta,
        "bottom_alignment_delta": bot_delta,
        "alternations_substantial": alt_substantial,
        "smaller_cluster_block_ids": [b["index"] for b in clusters[smaller]],
        "gap_mid": gap_mid,
    }


# ── H6 sidebar test + prototype modes ────────────────────────────────────


def _matches_h6_signature(cc: dict[str, Any], share_max: float = 0.020,
                          cov_min: float = 0.40, alt_max: int = 0) -> bool:
    if not cc:
        return False
    share = cc.get("text_share_smaller")
    cov = cc.get("smaller_y_coverage_frac")
    alt = cc.get("alternations_substantial")
    if share is None or cov is None or alt is None:
        return False
    return share <= share_max and cov >= cov_min and alt <= alt_max


def evaluate_page(page: dict[str, Any]) -> dict[str, Any]:
    """Run all three modes on one page.

    Returns:

        {
          "baseline": {..., "warn": bool},
          "cross_cluster": {...},
          "h6_matches": bool,
          "blanket_suppression_warn": bool,
          "cluster_exclusion_warn": bool,
          "excluded_block_ids": [int, ...]  # empty if no exclusion applied
        }
    """
    blocks = page["blocks"]
    page_width = page["page_width"]
    page_height = page["page_height"]

    baseline = _compute_baseline_signals(blocks, page_width)
    cc = _cross_cluster_metrics(blocks, page_width, page_height)
    h6 = _matches_h6_signature(cc)

    # Blanket suppression: silence if the H6 signature matches AND the
    # baseline warned. Otherwise leave the baseline alone.
    blanket = baseline["warn"] and not h6

    # Cluster exclusion: drop the smaller-cluster blocks (only) and
    # recompute baseline. Apply exclusion ONLY if H6 matches AND the
    # baseline warned.
    excluded_ids: list[int] = []
    if baseline["warn"] and h6:
        excluded_ids = list(cc.get("smaller_cluster_block_ids") or [])
        remaining = [b for b in blocks if b["index"] not in set(excluded_ids)]
        recomputed = _compute_baseline_signals(remaining, page_width)
        cluster_exclusion_warn = recomputed["warn"]
        # Also expose the recomputed signals.
    else:
        cluster_exclusion_warn = baseline["warn"]
        recomputed = None

    return {
        "baseline": baseline,
        "cross_cluster": cc,
        "h6_matches": h6,
        "blanket_suppression_warn": blanket,
        "cluster_exclusion_warn": cluster_exclusion_warn,
        "excluded_block_ids": excluded_ids,
        "recomputed_signals": recomputed,
    }


# ── Acceptance-gate replay ──────────────────────────────────────────────


def replay_fixtures(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    """Run every fixture through all three modes. Returns a summary and
    checks the acceptance gate.
    """
    results: list[dict[str, Any]] = []
    for f in fixtures:
        r = evaluate_page(f)
        results.append({
            "name": f["name"],
            "expected_baseline_warn": f["expected_baseline_warn"],
            "expected_cluster_exclusion_warn": f["expected_cluster_exclusion_warn"],
            "expected_multicolumn": f["expected_multicolumn"],
            **r,
            "notes": f["notes"],
        })

    # Acceptance gate: every fixture's actual verdicts must match the
    # ``expected_*`` fields the generator declared. Any drift is a
    # failure — either the fixture is wrong or the prototype is.
    gate_reasons: list[str] = []
    for r in results:
        exp_baseline = r.get("expected_baseline_warn")
        exp_cluster = r.get("expected_cluster_exclusion_warn")
        if r["baseline"]["warn"] != exp_baseline:
            gate_reasons.append(
                f"{r['name']}: baseline warn={r['baseline']['warn']} "
                f"differs from expected {exp_baseline}"
            )
        if r["cluster_exclusion_warn"] != exp_cluster:
            gate_reasons.append(
                f"{r['name']}: cluster_exclusion_warn={r['cluster_exclusion_warn']} "
                f"differs from expected {exp_cluster}"
            )

    # Discriminative check: the mixed fixture must have blanket
    # suppression FALSE (incorrectly silences) AND cluster exclusion
    # TRUE (correctly preserves) — the whole point of the fixture.
    named = {r["name"]: r for r in results}
    mixed = named.get("mixed_multicolumn_and_sidebar_page")
    if mixed is not None:
        if mixed["blanket_suppression_warn"] is not False:
            gate_reasons.append(
                "mixed_multicolumn_and_sidebar_page: blanket suppression did NOT "
                "silence — the fixture is not exercising the H6 match; strengthen "
                "the sidebar's H6 signature to make the demonstration discriminative."
            )
        if mixed["cluster_exclusion_warn"] is not True:
            gate_reasons.append(
                "mixed_multicolumn_and_sidebar_page: cluster exclusion did NOT "
                "preserve the genuine warning — the fixture's post-exclusion "
                "geometry does not fire the baseline detector; strengthen the "
                "two-column body signal."
            )
    return {
        "results": results,
        "acceptance_gate_pass": len(gate_reasons) == 0,
        "acceptance_gate_reasons": gate_reasons,
    }


def _current_commit() -> str:
    import subprocess  # nosec B404 - reads local git head only
    try:
        return subprocess.check_output(  # nosec B603 B607 - local git head
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True,
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    import argparse

    from benchmarks.sidebar_multicolumn_fixtures import (  # type: ignore
        FIXTURES,
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks" / "SIDEBAR_FIXTURES_REPORT_2026-07-19.json",
    )
    args = ap.parse_args()
    fixtures = [f() for f in FIXTURES]
    replay = replay_fixtures(fixtures)
    payload = {
        "harness_version": "sidebar_cluster_exclusion_prototype.py@2026-07-19",
        "commit_under_evaluation": _current_commit(),
        "fixtures": fixtures,
        **replay,
    }
    args.output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.output} — acceptance_gate_pass={replay['acceptance_gate_pass']}")
    if not replay["acceptance_gate_pass"]:
        for r in replay["acceptance_gate_reasons"]:
            print(f"  reason: {r}")
        return 41
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
