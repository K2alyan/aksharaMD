"""Tests for the two-axis ReadinessResult introduced in P0.4.

Every SCORING_POLICY rule carries a `category` (structural | content | meta).
ReadinessResult exposes computed `structural_score` and `substance_score`
properties that partition deductions along that axis. The single `score`
attribute is unchanged.
"""
from __future__ import annotations

from aksharamd.scoring import SCORING_POLICY_VERSION
from aksharamd.scoring.models import (
    SCORING_POLICY,
    DeductionRecord,
    ReadinessResult,
    ScoringRule,
)

_VALID_CATEGORIES = {"structural", "content", "meta"}


def test_scoring_policy_version_is_1_4():
    assert SCORING_POLICY_VERSION == "1.4"


def test_every_scoring_rule_has_a_valid_category():
    for rule_id, rule in SCORING_POLICY.items():
        assert rule.category in _VALID_CATEGORIES, (
            f"rule {rule_id} has invalid category {rule.category!r}"
        )


def test_scoring_rule_has_category_field_with_structural_default():
    rule = ScoringRule(
        rule_id="TEST_RULE",
        description="",
        max_penalty=0,
        formula="",
    )
    assert rule.category == "structural"


def test_structural_score_deducts_only_structural_penalties():
    result = ReadinessResult(
        score=100,
        deductions=[
            DeductionRecord(rule_id="W_MULTICOLUMN_ORDER", description="", penalty=20),
            DeductionRecord(rule_id="GLYPH_ARTIFACTS", description="", penalty=15),
            DeductionRecord(rule_id="AUTO_OCR_BACKEND_SELECTED", description="", penalty=0),
        ],
    )
    assert result.structural_score == 80
    assert result.substance_score == 85


def test_substance_score_deducts_only_content_penalties():
    result = ReadinessResult(
        score=100,
        deductions=[
            DeductionRecord(rule_id="GLYPH_ARTIFACTS", description="", penalty=25),
            DeductionRecord(rule_id="W_ENCODING_ARTIFACTS", description="", penalty=10),
            DeductionRecord(rule_id="HEADING_ISSUES", description="", penalty=6),
        ],
    )
    assert result.substance_score == 65
    assert result.structural_score == 94


def test_suppressed_deductions_do_not_affect_axis_scores():
    result = ReadinessResult(
        score=100,
        deductions=[
            DeductionRecord(
                rule_id="W_MULTICOLUMN_ORDER",
                description="",
                penalty=31,
                suppressed=True,
                suppression_reason="suppressed for test",
            ),
            DeductionRecord(
                rule_id="GLYPH_ARTIFACTS",
                description="",
                penalty=25,
                suppressed=True,
                suppression_reason="suppressed for test",
            ),
        ],
    )
    assert result.structural_score == 100
    assert result.substance_score == 100


def test_axis_scores_clamp_at_zero():
    result = ReadinessResult(
        score=0,
        deductions=[
            DeductionRecord(rule_id="W_MULTICOLUMN_ORDER", description="", penalty=200),
            DeductionRecord(rule_id="GLYPH_ARTIFACTS", description="", penalty=200),
        ],
    )
    assert result.structural_score == 0
    assert result.substance_score == 0


def test_unknown_rule_id_does_not_contribute_to_either_axis():
    result = ReadinessResult(
        score=100,
        deductions=[
            DeductionRecord(rule_id="RULE_NOT_IN_POLICY", description="", penalty=50),
        ],
    )
    assert result.structural_score == 100
    assert result.substance_score == 100


def test_empty_deductions_produce_perfect_axis_scores():
    result = ReadinessResult(score=100)
    assert result.structural_score == 100
    assert result.substance_score == 100


def test_meta_category_deductions_do_not_reduce_axis_scores():
    result = ReadinessResult(
        score=100,
        deductions=[
            DeductionRecord(
                rule_id="AUTO_OCR_BACKEND_SELECTED", description="", penalty=0
            ),
            DeductionRecord(
                rule_id="W_PDF_ATTACHMENT_IGNORED", description="", penalty=0
            ),
        ],
    )
    assert result.structural_score == 100
    assert result.substance_score == 100


def test_policy_category_distribution_matches_audit():
    """Sanity check: category counts should match the 2026-09-13 audit.

    5 structural, 16 content, 5 meta = 26 total. If this fails, someone
    added or reclassified a rule without updating the audit record.
    """
    counts = {"structural": 0, "content": 0, "meta": 0}
    for rule in SCORING_POLICY.values():
        counts[rule.category] += 1
    assert counts == {"structural": 5, "content": 16, "meta": 5}, (
        f"category distribution drifted from 2026-09-13 audit: {counts}"
    )
