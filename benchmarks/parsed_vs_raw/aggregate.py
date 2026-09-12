"""Aggregate per-(doc, question, arm) rows into per-arm summary metrics.

The core claim we are trying to verify is: for parser arms,
``correlation(readiness_score, correctness) > 0``. We report both
Pearson (linear) and Spearman (rank) coefficients since the readiness
score is discretized in 5-point bands and rank correlation is more
robust to that.
"""
from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .types import ArmResult


@dataclass
class ArmSummary:
    arm: str
    n: int
    n_scored: int
    mean_correctness: float
    mean_readiness: float | None
    pearson: float | None
    spearman: float | None


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation. Returns ``None`` for degenerate inputs."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if denom_x == 0.0 or denom_y == 0.0:
        return None
    return num / (denom_x * denom_y)


def _ranks(values: Sequence[float]) -> list[float]:
    """Average-rank assignment for ties (matches scipy.stats.rankdata)."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1  # 1-based ranks
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def summarise(results: Iterable[ArmResult]) -> list[ArmSummary]:
    """Group per-arm and compute mean correctness + correlations."""
    by_arm: dict[str, list[ArmResult]] = {}
    for row in results:
        by_arm.setdefault(row.arm, []).append(row)
    out: list[ArmSummary] = []
    for arm, rows in sorted(by_arm.items()):
        scored = [r for r in rows if r.judge_score >= 0]
        mean_corr = (sum(r.correctness for r in scored) / len(scored)) if scored else 0.0
        readiness_pairs = [
            (float(r.readiness_score), r.correctness)
            for r in scored
            if r.readiness_score is not None
        ]
        if readiness_pairs:
            xs = [x for x, _ in readiness_pairs]
            ys = [y for _, y in readiness_pairs]
            pearson = _pearson(xs, ys)
            spearman = _spearman(xs, ys)
            mean_read: float | None = sum(xs) / len(xs)
        else:
            pearson = spearman = None
            mean_read = None
        out.append(
            ArmSummary(
                arm=arm,
                n=len(rows),
                n_scored=len(scored),
                mean_correctness=mean_corr,
                mean_readiness=mean_read,
                pearson=pearson,
                spearman=spearman,
            )
        )
    return out


# -- I/O ------------------------------------------------------------------


_CSV_HEADER = [
    "doc_id",
    "arm",
    "question",
    "gold_answer",
    "answer",
    "readiness_score",
    "judge_score",
    "correctness",
    "error",
]


def write_rows_csv(results: Iterable[ArmResult], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        for r in results:
            writer.writerow([
                r.doc_id,
                r.arm,
                r.question,
                r.gold_answer,
                r.answer,
                "" if r.readiness_score is None else r.readiness_score,
                r.judge_score,
                f"{r.correctness:.4f}",
                r.error,
            ])


def write_summary_markdown(summaries: Sequence[ArmSummary], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Parsed-vs-raw pilot summary",
        "",
        "| arm | n | scored | mean_correctness | mean_readiness | pearson | spearman |",
        "|-----|---|--------|------------------|----------------|---------|----------|",
    ]
    for s in summaries:
        lines.append(
            "| {arm} | {n} | {ns} | {mc:.3f} | {mr} | {p} | {sp} |".format(
                arm=s.arm,
                n=s.n,
                ns=s.n_scored,
                mc=s.mean_correctness,
                mr="n/a" if s.mean_readiness is None else f"{s.mean_readiness:.2f}",
                p="n/a" if s.pearson is None else f"{s.pearson:.3f}",
                sp="n/a" if s.spearman is None else f"{s.spearman:.3f}",
            )
        )
    lines.append("")
    lines.append(
        "Positive Spearman on parser arms is the evidence Doc 2's proposal "
        "predicts. Values near zero (or negative) would mean the readiness "
        "score is not a useful proxy for downstream answer quality on this "
        "corpus and would motivate revisiting the scoring policy."
    )
    target.write_text("\n".join(lines), encoding="utf-8")
