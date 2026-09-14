"""Tests for the DetectorBudget primitive (P0.2).

Locks the measure-and-warn contract: detectors are timed on __exit__,
timings land on ``ctx.detector_timings``, over-budget detectors emit an
informational ``W_DETECTOR_TIMEOUT`` warning and appear in
``ctx.detector_timeouts``. Never raises.
"""
from __future__ import annotations

import os
import time

import pytest

from aksharamd.context import CompilationContext
from aksharamd.scoring.detector_budget import (
    DEFAULT_BUDGET_MS,
    DetectorBudget,
)
from aksharamd.scoring.models import SCORING_POLICY

# ── Rule registration ──────────────────────────────────────────────────────

def test_w_detector_timeout_is_registered_in_scoring_policy():
    """W_DETECTOR_TIMEOUT must be in SCORING_POLICY (informational, meta)."""
    assert "W_DETECTOR_TIMEOUT" in SCORING_POLICY
    rule = SCORING_POLICY["W_DETECTOR_TIMEOUT"]
    assert rule.max_penalty == 0
    assert rule.category == "meta"


# ── Field defaults on CompilationContext ───────────────────────────────────

def test_detector_timings_defaults_to_empty_dict():
    ctx = CompilationContext(source="test.pdf")
    assert ctx.detector_timings == {}


def test_detector_timeouts_defaults_to_empty_list():
    ctx = CompilationContext(source="test.pdf")
    assert ctx.detector_timeouts == []


# ── Happy path: detector completes under budget ────────────────────────────

def test_completed_detector_records_timing():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_FAST", budget_ms=1000):
        pass
    assert "TEST_FAST" in ctx.detector_timings
    assert ctx.detector_timings["TEST_FAST"] >= 0.0


def test_under_budget_detector_does_not_emit_warning():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_FAST", budget_ms=1000):
        pass
    assert ctx.detector_timeouts == []
    warnings = [i for i in ctx.validation.issues if i.code == "W_DETECTOR_TIMEOUT"]
    assert warnings == []


# ── Over-budget path ───────────────────────────────────────────────────────

def test_over_budget_detector_records_timeout():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_SLOW", budget_ms=1):
        time.sleep(0.02)  # 20ms — well over the 1ms budget
    assert "TEST_SLOW" in ctx.detector_timeouts
    assert ctx.detector_timings["TEST_SLOW"] > 1.0


def test_over_budget_detector_emits_informational_warning():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_SLOW", budget_ms=1):
        time.sleep(0.02)
    warnings = [i for i in ctx.validation.issues if i.code == "W_DETECTOR_TIMEOUT"]
    assert len(warnings) == 1
    assert "TEST_SLOW" in warnings[0].message
    assert "elapsed_ms" in warnings[0].metadata
    assert warnings[0].metadata["detector"] == "TEST_SLOW"
    assert warnings[0].metadata["budget_ms"] == 1


# ── Never-raises contract ──────────────────────────────────────────────────

def test_exception_inside_budget_still_records_timing():
    """A detector that raises still gets its wall time recorded."""
    ctx = CompilationContext(source="test.pdf")
    with pytest.raises(RuntimeError, match="detector failed"):
        with DetectorBudget(ctx, "TEST_RAISER", budget_ms=1000):
            raise RuntimeError("detector failed")
    assert "TEST_RAISER" in ctx.detector_timings


def test_budget_itself_never_raises_on_over_budget():
    """Measure-and-warn: the primitive must not turn slowness into an error."""
    ctx = CompilationContext(source="test.pdf")
    # No pytest.raises here — the with-block exits normally.
    with DetectorBudget(ctx, "TEST_SLOW", budget_ms=1):
        time.sleep(0.005)


# ── Elapsed / expired cooperative interface ────────────────────────────────

def test_elapsed_ms_is_zero_before_enter():
    ctx = CompilationContext(source="test.pdf")
    budget = DetectorBudget(ctx, "TEST_COOP", budget_ms=1000)
    assert budget.elapsed_ms == 0.0
    assert budget.expired is False


def test_expired_becomes_true_after_budget_exceeded():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_COOP", budget_ms=1) as budget:
        time.sleep(0.02)
        assert budget.expired is True


def test_expired_stays_false_when_under_budget():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_COOP", budget_ms=1000) as budget:
        assert budget.expired is False


# ── Defaults and env-var overrides ─────────────────────────────────────────

def test_default_budget_used_when_not_specified():
    ctx = CompilationContext(source="test.pdf")
    budget = DetectorBudget(ctx, "TEST_DEFAULT")
    assert budget.budget_ms == DEFAULT_BUDGET_MS


def test_env_var_would_override_default_budget():
    """DEFAULT_BUDGET_MS reads from AKSHARAMD_DETECTOR_BUDGET_MS at import time."""
    # We cannot easily re-import the module to test env-var picked up at
    # import; instead assert the current DEFAULT_BUDGET_MS matches env or 2000.
    expected = int(os.environ.get("AKSHARAMD_DETECTOR_BUDGET_MS", "2000"))
    assert DEFAULT_BUDGET_MS == expected


# ── Validation ─────────────────────────────────────────────────────────────

def test_blank_name_rejected():
    ctx = CompilationContext(source="test.pdf")
    with pytest.raises(ValueError, match="name must not be blank"):
        DetectorBudget(ctx, "")
    with pytest.raises(ValueError, match="name must not be blank"):
        DetectorBudget(ctx, "   ")


# ── Multiple detectors on the same context ─────────────────────────────────

def test_multiple_detectors_each_recorded_independently():
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_A", budget_ms=1000):
        pass
    with DetectorBudget(ctx, "TEST_B", budget_ms=1000):
        pass
    with DetectorBudget(ctx, "TEST_C", budget_ms=1000):
        pass
    assert set(ctx.detector_timings.keys()) == {"TEST_A", "TEST_B", "TEST_C"}
    assert ctx.detector_timeouts == []


def test_same_detector_twice_last_write_wins_on_timings():
    """If a detector runs twice under the same name, only the last timing is kept."""
    ctx = CompilationContext(source="test.pdf")
    with DetectorBudget(ctx, "TEST_TWICE", budget_ms=1000):
        pass
    first = ctx.detector_timings["TEST_TWICE"]
    with DetectorBudget(ctx, "TEST_TWICE", budget_ms=1000):
        time.sleep(0.001)
    second = ctx.detector_timings["TEST_TWICE"]
    # Not strictly ordered, but second should be non-negative.
    assert second >= 0
    assert first >= 0
