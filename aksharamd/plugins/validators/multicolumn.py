"""Multi-column reading-order validator.

Emits W_MULTICOLUMN_ORDER when extracted block sequence shows signs of
column interleaving — not merely because the page has multiple columns.

Root cause: _detect_column_boundaries() may miss multi-column layout (returning
boundaries=[]) or detect the wrong boundary positions. When detection fails,
spans are sorted by y globally, so left and right column content is interleaved
in y-order. When boundaries are wrong, content from different columns is
mixed within detected columns.

Detection approach (independent of the PDF parser's boundary detection):
  1. For each page, find the largest x0 gap in the middle 25-75% of x-range.
     A gap ≥15% of the x-range signals a two-column structure.
  2. Classify blocks into two clusters at that gap midpoint.
  3. Compute the TRANSITION RATE — how often consecutive blocks switch clusters.
     - Low rate (<15%): blocks are grouped by column — correct column-first order.
     - High rate (>35%): blocks alternate left-right-left — y-first (interleaved) order.
  4. Also check y-monotonicity: a correct column sort has ONE large y-drop (the
     column transition), while y-interleaved output has none.

Signals emitted:
  W_MULTICOLUMN_ORDER — multiple geometry signals agree on interleaving.

This is observational only — no readiness score cap is applied here.
Calibration against the 21-doc corpus should determine whether a cap is justified.

Requires block.metadata["x0"] / ["y0"] populated by the PDF parser, and
doc.metadata["pdf_column_info"] populated with page_width for each page.
"""
from __future__ import annotations

from ...context import CompilationContext
from ...models.block import BlockType
from ..base import ValidatorPlugin
from ..registry import register_plugin


def _find_column_gap(
    x_values: list[float],
) -> tuple[float, float, float]:
    """
    Return (gap_size, gap_midpoint, x_range) for the largest x gap in the
    middle 25-75% of the x distribution.

    gap_size is in absolute coordinates (PDF points).
    Returns (0, 0, 0) if no significant gap is found.
    """
    if len(x_values) < 4:
        return 0.0, 0.0, 0.0

    sorted_x = sorted(set(round(x, 1) for x in x_values))
    x_range = sorted_x[-1] - sorted_x[0]
    if x_range < 50:  # page too narrow to have columns
        return 0.0, 0.0, x_range

    best_gap = 0.0
    best_mid = 0.0
    for i in range(1, len(sorted_x)):
        gap = sorted_x[i] - sorted_x[i - 1]
        mid = (sorted_x[i - 1] + sorted_x[i]) / 2
        rel_mid = (mid - sorted_x[0]) / x_range
        if gap > best_gap and 0.20 < rel_mid < 0.80:
            best_gap = gap
            best_mid = mid

    return best_gap, best_mid, x_range


