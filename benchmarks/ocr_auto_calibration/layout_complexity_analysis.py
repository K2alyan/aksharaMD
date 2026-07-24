"""Layout-complexity vs OCR-difficulty analysis (Commit 3 of the
Layout Complexity v1 milestone).

Consumes a list of :class:`LayoutComplexityCapture` records and
produces structured analysis payloads. Evidence only — no production
routing decisions, no manifest writes.

Analyses
--------

1. :func:`layout_vs_ocr_table` — per-doc cross-tabulation of layout
   complexity (band, score, top signals) against OCR difficulty
   (has_ocr_pixmap fraction, native-text char count). This is the
   scientific-corpus caveat the milestone explicitly calls out: a
   native-text arXiv paper is layout-complex but OCR-simple; the
   analysis MUST distinguish the two.

2. :func:`false_positive_report` — flags documents whose layout
   complexity band is ``moderate`` or ``complex`` but whose
   OCR-required page fraction is below a threshold. These are the
   docs that a naive "route to UOC when layout is complex" rule
   would send to UOC unnecessarily. Includes the reason each doc was
   flagged.

3. :func:`rejected_table_candidate_predictor` — evaluates whether the
   raw ``rejected_table_candidate_count`` correlates with the
   OCR-required page fraction. Reports Pearson r AND a rank-order
   agreement flag; both are only informative on a corpus of 5+ docs.
   When the corpus is smaller, returns ``correlation_available=False``
   so the caller reports "insufficient sample" rather than false
   precision.

Nothing here consumes an actual OCR-treatment result. Once real
Tesseract vs UOC captures exist, a follow-up analysis can attach
observed backend-benefit deltas to each doc row; the current shape
is designed to accept that extension without a schema break.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from aksharamd.plugins.parsers.layout_complexity_evaluator import (
    BAND_COMPLEX,
    BAND_MODERATE,
    BAND_SIMPLE,
    SIGNAL_REJECTED_TABLE_CANDIDATE,
)
from benchmarks.ocr_auto_calibration.layout_complexity_capture import (
    LayoutComplexityCapture,
    per_signal_page_counts,
)

# ---------------------------------------------------------------------------
# Analysis policy version. Bump when any threshold below or the shape of the
# emitted analysis payloads changes.
# ---------------------------------------------------------------------------
LAYOUT_COMPLEXITY_ANALYSIS_VERSION = "1"

# False-positive threshold: a doc classified moderate/complex layout while
# fewer than this fraction of its pages actually require OCR is flagged.
# The bar is DELIBERATELY generous (10%) — the intent is to surface
# candidates for human review, not to make a routing decision.
FALSE_POSITIVE_OCR_FRACTION_MAX = 0.10

# Minimum char count below which we no longer trust ``ocr_required_fraction``
# to distinguish "native text" from "image-only scan" — very short docs are
# noisy either way and are excluded from the false-positive tally.
FALSE_POSITIVE_MIN_TOTAL_CHARS = 2000

# Minimum sample size for a meaningful Pearson correlation. Below this,
# report the raw pairs and mark the correlation as unavailable.
CORRELATION_MIN_SAMPLE_SIZE = 5


@dataclass(frozen=True)
class DocumentRow:
    """One row of :func:`layout_vs_ocr_table` — the atomic evidence unit."""

    document_id: str
    total_pages: int
    page_char_count_total: int
    ocr_required_page_count: int
    ocr_required_fraction: float
    layout_score: float
    layout_band: str
    triggered_signals: tuple[str, ...]
    rejected_table_candidate_total: int
    per_signal_page_counts: Mapping[str, int]
    top_contributing_page_indices: tuple[int, ...]
    is_native_text_dominant: bool


@dataclass(frozen=True)
class LayoutVsOcrTable:
    rows: tuple[DocumentRow, ...]
    summary: Mapping[str, float]


@dataclass(frozen=True)
class FalsePositiveEntry:
    document_id: str
    layout_band: str
    layout_score: float
    ocr_required_fraction: float
    page_char_count_total: int
    triggered_signals: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class FalsePositiveReport:
    entries: tuple[FalsePositiveEntry, ...]
    total_documents_considered: int
    documents_excluded_short: int
    threshold_ocr_fraction_max: float
    threshold_min_total_chars: int


@dataclass(frozen=True)
class RejectedTableCandidatePairs:
    """Raw evidence — per-doc rejected-count and OCR-required fraction."""

    pairs: tuple[tuple[str, int, float], ...] = field(default_factory=tuple)
    correlation_available: bool = False
    pearson_r: float | None = None
    interpretation: str = ""


@dataclass(frozen=True)
class LayoutComplexityAnalysis:
    analysis_version: str
    layout_vs_ocr: LayoutVsOcrTable
    false_positives: FalsePositiveReport
    rejected_table_predictor: RejectedTableCandidatePairs


def _is_native_text_dominant(capture: LayoutComplexityCapture) -> bool:
    """A doc is native-text-dominant when it has substantial native
    text AND a very low OCR-required-page fraction. Used to separate
    the "native scientific paper" case from the "scanned document"
    case in the report.
    """
    return (
        capture.page_char_count_total >= FALSE_POSITIVE_MIN_TOTAL_CHARS
        and capture.ocr_required_fraction <= FALSE_POSITIVE_OCR_FRACTION_MAX
    )


def layout_vs_ocr_table(
    captures: list[LayoutComplexityCapture],
) -> LayoutVsOcrTable:
    rows: list[DocumentRow] = []
    for capture in captures:
        counts = per_signal_page_counts(capture)
        top_page_indices = tuple(
            p.page_index for p in capture.decision.top_contributing_pages
        )
        rows.append(
            DocumentRow(
                document_id=capture.document_id,
                total_pages=capture.total_pages,
                page_char_count_total=capture.page_char_count_total,
                ocr_required_page_count=capture.ocr_required_page_count,
                ocr_required_fraction=capture.ocr_required_fraction,
                layout_score=capture.decision.score,
                layout_band=capture.decision.band,
                triggered_signals=capture.decision.triggered_signals,
                rejected_table_candidate_total=capture.rejected_table_candidate_total,
                per_signal_page_counts=counts,
                top_contributing_page_indices=top_page_indices,
                is_native_text_dominant=_is_native_text_dominant(capture),
            )
        )

    summary = _table_summary(rows)
    return LayoutVsOcrTable(rows=tuple(rows), summary=summary)


def _table_summary(rows: list[DocumentRow]) -> Mapping[str, float]:
    n = len(rows)
    if n == 0:
        return MappingProxyType({"documents": 0.0})

    band_counts = {BAND_SIMPLE: 0, BAND_MODERATE: 0, BAND_COMPLEX: 0}
    native_text_dominant = 0
    layout_scores: list[float] = []
    ocr_fractions: list[float] = []
    for row in rows:
        band_counts[row.layout_band] = band_counts.get(row.layout_band, 0) + 1
        if row.is_native_text_dominant:
            native_text_dominant += 1
        layout_scores.append(row.layout_score)
        ocr_fractions.append(row.ocr_required_fraction)

    return MappingProxyType(
        {
            "documents": float(n),
            "band.simple_count": float(band_counts[BAND_SIMPLE]),
            "band.moderate_count": float(band_counts[BAND_MODERATE]),
            "band.complex_count": float(band_counts[BAND_COMPLEX]),
            "native_text_dominant_count": float(native_text_dominant),
            "layout_score.mean": statistics.mean(layout_scores),
            "layout_score.min": min(layout_scores),
            "layout_score.max": max(layout_scores),
            "ocr_required_fraction.mean": statistics.mean(ocr_fractions),
            "ocr_required_fraction.min": min(ocr_fractions),
            "ocr_required_fraction.max": max(ocr_fractions),
        }
    )


def false_positive_report(
    captures: list[LayoutComplexityCapture],
) -> FalsePositiveReport:
    """Docs classified moderate/complex layout with an OCR-required
    fraction below the threshold AND substantial native text.

    A ``simple`` layout classification is never a false positive here
    — this analysis focuses on the case the milestone caveat calls
    out: layout-complex scientific PDFs that a naive routing rule
    would push to UOC without any OCR benefit.
    """
    entries: list[FalsePositiveEntry] = []
    excluded_short = 0
    considered = 0
    for capture in captures:
        if capture.page_char_count_total < FALSE_POSITIVE_MIN_TOTAL_CHARS:
            excluded_short += 1
            continue
        considered += 1
        if capture.decision.band == BAND_SIMPLE:
            continue
        if capture.ocr_required_fraction > FALSE_POSITIVE_OCR_FRACTION_MAX:
            continue
        entries.append(
            FalsePositiveEntry(
                document_id=capture.document_id,
                layout_band=capture.decision.band,
                layout_score=capture.decision.score,
                ocr_required_fraction=capture.ocr_required_fraction,
                page_char_count_total=capture.page_char_count_total,
                triggered_signals=capture.decision.triggered_signals,
                reason=(
                    f"native-text-dominant document ({capture.page_char_count_total} chars, "
                    f"OCR fraction {capture.ocr_required_fraction:.3f} "
                    f"<= {FALSE_POSITIVE_OCR_FRACTION_MAX:.2f}) classified "
                    f"'{capture.decision.band}' by layout signals "
                    f"{list(capture.decision.triggered_signals)}. Routing on layout "
                    f"complexity alone would send this to UOC without OCR benefit."
                ),
            )
        )
    return FalsePositiveReport(
        entries=tuple(entries),
        total_documents_considered=considered,
        documents_excluded_short=excluded_short,
        threshold_ocr_fraction_max=FALSE_POSITIVE_OCR_FRACTION_MAX,
        threshold_min_total_chars=FALSE_POSITIVE_MIN_TOTAL_CHARS,
    )


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Return Pearson r or ``None`` when undefined (constant series)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denom == 0.0:
        return None
    num = sum(a * b for a, b in zip(dx, dy))
    return num / denom


def rejected_table_candidate_predictor(
    captures: list[LayoutComplexityCapture],
) -> RejectedTableCandidatePairs:
    """Report the per-doc pairs ``(rejected_table_candidate_total,
    ocr_required_fraction)`` and, if the corpus is large enough, the
    Pearson correlation.

    Rationale: the milestone spec explicitly calls out
    ``rejected_table_candidate_count`` as a candidate UOC-benefit
    predictor. Real evidence for that hypothesis requires a labeled
    UOC-vs-Tesseract structural gain, which is out of scope for this
    evidence commit (a heavy-model run). This function surfaces the
    raw pairs plus an interim proxy — correlation with the OCR-required
    fraction — so a reviewer can spot whether the two even move
    together on this corpus before commissioning the heavier run.

    A weak or inverted correlation on real scientific PDFs is
    informative: it would mean the signal fires on native-text pages
    where the parser's quality gate over-rejects candidates, not on
    the scanned pages where UOC would actually help.
    """
    pairs = tuple(
        (
            capture.document_id,
            capture.rejected_table_candidate_total,
            capture.ocr_required_fraction,
        )
        for capture in captures
    )

    if len(pairs) < CORRELATION_MIN_SAMPLE_SIZE:
        return RejectedTableCandidatePairs(
            pairs=pairs,
            correlation_available=False,
            pearson_r=None,
            interpretation=(
                f"insufficient sample: need at least "
                f"{CORRELATION_MIN_SAMPLE_SIZE} documents; "
                f"corpus has {len(pairs)}"
            ),
        )

    xs = [float(p[1]) for p in pairs]
    ys = [float(p[2]) for p in pairs]
    r = _pearson_r(xs, ys)
    if r is None:
        return RejectedTableCandidatePairs(
            pairs=pairs,
            correlation_available=False,
            pearson_r=None,
            interpretation=(
                "Pearson r undefined (constant series); "
                "rejected_table_candidate_count or ocr_required_fraction "
                "is invariant across the corpus"
            ),
        )

    if r >= 0.5:
        interpretation = (
            f"positive correlation (r={r:.3f}) — rejected_table_candidate_count "
            f"tracks OCR difficulty on this corpus. A UOC-vs-Tesseract "
            f"structural-gain run would be worth commissioning."
        )
    elif r <= -0.5:
        interpretation = (
            f"negative correlation (r={r:.3f}) — rejected_table_candidate_count "
            f"is higher on OCR-simple documents on this corpus. The signal "
            f"is a poor UOC-benefit predictor as measured here; the "
            f"conservative Commit 2 cap is justified."
        )
    else:
        interpretation = (
            f"weak correlation (r={r:.3f}) — rejected_table_candidate_count "
            f"does not clearly track OCR difficulty on this corpus. Treat "
            f"the signal as under-calibrated pending a UOC-vs-Tesseract run."
        )

    _ = SIGNAL_REJECTED_TABLE_CANDIDATE  # module reference for grep-ability
    return RejectedTableCandidatePairs(
        pairs=pairs,
        correlation_available=True,
        pearson_r=r,
        interpretation=interpretation,
    )


def analyze(
    captures: list[LayoutComplexityCapture],
) -> LayoutComplexityAnalysis:
    return LayoutComplexityAnalysis(
        analysis_version=LAYOUT_COMPLEXITY_ANALYSIS_VERSION,
        layout_vs_ocr=layout_vs_ocr_table(captures),
        false_positives=false_positive_report(captures),
        rejected_table_predictor=rejected_table_candidate_predictor(captures),
    )


__all__ = [
    "CORRELATION_MIN_SAMPLE_SIZE",
    "DocumentRow",
    "FALSE_POSITIVE_MIN_TOTAL_CHARS",
    "FALSE_POSITIVE_OCR_FRACTION_MAX",
    "FalsePositiveEntry",
    "FalsePositiveReport",
    "LAYOUT_COMPLEXITY_ANALYSIS_VERSION",
    "LayoutComplexityAnalysis",
    "LayoutVsOcrTable",
    "RejectedTableCandidatePairs",
    "analyze",
    "false_positive_report",
    "layout_vs_ocr_table",
    "rejected_table_candidate_predictor",
]
