"""PDF layout-complexity evaluator (Policy v1).

Pure evaluator: consumes the neutral
:class:`~aksharamd.plugins.parsers.layout_complexity.LayoutDocumentFeatures`
value type from the feature-model module and returns a structured
:class:`LayoutComplexityDecision`.

Contract
--------

* No I/O, no parser imports beyond the feature model. The evaluator
  never touches ``pdf.py``, fitz, or the filesystem.
* Deterministic. Identical
  :class:`~aksharamd.plugins.parsers.layout_complexity.LayoutDocumentFeatures`
  inputs must produce equal :class:`LayoutComplexityDecision` outputs.
* All weights and thresholds are versioned constants declared in this
  module. Any semantic change to a constant requires bumping
  :data:`LAYOUT_COMPLEXITY_POLICY_VERSION`. The decision carries the
  version so calibration reports (Commit 3) and any future Auto Policy
  path (Commit 4) can pin against a specific evaluator vintage.
* Not wired into Auto routing by this commit. Nothing in
  ``pdf.py`` / ``auto_selector.py`` calls this module. The manifest
  schema is unchanged.

Signal design
-------------

Six independent per-page signals, each with a per-page cap AND a
document-level cap. Caps prevent any single unbounded feature (most
notably ``rejected_table_candidate_count``) from dominating the score:

* ``multi_column`` — ``column_count >= 2``.
* ``table`` — ``table_count >= 1``, contribution scales with count.
* ``figure_caption`` — ``figure_caption_hit_count >= 1``, contribution
  scales with count.
* ``fragmented_text`` — many short spans (density signal for laid-out
  figures, forms, sidebars).
* ``mixed_content`` — a page with substantial text AND substantial
  image area (a genuinely mixed page, not a pure scan).
* ``rejected_table_candidate`` — the parser's table quality gate
  rejected candidate regions. Contribution scales with count but is
  bounded at both the per-page and document levels; the milestone
  spec calls this out as a signal that MUST NOT dominate the score
  ahead of empirical calibration.

Every trigger threshold and weight below has been chosen to keep an
ordinary single-column native-text page at score 0 and to keep an
image-only scan (no text layer, no detected tables) at score 0. Both
are validated by explicit tests. The bands are heuristic and will be
recalibrated against real scientific PDFs in Commit 3.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from aksharamd.plugins.parsers.layout_complexity import (
    LayoutDocumentFeatures,
    LayoutPageFeatures,
)

# ---------------------------------------------------------------------------
# Policy version. Bump whenever any weight, cap, threshold, trigger, band
# boundary, or the aggregation shape changes. The version rides on every
# LayoutComplexityDecision so downstream (calibration reports, Auto Policy
# v2) can pin against a specific vintage.
# ---------------------------------------------------------------------------
LAYOUT_COMPLEXITY_POLICY_VERSION = "1"

# ---------------------------------------------------------------------------
# Per-page signal triggers. A signal fires for a page only when the
# corresponding threshold is met, and only then does the page contribute to
# that signal's total. All thresholds are inclusive lower bounds.
# ---------------------------------------------------------------------------
MULTI_COLUMN_MIN_COLUMNS = 2
TABLE_MIN_COUNT = 1
FIGURE_CAPTION_MIN_COUNT = 1
# Fragmented text = many spans on the page (real prose) AND the mean span
# is very short. Requiring both prevents a scan (span_count = 0) and a
# small figure caption (span_count low) from tripping this signal.
FRAGMENTED_TEXT_MAX_MEAN_SPAN_CHAR_LENGTH = 8.0
FRAGMENTED_TEXT_MIN_SPAN_COUNT = 40
# Mixed content requires BOTH real text AND significant image area on the
# same page. A pure scan (no text layer) has no page_char_count, so the
# min-chars requirement keeps scans out of this signal.
MIXED_CONTENT_MIN_IMAGE_AREA_RATIO = 0.15
MIXED_CONTENT_MIN_PAGE_CHAR_COUNT = 100
REJECTED_TABLE_CANDIDATE_MIN_COUNT = 1

# ---------------------------------------------------------------------------
# Per-page contributions when a signal fires. "Weight" here means "score
# points added to the document total when the signal contributes for one
# page (or one qualifying item on that page, where the signal scales with
# count — see per-page caps below)". Points, not percentages.
# ---------------------------------------------------------------------------
WEIGHT_MULTI_COLUMN = 6.0
WEIGHT_TABLE_PER_COUNT = 5.0
WEIGHT_FIGURE_CAPTION_PER_COUNT = 2.0
WEIGHT_FRAGMENTED_TEXT = 5.0
WEIGHT_MIXED_CONTENT = 6.0
WEIGHT_REJECTED_TABLE_CANDIDATE_PER_COUNT = 1.0

# ---------------------------------------------------------------------------
# Per-page caps for signals whose per-page contribution scales with a count.
# These are the FIRST line of defense against a single pathological page
# dominating the score — e.g. a page reporting 10000 rejected table
# candidates. The per-page contribution is capped BEFORE it accumulates
# into the document total.
# ---------------------------------------------------------------------------
TABLE_PER_PAGE_CAP_COUNT = 3
FIGURE_CAPTION_PER_PAGE_CAP_COUNT = 3
REJECTED_TABLE_CANDIDATE_PER_PAGE_CAP_COUNT = 5

# ---------------------------------------------------------------------------
# Document-level cumulative caps per signal. Second line of defense: even
# a long document that fires the same signal on many pages cannot let one
# signal dominate the total score. The CAP for rejected_table_candidate is
# deliberately conservative pending empirical calibration in Commit 3.
# ---------------------------------------------------------------------------
CAP_MULTI_COLUMN = 25.0
CAP_TABLE = 30.0
CAP_FIGURE_CAPTION = 15.0
CAP_FRAGMENTED_TEXT = 20.0
CAP_MIXED_CONTENT = 20.0
CAP_REJECTED_TABLE_CANDIDATE = 15.0

# ---------------------------------------------------------------------------
# Score bounds and band boundaries. Score is clipped to [SCORE_MIN,
# SCORE_MAX] after all signal contributions are summed. Bands are chosen
# so that a document with no signals is simple, a document with one or
# two clear signals lands in moderate, and a document with several
# co-occurring signals across many pages lands in complex.
# ---------------------------------------------------------------------------
SCORE_MIN = 0.0
SCORE_MAX = 100.0
BAND_SIMPLE_MAX_SCORE = 30.0
BAND_MODERATE_MAX_SCORE = 60.0

# ---------------------------------------------------------------------------
# Top-contributing-pages window. Small integer, not tunable per-call.
# ---------------------------------------------------------------------------
TOP_CONTRIBUTING_PAGE_COUNT = 3

# ---------------------------------------------------------------------------
# Signal name vocabulary. Fixed strings so downstream reports can pattern
# match without importing this module.
# ---------------------------------------------------------------------------
SIGNAL_MULTI_COLUMN = "multi_column"
SIGNAL_TABLE = "table"
SIGNAL_FIGURE_CAPTION = "figure_caption"
SIGNAL_FRAGMENTED_TEXT = "fragmented_text"
SIGNAL_MIXED_CONTENT = "mixed_content"
SIGNAL_REJECTED_TABLE_CANDIDATE = "rejected_table_candidate"

_ALL_SIGNALS: tuple[str, ...] = (
    SIGNAL_MULTI_COLUMN,
    SIGNAL_TABLE,
    SIGNAL_FIGURE_CAPTION,
    SIGNAL_FRAGMENTED_TEXT,
    SIGNAL_MIXED_CONTENT,
    SIGNAL_REJECTED_TABLE_CANDIDATE,
)

_SIGNAL_CAPS: Mapping[str, float] = MappingProxyType(
    {
        SIGNAL_MULTI_COLUMN: CAP_MULTI_COLUMN,
        SIGNAL_TABLE: CAP_TABLE,
        SIGNAL_FIGURE_CAPTION: CAP_FIGURE_CAPTION,
        SIGNAL_FRAGMENTED_TEXT: CAP_FRAGMENTED_TEXT,
        SIGNAL_MIXED_CONTENT: CAP_MIXED_CONTENT,
        SIGNAL_REJECTED_TABLE_CANDIDATE: CAP_REJECTED_TABLE_CANDIDATE,
    }
)

# ---------------------------------------------------------------------------
# Band names.
# ---------------------------------------------------------------------------
BAND_SIMPLE = "simple"
BAND_MODERATE = "moderate"
BAND_COMPLEX = "complex"


@dataclass(frozen=True)
class LayoutPageContribution:
    """A single page's contribution to the document complexity score.

    Only pages with ``contribution > 0`` are ever surfaced. The
    ``triggered_signals`` tuple lists the signals in a stable order
    (declaration order in :data:`_ALL_SIGNALS`) so identical inputs
    yield identical decisions.
    """

    page_index: int
    contribution: float
    triggered_signals: tuple[str, ...]


@dataclass(frozen=True)
class LayoutComplexityDecision:
    """Structured record of one layout-complexity evaluation.

    Field notes:

    * ``score`` is a float in ``[SCORE_MIN, SCORE_MAX]``, i.e. 0-100.
    * ``band`` is one of :data:`BAND_SIMPLE`, :data:`BAND_MODERATE`,
      :data:`BAND_COMPLEX`.
    * ``triggered_signals`` lists the signals whose (post-cap)
      contribution to the document score is non-zero, in the fixed
      order of :data:`_ALL_SIGNALS`.
    * ``measurements`` is a read-only mapping of raw + post-cap per-signal
      contributions and per-signal triggering page counts. It is the
      auditable trail: it explains why the score is what it is.
    * ``top_contributing_pages`` are up to
      :data:`TOP_CONTRIBUTING_PAGE_COUNT` pages ranked by contribution
      descending, with page_index ascending as the deterministic
      tiebreaker.
    * ``policy_version`` is
      :data:`LAYOUT_COMPLEXITY_POLICY_VERSION` at evaluation time.
    * ``extractor_version`` is passed through from the input
      :class:`LayoutDocumentFeatures`; a calibration report can pin
      against the feature vintage as well as the evaluator vintage.
    * ``reason`` is a short single-line human-readable summary suitable
      for a benchmark report or a log line. It is derived from the
      structured fields, not a source of truth.
    """

    score: float
    band: str
    triggered_signals: tuple[str, ...]
    measurements: Mapping[str, float]
    top_contributing_pages: tuple[LayoutPageContribution, ...]
    policy_version: str
    extractor_version: str
    reason: str


def evaluate_layout_complexity(
    features: LayoutDocumentFeatures,
) -> LayoutComplexityDecision:
    """Score a document's layout complexity and return a structured decision.

    Pure function of ``features``. See module docstring for the signal
    catalog, cap policy, and determinism contract.
    """
    per_signal_raw: dict[str, float] = {s: 0.0 for s in _ALL_SIGNALS}
    per_signal_page_count: dict[str, int] = {s: 0 for s in _ALL_SIGNALS}
    page_contributions: list[LayoutPageContribution] = []

    for page in features.pages:
        contrib, signals = _evaluate_page(page)
        for signal, points in contrib.items():
            if points > 0.0:
                per_signal_raw[signal] += points
                per_signal_page_count[signal] += 1
        if signals:
            total = sum(contrib.values())
            page_contributions.append(
                LayoutPageContribution(
                    page_index=page.page_index,
                    contribution=total,
                    triggered_signals=signals,
                )
            )

    per_signal_capped: dict[str, float] = {
        signal: min(per_signal_raw[signal], _SIGNAL_CAPS[signal])
        for signal in _ALL_SIGNALS
    }

    raw_score = sum(per_signal_capped.values())
    score = max(SCORE_MIN, min(SCORE_MAX, raw_score))

    if score < BAND_SIMPLE_MAX_SCORE:
        band = BAND_SIMPLE
    elif score < BAND_MODERATE_MAX_SCORE:
        band = BAND_MODERATE
    else:
        band = BAND_COMPLEX

    triggered = tuple(
        signal for signal in _ALL_SIGNALS if per_signal_capped[signal] > 0.0
    )

    top_pages = tuple(
        sorted(
            page_contributions,
            key=lambda c: (-c.contribution, c.page_index),
        )[:TOP_CONTRIBUTING_PAGE_COUNT]
    )

    measurements = _build_measurements(
        per_signal_raw=per_signal_raw,
        per_signal_capped=per_signal_capped,
        per_signal_page_count=per_signal_page_count,
        total_pages=features.total_pages,
        score=score,
    )

    reason = _format_reason(band=band, score=score, triggered=triggered)

    return LayoutComplexityDecision(
        score=score,
        band=band,
        triggered_signals=triggered,
        measurements=measurements,
        top_contributing_pages=top_pages,
        policy_version=LAYOUT_COMPLEXITY_POLICY_VERSION,
        extractor_version=features.extractor_version,
        reason=reason,
    )


def _evaluate_page(
    page: LayoutPageFeatures,
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Compute per-signal per-page contributions for one page.

    Returns a mapping of every signal to its per-page contribution
    (zero when the signal did not fire) and a tuple of the signal
    names that fired, in :data:`_ALL_SIGNALS` order.
    """
    contrib: dict[str, float] = {s: 0.0 for s in _ALL_SIGNALS}
    fired: list[str] = []

    if page.column_count >= MULTI_COLUMN_MIN_COLUMNS:
        contrib[SIGNAL_MULTI_COLUMN] = WEIGHT_MULTI_COLUMN
        fired.append(SIGNAL_MULTI_COLUMN)

    if page.table_count >= TABLE_MIN_COUNT:
        capped_count = min(page.table_count, TABLE_PER_PAGE_CAP_COUNT)
        contrib[SIGNAL_TABLE] = WEIGHT_TABLE_PER_COUNT * capped_count
        fired.append(SIGNAL_TABLE)

    if page.figure_caption_hit_count >= FIGURE_CAPTION_MIN_COUNT:
        capped_count = min(
            page.figure_caption_hit_count, FIGURE_CAPTION_PER_PAGE_CAP_COUNT
        )
        contrib[SIGNAL_FIGURE_CAPTION] = (
            WEIGHT_FIGURE_CAPTION_PER_COUNT * capped_count
        )
        fired.append(SIGNAL_FIGURE_CAPTION)

    if (
        page.span_count >= FRAGMENTED_TEXT_MIN_SPAN_COUNT
        and 0.0 < page.mean_span_char_length
        <= FRAGMENTED_TEXT_MAX_MEAN_SPAN_CHAR_LENGTH
    ):
        contrib[SIGNAL_FRAGMENTED_TEXT] = WEIGHT_FRAGMENTED_TEXT
        fired.append(SIGNAL_FRAGMENTED_TEXT)

    if (
        page.page_char_count >= MIXED_CONTENT_MIN_PAGE_CHAR_COUNT
        and page.image_area_ratio >= MIXED_CONTENT_MIN_IMAGE_AREA_RATIO
    ):
        contrib[SIGNAL_MIXED_CONTENT] = WEIGHT_MIXED_CONTENT
        fired.append(SIGNAL_MIXED_CONTENT)

    if (
        page.rejected_table_candidate_count
        >= REJECTED_TABLE_CANDIDATE_MIN_COUNT
    ):
        capped_count = min(
            page.rejected_table_candidate_count,
            REJECTED_TABLE_CANDIDATE_PER_PAGE_CAP_COUNT,
        )
        contrib[SIGNAL_REJECTED_TABLE_CANDIDATE] = (
            WEIGHT_REJECTED_TABLE_CANDIDATE_PER_COUNT * capped_count
        )
        fired.append(SIGNAL_REJECTED_TABLE_CANDIDATE)

    return contrib, tuple(fired)


def _build_measurements(
    *,
    per_signal_raw: dict[str, float],
    per_signal_capped: dict[str, float],
    per_signal_page_count: dict[str, int],
    total_pages: int,
    score: float,
) -> Mapping[str, float]:
    payload: dict[str, float] = {
        "total_pages": float(total_pages),
        "score": score,
    }
    for signal in _ALL_SIGNALS:
        payload[f"{signal}.raw_score"] = per_signal_raw[signal]
        payload[f"{signal}.score"] = per_signal_capped[signal]
        payload[f"{signal}.page_count"] = float(per_signal_page_count[signal])
        payload[f"{signal}.cap"] = _SIGNAL_CAPS[signal]
    return MappingProxyType(payload)


def _format_reason(
    *, band: str, score: float, triggered: tuple[str, ...]
) -> str:
    if not triggered:
        return f"{band} layout: score={score:.1f}/100, no complexity signals triggered"
    return (
        f"{band} layout: score={score:.1f}/100, "
        f"signals=[{', '.join(triggered)}]"
    )


__all__ = [
    "LAYOUT_COMPLEXITY_POLICY_VERSION",
    "LayoutComplexityDecision",
    "LayoutPageContribution",
    "evaluate_layout_complexity",
]