def _analyse_page(blocks: list, page_width: float) -> dict:
    """
    Analyse block sequence on a page for column interleaving.

    The key insight: a CORRECT column sort groups all left-column blocks before
    right-column blocks (low transition rate, one large y-drop at column switch).
    A FAILED sort (spans sorted by y) produces alternating clusters with no y-drop.

    Returns a dict with geometry signals and a 'warn' boolean.
    """
    positional = [
        b for b in blocks
        if b.metadata.get("x0") is not None and b.metadata.get("y0") is not None
        and b.type not in (BlockType.TABLE, BlockType.IMAGE, BlockType.FOOTNOTE)
    ]
    result: dict = {
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

    x_vals = [b.metadata["x0"] for b in positional]
    y_vals = [b.metadata["y0"] for b in positional]

    gap_size, gap_mid, x_range = _find_column_gap(x_vals)
    gap_rel = gap_size / x_range if x_range > 0 else 0.0
    result["gap_size"] = round(gap_size, 1)
    result["gap_rel"] = round(gap_rel, 2)

    # No significant bimodal distribution → single column, skip
    if gap_rel < 0.15 or gap_size < 60:
        return result

    # Classify each block to left (0) or right (1) cluster
    clusters = [0 if b.metadata["x0"] < gap_mid else 1 for b in positional]

    transitions = sum(1 for i in range(1, len(clusters)) if clusters[i] != clusters[i - 1])
    transition_rate = transitions / max(len(clusters) - 1, 1)
    result["transition_rate"] = round(transition_rate, 2)

    # Count large y-drops — a correct column sort has exactly 1 (the column boundary)
    y_diffs = [y_vals[i + 1] - y_vals[i] for i in range(len(y_vals) - 1)]
    large_drops = sum(1 for d in y_diffs if d < -40)
    result["large_y_drops"] = large_drops

    # Short block fraction
    short = sum(1 for b in positional if len((b.content or "").split()) < 8)
    short_frac = short / len(positional)
    result["short_frac"] = round(short_frac, 2)

    # ── Signal evaluation ─────────────────────────────────────────────────────
    signals = []

    # Primary signal: high transition rate (blocks alternate between clusters)
    # A correct sort has transition_rate ≈ 1/n (one switch for n blocks per col).
    # Interleaved output has transition_rate ≈ 0.5+ (switches every other block).
    # Threshold 0.28: catches 3colpres (0.30) while leaving eastbaytimes (0.25) clean.
    # Calibrated on 21-doc corpus v2; see benchmarks/MULTICOLUMN_OBSERVATION_REPORT_V2.md.
    if transition_rate >= 0.28:
        signals.append(f"high_transition_rate={transition_rate:.2f}")

    # Confirming signal: y values are monotonic (no column-break y-drop)
    # Correct column sort: one large y-drop when moving from col 0 to col 1.
    # y-sorted interleaved output: no large drops (y increases throughout).
    if large_drops == 0 and transition_rate >= 0.25:
        signals.append("y_monotonic_with_transitions")

    # Supporting signal: many short fragments (suggestive, not sufficient alone)
    if short_frac >= 0.55 and transition_rate >= 0.20:
        signals.append(f"short_frac={short_frac:.2f}")

    result["signals"] = signals

    # Warn when the primary signal fires, OR when primary + one supporting signal agree
    if "high_transition_rate" in " ".join(signals):
        result["warn"] = True
    elif len(signals) >= 2:
        result["warn"] = True

    return result


class MultiColumnOrderValidator(ValidatorPlugin):
    name = "multicolumn_order_validator"
    priority = 35
    # Maturity: CANDIDATE — 2 known positives (3colpres, 4c), 2 explicit controls clean,
    # precision 100% on 21-doc corpus. Known FN class: span-level interleaving (ikea3,
    # elpais, simple2) is undetectable at block level. Does not affect readiness score.
    # Phase 1 re-score (2026-07-13): precision 100%, recall 40% (2/5 ordering targets).
    warning_maturity = "candidate"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document
        # pdf_column_info now contains ALL pages (not just multi-column detected ones).
        # We use it only for page_width; the cluster analysis is independent.
        column_info_raw = doc.metadata.get("pdf_column_info", {})

        # Group blocks by page
        blocks_by_page: dict[int, list] = {}
        for block in doc.blocks:
            pg = block.page or 0
            blocks_by_page.setdefault(pg, []).append(block)

        page_analyses: list[dict] = []
        problem_pages: list[int] = []

        for pg, page_blocks in blocks_by_page.items():
            col_info = column_info_raw.get(pg) or column_info_raw.get(str(pg), {})
            page_width: float = col_info.get("page_width", 0.0)
            if page_width == 0.0:
                continue

            analysis = _analyse_page(page_blocks, page_width)
            analysis["page"] = pg
            page_analyses.append(analysis)

            if analysis["warn"]:
                problem_pages.append(pg)

        if not problem_pages:
            # Store diagnostics even when no warning fires (for observational mode)
            doc.metadata["multicolumn_diagnostics"] = {
                "problem_pages": [],
                "page_analyses": page_analyses,
                "warned": False,
                "warning_maturity": self.warning_maturity,
            }
            return ctx

        # Aggregate for warning message
        max_tr = max((a["transition_rate"] for a in page_analyses if a["page"] in problem_pages), default=0)
        all_signals = []
        for a in page_analyses:
            if a["page"] in problem_pages:
                all_signals.extend(a["signals"])
        signal_summary = "; ".join(sorted(set(all_signals)))
        page_list = ", ".join(f"p{p}" for p in sorted(problem_pages))

        ctx.warn(
            "W_MULTICOLUMN_ORDER",
            (
                f"Multi-column reading order likely incorrect on {len(problem_pages)} "
                f"page(s) ({page_list}). "
                f"Max transition_rate={max_tr:.2f}. "
                f"Signals: {signal_summary}. "
                "Content from adjacent columns may be interleaved."
            ),
        )

        doc.metadata["multicolumn_diagnostics"] = {
            "problem_pages": problem_pages,
            "page_analyses": page_analyses,
            "warned": True,
            "warning_maturity": self.warning_maturity,
        }

        return ctx


register_plugin(MultiColumnOrderValidator)
