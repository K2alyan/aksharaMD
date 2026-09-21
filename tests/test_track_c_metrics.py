"""Durable tests for Track C metric functions.

Covers _compute_numeric_em, _compute_token_f1, _normalize_boolean,
_to_float_numeric, and _compute_primary_score.

These tests lock in the corrected behaviour after the numeric-EM bug fix
(string-EM fallback was stripping '-' and '.' as punctuation, causing
-5==5 and 1.2==12). Adding new cases here requires updating the comment
explaining the invariant being tested.
"""
from __future__ import annotations

import pytest

from benchmarks.eval_v1.stage1.run_track_c import (
    _compute_numeric_em,
    _compute_primary_score,
    _normalize_boolean,
    _to_float_numeric,
)

# ---------------------------------------------------------------------------
# _compute_numeric_em
# ---------------------------------------------------------------------------


class TestNumericEM:
    # --- sign correctness (previously failed: minus stripped as punctuation) ---

    def test_negative_vs_positive_is_wrong(self):
        assert _compute_numeric_em("-5", "5", "") == 0.0

    def test_positive_vs_negative_is_wrong(self):
        assert _compute_numeric_em("5", "-5", "") == 0.0

    def test_negative_matches_negative(self):
        assert _compute_numeric_em("-5", "-5", "") == 1.0

    # --- decimal correctness (previously failed: '.' stripped as punctuation) ---

    def test_decimal_vs_integer_is_wrong(self):
        assert _compute_numeric_em("1.2", "12", "") == 0.0

    def test_integer_vs_decimal_is_wrong(self):
        assert _compute_numeric_em("12", "1.2", "") == 0.0

    def test_same_decimal_matches(self):
        assert _compute_numeric_em("1.2", "1.2", "") == 1.0

    # --- exact match ---

    def test_integer_match(self):
        assert _compute_numeric_em("12", "12", "") == 1.0

    def test_zero_match(self):
        assert _compute_numeric_em("0", "0", "") == 1.0

    # --- tolerance (1e-4 relative) ---

    def test_within_tolerance(self):
        assert _compute_numeric_em("1000.0001", "1000", "") == 1.0

    def test_outside_tolerance(self):
        assert _compute_numeric_em("1000.5", "1000", "") == 0.0

    # --- external scale field (TAT-DQA) ---

    def test_external_scale_million(self):
        # Both sides multiplied by 1e6; 1.5 × 1e6 == 1.5 × 1e6
        assert _compute_numeric_em("1.5", "1.5", "million") == 1.0

    def test_external_scale_mismatch(self):
        # 1.5M != 2.0M
        assert _compute_numeric_em("1.5", "2.0", "million") == 0.0

    def test_external_scale_thousand(self):
        assert _compute_numeric_em("500", "500", "thousand") == 1.0

    def test_external_scale_percent_no_mult(self):
        # 'percent' is not in _SCALE_MULT; both sides × 1.0; 15.3 == 15.3
        assert _compute_numeric_em("15.3", "15.3", "percent") == 1.0

    def test_percent_with_symbol_stripped(self):
        # Prediction "15.3%" — % stripped before parsing
        assert _compute_numeric_em("15.3%", "15.3", "percent") == 1.0

    # --- embedded unit suffix (in _to_float_numeric) ---

    def test_embedded_M_suffix(self):
        # "1.5M" parsed as 1.5e6; gold "1.5" with scale=million also 1.5e6
        assert _compute_numeric_em("1.5M", "1.5", "million") == 1.0

    def test_embedded_B_suffix(self):
        assert _compute_numeric_em("2B", "2.0", "billion") == 1.0

    def test_embedded_K_suffix(self):
        assert _compute_numeric_em("500K", "500", "thousand") == 1.0

    # --- multi-span gold (pipe-separated) ---

    def test_multi_span_first_matches(self):
        assert _compute_numeric_em("5", "5 | 10", "") == 1.0

    def test_multi_span_second_matches(self):
        assert _compute_numeric_em("10", "5 | 10", "") == 1.0

    def test_multi_span_none_matches(self):
        assert _compute_numeric_em("3", "5 | 10", "") == 0.0

    # --- non-numeric (string EM path) ---

    def test_nonnumeric_match(self):
        assert _compute_numeric_em("march", "march", "") == 1.0

    def test_nonnumeric_mismatch(self):
        assert _compute_numeric_em("march", "april", "") == 0.0

    # --- mixed numeric / non-numeric: no match (not string-EM fallback) ---

    def test_numeric_pred_nonnumeric_gold(self):
        assert _compute_numeric_em("5", "march", "") == 0.0

    def test_nonnumeric_pred_numeric_gold(self):
        assert _compute_numeric_em("march", "5", "") == 0.0

    # --- currency symbols stripped ---

    def test_dollar_stripped(self):
        assert _compute_numeric_em("$1.5", "1.5", "") == 1.0

    def test_currency_and_comma(self):
        assert _compute_numeric_em("$1,500", "1500", "") == 1.0


