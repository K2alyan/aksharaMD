"""Layout-complexity evaluator (Policy v1) unit tests.

Commit 2 of the layout-complexity milestone introduces the pure
evaluator that consumes
:class:`~aksharamd.plugins.parsers.layout_complexity.LayoutDocumentFeatures`
and returns a structured
:class:`~aksharamd.plugins.parsers.layout_complexity_evaluator.LayoutComplexityDecision`.

These tests pin the contract the milestone spec requires:

* ordinary digital text stays simple;
* a simple scan does NOT automatically classify as complex;
* every signal (multi_column, table, figure_caption, fragmented_text,
  mixed_content, rejected_table_candidate) contributes independently;
* no single unbounded count can dominate the score;
* identical features always produce identical decisions (determinism);
* the score is always in ``[0, 100]``;
* document aggregation identifies the correct top-contributing pages.

Nothing here calls :mod:`aksharamd.plugins.parsers.pdf`. The evaluator
depends on the neutral feature surface only, so these tests assemble
:class:`LayoutPageFeatures` / :class:`LayoutDocumentFeatures` by hand
rather than driving synthetic PDFs through the bridge.
"""
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from aksharamd.plugins.parsers.layout_complexity import (
    LAYOUT_FEATURE_EXTRACTOR_VERSION,
    LayoutDocumentFeatures,
    LayoutPageFeatures,
)
from aksharamd.plugins.parsers.layout_complexity_evaluator import (
    BAND_COMPLEX,
    BAND_MODERATE,
    BAND_MODERATE_MAX_SCORE,
    BAND_SIMPLE,
    BAND_SIMPLE_MAX_SCORE,
    CAP_REJECTED_TABLE_CANDIDATE,
    LAYOUT_COMPLEXITY_POLICY_VERSION,
    SCORE_MAX,
    SCORE_MIN,
    SIGNAL_FIGURE_CAPTION,
    SIGNAL_FRAGMENTED_TEXT,
    SIGNAL_MIXED_CONTENT,
    SIGNAL_MULTI_COLUMN,
    SIGNAL_REJECTED_TABLE_CANDIDATE,
    SIGNAL_TABLE,
    TOP_CONTRIBUTING_PAGE_COUNT,
    LayoutComplexityDecision,
    LayoutPageContribution,
    evaluate_layout_complexity,
)

# ── Fixture helpers ──────────────────────────────────────────────────