# ---------------------------------------------------------------------------
# _to_float_numeric
# ---------------------------------------------------------------------------


class TestToFloatNumeric:
    def test_plain_integer(self):
        assert _to_float_numeric("12", None) == 12.0

    def test_plain_negative(self):
        assert _to_float_numeric("-5", None) == -5.0

    def test_comma_stripped(self):
        assert _to_float_numeric("1,234", None) == 1234.0

    def test_embedded_M(self):
        assert _to_float_numeric("1.2M", None) == pytest.approx(1.2e6)

    def test_embedded_B(self):
        assert _to_float_numeric("3.5B", None) == pytest.approx(3.5e9)

    def test_embedded_K(self):
        assert _to_float_numeric("500K", None) == pytest.approx(5e5)

    def test_embedded_billion_word(self):
        assert _to_float_numeric("2.1 billion", None) == pytest.approx(2.1e9)

    def test_embedded_million_word(self):
        assert _to_float_numeric("1.5 million", None) == pytest.approx(1.5e6)

    def test_external_scale_million(self):
        assert _to_float_numeric("1.5", "million") == pytest.approx(1.5e6)

    def test_external_scale_none_string(self):
        assert _to_float_numeric("1.5", None) == 1.5

    def test_nonnumeric_returns_none(self):
        assert _to_float_numeric("abc", None) is None

    def test_empty_string_returns_none(self):
        assert _to_float_numeric("", None) is None

    def test_percent_symbol_stripped_no_mult(self):
        # % stripped; "percent" scale not in _SCALE_MULT → ×1.0
        assert _to_float_numeric("15.3%", "percent") == pytest.approx(15.3)


# ---------------------------------------------------------------------------
# _normalize_boolean
# ---------------------------------------------------------------------------


class TestNormalizeBoolean:
    def test_bare_yes(self):
        assert _normalize_boolean("yes") == "yes"

    def test_bare_no(self):
        assert _normalize_boolean("no") == "no"

    def test_yes_with_comma(self):
        assert _normalize_boolean("Yes, because the paper shows...") == "yes"

    def test_no_with_period(self):
        assert _normalize_boolean("No. The approach does not...") == "no"

    def test_nobody_not_no(self):
        # "nobody" starts with "no" but not as a whole word
        assert _normalize_boolean("Nobody knows") is None

    def test_unanswerable_not_boolean(self):
        assert _normalize_boolean("unanswerable") is None

    def test_empty_string(self):
        assert _normalize_boolean("") is None

    def test_yes_uppercase(self):
        assert _normalize_boolean("YES") == "yes"

    def test_no_uppercase(self):
        assert _normalize_boolean("NO") == "no"


# ---------------------------------------------------------------------------
# _compute_primary_score — routing
# ---------------------------------------------------------------------------


class TestPrimaryScore:
    def test_unanswerable_correct(self):
        assert _compute_primary_score("unanswerable", "unanswerable", "unanswerable", "qasper") == 1.0

    def test_unanswerable_wrong(self):
        assert _compute_primary_score("yes", "unanswerable", "unanswerable", "qasper") == 0.0

    def test_qasper_boolean_yes_match(self):
        assert _compute_primary_score("Yes, the paper claims...", "yes", "boolean", "qasper") == 1.0

    def test_qasper_boolean_no_mismatch(self):
        assert _compute_primary_score("No, it does not.", "yes", "boolean", "qasper") == 0.0

    def test_qasper_extractive_token_f1(self):
        # Partial token overlap → between 0 and 1
        score = _compute_primary_score(
            "the transformer model", "the transformer architecture", "extractive", "qasper"
        )
        assert 0.0 < score < 1.0

    def test_qasper_abstractive_token_f1(self):
        score = _compute_primary_score("neural network", "neural network", "abstractive", "qasper")
        assert score == 1.0

    def test_tatdqa_arithmetic_numeric_em(self):
        # arithmetic → numeric EM
        assert _compute_primary_score("1.5", "1.5", "arithmetic", "tat_dqa", scale="million") == 1.0

    def test_tatdqa_arithmetic_wrong_sign(self):
        assert _compute_primary_score("-1.5", "1.5", "arithmetic", "tat_dqa", scale="") == 0.0

    def test_tatdqa_span_token_f1(self):
        score = _compute_primary_score("revenue growth", "revenue growth", "span", "tat_dqa")
        assert score == 1.0

    def test_tatdqa_count_numeric_em(self):
        assert _compute_primary_score("3", "3", "count", "tat_dqa") == 1.0