def _neutral_page(page_index: int = 0, **overrides: object) -> LayoutPageFeatures:
    """Return a LayoutPageFeatures with a single-column, text-only, no-signal
    baseline. Individual tests override just the fields they want to
    exercise so each test is easy to read in isolation."""
    base = LayoutPageFeatures(
        page_index=page_index,
        page_width=612.0,
        page_height=792.0,
        page_char_count=2000,
        span_count=50,
        mean_span_char_length=40.0,
        has_ocr_pixmap=False,
        image_count=0,
        image_area_ratio=0.0,
        table_count=0,
        rejected_table_candidate_count=0,
        column_count=1,
        math_bbox_count=0,
        figure_caption_hit_count=0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _doc(*pages: LayoutPageFeatures) -> LayoutDocumentFeatures:
    return LayoutDocumentFeatures(pages=tuple(pages))


# ── Baseline: ordinary digital text is simple ────────────────────────


def test_ordinary_digital_text_page_is_simple() -> None:
    """A single-column, native-text page with no images, no tables, and
    no figure captions must produce a score of 0 in the ``simple`` band
    with no triggered signals."""
    decision = evaluate_layout_complexity(_doc(_neutral_page()))

    assert decision.score == 0.0
    assert decision.band == BAND_SIMPLE
    assert decision.triggered_signals == ()
    assert decision.top_contributing_pages == ()
    assert decision.policy_version == LAYOUT_COMPLEXITY_POLICY_VERSION
    assert decision.extractor_version == LAYOUT_FEATURE_EXTRACTOR_VERSION
    assert "no complexity signals" in decision.reason


def test_multi_page_native_text_document_is_simple() -> None:
    """Ten pages of ordinary prose — no signals across the whole
    document. The band stays simple; nothing about being long triggers
    complexity."""
    decision = evaluate_layout_complexity(
        _doc(*(_neutral_page(page_index=i) for i in range(10)))
    )
    assert decision.score == 0.0
    assert decision.band == BAND_SIMPLE
    assert decision.triggered_signals == ()


# ── Baseline: a simple scan does NOT auto-classify as complex ────────


def test_simple_scan_is_not_classified_as_complex() -> None:
    """An image-only scanned page — no text layer, so no page_char_count
    and no spans. The parser has flagged the page for rasterization
    (``has_ocr_pixmap=True``) and the page area is entirely image. The
    evaluator MUST NOT infer complexity from image coverage alone: no
    text means no mixed_content signal, no spans means no
    fragmented_text signal, and column detection defaults to 1."""
    scan_page = _neutral_page(
        page_char_count=0,
        span_count=0,
        mean_span_char_length=0.0,
        has_ocr_pixmap=True,
        image_count=1,
        image_area_ratio=1.0,
        column_count=1,
    )
    decision = evaluate_layout_complexity(_doc(scan_page))

    assert decision.score == 0.0
    assert decision.band == BAND_SIMPLE
    assert decision.triggered_signals == ()


def test_multi_page_simple_scan_document_is_not_complex() -> None:
    scan_page_kwargs = dict(
        page_char_count=0,
        span_count=0,
        mean_span_char_length=0.0,
        has_ocr_pixmap=True,
        image_count=1,
        image_area_ratio=1.0,
    )
    decision = evaluate_layout_complexity(
        _doc(
            *(
                _neutral_page(page_index=i, **scan_page_kwargs)  # type: ignore[arg-type]
                for i in range(20)
            )
        )
    )
    assert decision.score == 0.0
    assert decision.band == BAND_SIMPLE


# ── Each signal contributes independently ────────────────────────────


def test_multi_column_signal_contributes_independently() -> None:
    decision = evaluate_layout_complexity(
        _doc(_neutral_page(column_count=2))
    )
    assert decision.triggered_signals == (SIGNAL_MULTI_COLUMN,)
    assert decision.score > 0.0
    assert decision.measurements[f"{SIGNAL_MULTI_COLUMN}.page_count"] == 1.0


def test_table_signal_contributes_independently() -> None:
    decision = evaluate_layout_complexity(
        _doc(_neutral_page(table_count=1))
    )
    assert decision.triggered_signals == (SIGNAL_TABLE,)
    assert decision.score > 0.0
    assert decision.measurements[f"{SIGNAL_TABLE}.page_count"] == 1.0


def test_figure_caption_signal_contributes_independently() -> None:
    decision = evaluate_layout_complexity(
        _doc(_neutral_page(figure_caption_hit_count=1))
    )
    assert decision.triggered_signals == (SIGNAL_FIGURE_CAPTION,)
    assert decision.score > 0.0


def test_fragmented_text_signal_contributes_independently() -> None:
    """Many spans, each very short. This is the "form / label grid /
    sidebar" signature. Ordinary prose has long spans (~40 chars mean)
    and does not trigger."""
    decision = evaluate_layout_complexity(
        _doc(
            _neutral_page(
                span_count=120,
                mean_span_char_length=4.0,
                page_char_count=480,
            )
        )
    )
    assert decision.triggered_signals == (SIGNAL_FRAGMENTED_TEXT,)
    assert decision.score > 0.0


def test_mixed_content_signal_contributes_independently() -> None:
    """A page with substantial native text AND a substantial image area
    (a genuinely mixed page — not a pure scan, not a pure text page)."""
    decision = evaluate_layout_complexity(
        _doc(
            _neutral_page(
                page_char_count=1500,
                image_count=1,
                image_area_ratio=0.4,
            )
        )
    )
    assert decision.triggered_signals == (SIGNAL_MIXED_CONTENT,)
    assert decision.score > 0.0


def test_rejected_table_signal_contributes_independently() -> None:
    decision = evaluate_layout_complexity(
        _doc(_neutral_page(rejected_table_candidate_count=1))
    )
    assert decision.triggered_signals == (SIGNAL_REJECTED_TABLE_CANDIDATE,)
    assert decision.score > 0.0


def test_signal_triggers_have_stable_order() -> None:
    """When several signals fire together the triggered_signals tuple
    must present them in the fixed declaration order — this is part of
    the determinism contract."""
    page = _neutral_page(
        column_count=2,
        table_count=1,
        figure_caption_hit_count=1,
        rejected_table_candidate_count=1,
    )
    decision = evaluate_layout_complexity(_doc(page))
    assert decision.triggered_signals == (
        SIGNAL_MULTI_COLUMN,
        SIGNAL_TABLE,
        SIGNAL_FIGURE_CAPTION,
        SIGNAL_REJECTED_TABLE_CANDIDATE,
    )


# ── No unbounded count can dominate the score ────────────────────────


def test_rejected_table_candidate_contribution_is_capped_by_document_cap() -> None:
    """The regression the milestone spec calls out explicitly. Even a
    document that reports an absurd rejected_table_candidate_count
    across many pages must not let this one signal dominate the score:
    its total post-cap contribution is bounded by
    :data:`CAP_REJECTED_TABLE_CANDIDATE`."""
    pathological_pages = [
        _neutral_page(page_index=i, rejected_table_candidate_count=10_000)
        for i in range(50)
    ]
    decision = evaluate_layout_complexity(_doc(*pathological_pages))

    signal_score = decision.measurements[
        f"{SIGNAL_REJECTED_TABLE_CANDIDATE}.score"
    ]
    raw_signal_score = decision.measurements[
        f"{SIGNAL_REJECTED_TABLE_CANDIDATE}.raw_score"
    ]
    assert signal_score == CAP_REJECTED_TABLE_CANDIDATE
    assert raw_signal_score > CAP_REJECTED_TABLE_CANDIDATE
    # Score comes entirely from the one signal, which is capped, so the
    # total score cannot exceed the cap either.
    assert decision.score == CAP_REJECTED_TABLE_CANDIDATE


def test_pathological_single_page_rejected_count_is_bounded_by_per_page_cap() -> None:
    """The per-page cap is a first line of defense: a single page with
    10 000 rejected candidates cannot contribute more than one page's
    worth to the signal total."""
    decision = evaluate_layout_complexity(
        _doc(_neutral_page(rejected_table_candidate_count=10_000))
    )
    # Whatever the per-page cap resolves to, the score for this signal
    # on this single page must be strictly less than the document cap
    # AND strictly less than the raw count. Otherwise the per-page cap
    # isn't doing any work.
    signal_score = decision.measurements[
        f"{SIGNAL_REJECTED_TABLE_CANDIDATE}.score"
    ]
    assert signal_score < CAP_REJECTED_TABLE_CANDIDATE
    assert signal_score < 10_000


def test_table_count_per_page_cap_bounds_pathological_table_count() -> None:
    """Same shape of test for another counting signal — a page reporting
    100 tables shouldn't dominate. The per-page cap keeps its
    contribution modest even before the document cap kicks in."""
    decision_pathological = evaluate_layout_complexity(
        _doc(_neutral_page(table_count=100))
    )
    decision_reasonable = evaluate_layout_complexity(
        _doc(_neutral_page(table_count=3))
    )
    # The reasonable case must reach the per-page cap already, so the
    # pathological case can produce no more score contribution.
    assert (
        decision_pathological.measurements[f"{SIGNAL_TABLE}.score"]
        == decision_reasonable.measurements[f"{SIGNAL_TABLE}.score"]
    )


# ── Score bounds ─────────────────────────────────────────────────────


def test_score_within_0_to_100_on_empty_document() -> None:
    decision = evaluate_layout_complexity(_doc())
    assert SCORE_MIN <= decision.score <= SCORE_MAX
    assert decision.score == 0.0


def test_score_within_0_to_100_on_adversarial_all_signals_document() -> None:
    """Every signal firing on every one of many pages. The score must
    saturate at SCORE_MAX; it must never exceed it because every signal
    has a cap and the sum of caps is bounded, but the clip is the last
    line of defense."""
    heavy_page_kwargs = dict(
        page_char_count=5000,
        span_count=200,
        mean_span_char_length=3.0,
        image_count=5,
        image_area_ratio=0.6,
        table_count=10,
        rejected_table_candidate_count=1000,
        column_count=3,
        figure_caption_hit_count=10,
    )
    pages = [
        _neutral_page(page_index=i, **heavy_page_kwargs)  # type: ignore[arg-type]
        for i in range(30)
    ]
    decision = evaluate_layout_complexity(_doc(*pages))

    assert SCORE_MIN <= decision.score <= SCORE_MAX
    assert decision.band == BAND_COMPLEX


def test_band_boundaries_match_declared_thresholds() -> None:
    """A configuration that just barely crosses SIMPLE_MAX_SCORE lands
    in moderate; a configuration well past MODERATE_MAX_SCORE lands in
    complex. The band function is boundary-inclusive at the high end:
    ``simple`` when ``score < SIMPLE_MAX``, ``moderate`` when
    ``score < MODERATE_MAX``, ``complex`` otherwise."""
    # A single multi_column page contributes < SIMPLE_MAX; still simple.
    small = evaluate_layout_complexity(_doc(_neutral_page(column_count=2)))
    assert small.band == BAND_SIMPLE
    assert small.score < BAND_SIMPLE_MAX_SCORE

    # A page with several stacked signals should land in the moderate
    # or complex band depending on how many signals fired.
    stacked = evaluate_layout_complexity(
        _doc(
            _neutral_page(
                column_count=2,
                table_count=3,
                figure_caption_hit_count=3,
                rejected_table_candidate_count=5,
                page_char_count=2000,
                image_count=2,
                image_area_ratio=0.4,
            )
        )
    )
    assert stacked.band in {BAND_MODERATE, BAND_COMPLEX}
    assert stacked.score >= BAND_SIMPLE_MAX_SCORE

    # A many-page document with many signals firing must reach complex.
    very_stacked = evaluate_layout_complexity(
        _doc(
            *(
                _neutral_page(
                    page_index=i,
                    column_count=2,
                    table_count=3,
                    figure_caption_hit_count=3,
                    rejected_table_candidate_count=5,
                    page_char_count=2000,
                    image_count=2,
                    image_area_ratio=0.4,
                )
                for i in range(20)
            )
        )
    )
    assert very_stacked.band == BAND_COMPLEX
    assert very_stacked.score >= BAND_MODERATE_MAX_SCORE


# ── Determinism ──────────────────────────────────────────────────────


def test_identical_features_produce_identical_decisions() -> None:
    """The determinism contract: same input → equal output (dataclass
    ``__eq__`` on frozen dataclasses is structural)."""
    features = _doc(
        _neutral_page(page_index=0, column_count=2, table_count=1),
        _neutral_page(page_index=1, figure_caption_hit_count=2),
        _neutral_page(page_index=2, rejected_table_candidate_count=3),
    )
    first = evaluate_layout_complexity(features)
    second = evaluate_layout_complexity(features)

    assert first == second
    assert first.triggered_signals == second.triggered_signals
    assert first.top_contributing_pages == second.top_contributing_pages
    # measurements is a MappingProxyType view; compare as dicts.
    assert dict(first.measurements) == dict(second.measurements)


def test_decision_is_frozen() -> None:
    decision = evaluate_layout_complexity(_doc(_neutral_page()))
    try:
        decision.score = 99.0  # type: ignore[misc]
    except Exception:
        # ``dataclasses.FrozenInstanceError`` is a subclass of
        # ``AttributeError``; either flavor of failure is acceptable.
        return
    raise AssertionError("LayoutComplexityDecision must be frozen")


def test_measurements_mapping_is_read_only() -> None:
    decision = evaluate_layout_complexity(_doc(_neutral_page()))
    assert isinstance(decision.measurements, MappingProxyType)
    try:
        decision.measurements["score"] = 999.0  # type: ignore[index]
    except TypeError:
        return
    raise AssertionError("measurements must be a read-only mapping")


# ── Top-contributing pages ──────────────────────────────────────────


def test_top_contributing_pages_ranks_by_contribution_descending() -> None:
    """A document with pages of different complexity must surface the
    highest-contributing pages first, capped at
    :data:`TOP_CONTRIBUTING_PAGE_COUNT`."""
    pages = [
        # Page 0: no signals — should NOT appear in top.
        _neutral_page(page_index=0),
        # Page 1: one signal.
        _neutral_page(page_index=1, column_count=2),
        # Page 2: two signals — higher contribution than page 1.
        _neutral_page(page_index=2, column_count=2, table_count=1),
        # Page 3: three signals — highest.
        _neutral_page(
            page_index=3,
            column_count=2,
            table_count=2,
            figure_caption_hit_count=2,
        ),
        # Page 4: one signal.
        _neutral_page(page_index=4, figure_caption_hit_count=1),
    ]
    decision = evaluate_layout_complexity(_doc(*pages))

    assert len(decision.top_contributing_pages) <= TOP_CONTRIBUTING_PAGE_COUNT
    indices = [p.page_index for p in decision.top_contributing_pages]
    # Page 3 has the highest contribution, then page 2, then page 1 or 4.
    assert indices[0] == 3
    assert indices[1] == 2
    # The no-signal page 0 must never appear.
    assert 0 not in indices


def test_top_contributing_pages_tiebreaks_on_page_index_ascending() -> None:
    """Two pages with identical contributions must sort by page_index
    ascending — a deterministic tiebreaker. Otherwise identical inputs
    could yield different orderings."""
    pages = [
        _neutral_page(page_index=5, column_count=2),
        _neutral_page(page_index=2, column_count=2),
        _neutral_page(page_index=7, column_count=2),
    ]
    decision = evaluate_layout_complexity(_doc(*pages))
    indices = [p.page_index for p in decision.top_contributing_pages]
    assert indices == [2, 5, 7]


def test_top_contributing_pages_omits_pages_with_no_signals() -> None:
    """A page without any triggered signal contributes zero and must
    NOT be surfaced as a 'top contributor'."""
    pages = [
        _neutral_page(page_index=i)  # 5 no-signal pages
        for i in range(5)
    ]
    pages.append(_neutral_page(page_index=99, column_count=2))
    decision = evaluate_layout_complexity(_doc(*pages))
    assert [p.page_index for p in decision.top_contributing_pages] == [99]


# ── Structured record shape ─────────────────────────────────────────


def test_decision_carries_policy_and_extractor_versions() -> None:
    decision = evaluate_layout_complexity(_doc(_neutral_page()))
    assert decision.policy_version == LAYOUT_COMPLEXITY_POLICY_VERSION
    assert decision.extractor_version == LAYOUT_FEATURE_EXTRACTOR_VERSION


def test_decision_returned_type_is_layout_complexity_decision() -> None:
    decision = evaluate_layout_complexity(_doc(_neutral_page()))
    assert isinstance(decision, LayoutComplexityDecision)
    for contrib in decision.top_contributing_pages:
        assert isinstance(contrib, LayoutPageContribution)


def test_reason_includes_band_and_score() -> None:
    decision = evaluate_layout_complexity(_doc(_neutral_page(column_count=2)))
    assert decision.band in decision.reason
    assert f"{decision.score:.1f}" in decision.reason
