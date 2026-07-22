"""PR 100 — Auto Policy v1 selector unit tests.

These tests exercise the pure ``select_ocr_backend`` decision function
in isolation. No PDFs, no compiler, no CLI. Backend availability is
constructed as a plain :class:`BackendAvailability` dataclass — this
mirrors what the real backend probes return without importing the
heavy backend modules.

Every test asserts against structured fields on :class:`AutoOcrDecision`
so that a regression in the rule shape (or a silent addition of a new
field with a default) fails loudly.
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from aksharamd.plugins.ocr_backends._protocol import (
    BackendAvailability,
    BackendAvailabilityDetails,
)
from aksharamd.plugins.ocr_backends.auto_selector import (
    AUTO_POLICY_VERSION,
    AutoOcrDecision,
    select_ocr_backend,
)

# ── helpers ────────────────────────────────────────────────────────────


def _runnable_uoc() -> BackendAvailability:
    """UOC availability probe result: everything green."""
    return BackendAvailability(
        is_available=True,
        reason="",
        hardware_compatible=True,
        model_installed=True,
        runnable_now=True,
        details=BackendAvailabilityDetails(
            model_snapshot_present=True,
            model_snapshot_verified=True,
        ),
        recommended_command=None,
    )


def _uoc_hardware_incompatible() -> BackendAvailability:
    return BackendAvailability(
        is_available=False,
        reason="No CUDA-capable GPU detected.",
        hardware_compatible=False,
        model_installed=True,
        runnable_now=False,
        details=BackendAvailabilityDetails(
            device_name=None,
            model_snapshot_present=True,
            model_snapshot_verified=True,
        ),
        recommended_command=None,
    )


def _uoc_model_not_installed() -> BackendAvailability:
    return BackendAvailability(
        is_available=False,
        reason="Model snapshot is not installed.",
        hardware_compatible=True,
        model_installed=False,
        runnable_now=False,
        details=BackendAvailabilityDetails(
            model_snapshot_present=False,
            model_snapshot_verified=False,
        ),
        recommended_command="aksharamd models install unlimited_ocr",
    )


def _uoc_receipt_stale() -> BackendAvailability:
    return BackendAvailability(
        is_available=False,
        reason="Model snapshot is not verified.",
        hardware_compatible=True,
        model_installed=True,
        runnable_now=False,
        details=BackendAvailabilityDetails(
            model_snapshot_present=True,
            model_snapshot_verified=False,
        ),
        recommended_command="aksharamd models verify unlimited_ocr",
    )


# ── 1. UOC preferred + runnable → picks UOC ────────────────────────────


def test_meets_both_thresholds_and_runnable_selects_uoc():
    decision = select_ocr_backend(
        total_pages=20,
        ocr_required_pages=9,   # 45% > 30%, and 9 >= 3
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.selected_backend == "unlimited_ocr"
    assert decision.preferred_backend == "unlimited_ocr"
    assert decision.fallback_occurred is False
    assert decision.fallback_reason is None
    assert decision.recommended_command is None
    assert decision.preferred_backend_runnable is True
    assert decision.policy_version == AUTO_POLICY_VERSION


# ── 2. UOC preferred but hardware incompatible → falls back to Tesseract ──


def test_uoc_preferred_hardware_incompatible_falls_back():
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=5,
        unlimited_ocr_availability=_uoc_hardware_incompatible(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "unlimited_ocr"
    assert decision.fallback_occurred is True
    assert decision.fallback_reason == "hardware_incompatible"
    # Hardware issues are not solved by a CLI command.
    assert decision.recommended_command is None
    assert decision.preferred_backend_runnable is False


# ── 2b. UOC preferred but model not installed → falls back ─────────────


def test_uoc_preferred_model_not_installed_falls_back():
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=5,
        unlimited_ocr_availability=_uoc_model_not_installed(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "unlimited_ocr"
    assert decision.fallback_occurred is True
    assert decision.fallback_reason == "model_not_installed"
    assert decision.recommended_command == "aksharamd models install unlimited_ocr"


# ── 2c. UOC preferred but receipt stale → falls back ───────────────────


def test_uoc_preferred_receipt_stale_falls_back():
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=5,
        unlimited_ocr_availability=_uoc_receipt_stale(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "unlimited_ocr"
    assert decision.fallback_occurred is True
    assert decision.fallback_reason == "model_not_verified"
    assert decision.recommended_command == "aksharamd models verify unlimited_ocr"


# ── 3. Below min-page floor → picks Tesseract, no fallback ─────────────


def test_below_min_page_floor_selects_tesseract_no_fallback():
    # 2 OCR-required pages < 3-page floor. Even at 50% (which passes
    # the fraction threshold), the absolute floor takes precedence.
    decision = select_ocr_backend(
        total_pages=4,
        ocr_required_pages=2,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "tesseract"
    assert decision.fallback_occurred is False
    assert decision.fallback_reason is None
    assert decision.recommended_command is None


# ── 4. Below fraction threshold → picks Tesseract, no fallback ────────


def test_below_fraction_threshold_selects_tesseract_no_fallback():
    # 10% of 20 pages = 2 OCR-required (also below floor, but we care
    # about the fraction gate here).
    decision = select_ocr_backend(
        total_pages=20,
        ocr_required_pages=2,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "tesseract"
    assert decision.fallback_occurred is False
    # 4 OCR-required pages, 20 total = 20% < 30% → still Tesseract.
    decision2 = select_ocr_backend(
        total_pages=20,
        ocr_required_pages=4,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision2.selected_backend == "tesseract"
    assert decision2.preferred_backend == "tesseract"
    assert decision2.fallback_occurred is False


# ── 5. Exactly at the min-page and fraction bounds → picks UOC ─────────


def test_exact_boundaries_select_uoc():
    # 3 OCR-required pages out of 10 = exactly 30% and exactly 3.
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=3,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.selected_backend == "unlimited_ocr"
    assert decision.preferred_backend == "unlimited_ocr"
    assert decision.fallback_occurred is False
    assert decision.ocr_required_fraction == pytest.approx(0.30)


# ── 6. Zero OCR pages → picks Tesseract, no fallback, no preference ──


def test_zero_ocr_required_selects_tesseract_no_fallback():
    decision = select_ocr_backend(
        total_pages=15,
        ocr_required_pages=0,
        # UOC being completely broken must NOT matter here.
        unlimited_ocr_availability=_uoc_hardware_incompatible(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "tesseract"
    assert decision.fallback_occurred is False
    assert decision.fallback_reason is None
    assert decision.recommended_command is None
    assert decision.ocr_required_fraction == 0.0


# ── 7. Zero total pages → picks Tesseract, no crash ───────────────────


def test_zero_total_pages_is_defensive_tesseract_choice():
    decision = select_ocr_backend(
        total_pages=0,
        ocr_required_pages=0,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.selected_backend == "tesseract"
    assert decision.preferred_backend == "tesseract"
    assert decision.fallback_occurred is False
    assert decision.total_pages == 0
    assert decision.ocr_required_fraction == 0.0


# ── 8. Determinism — same inputs produce identical decisions ──────────


def test_determinism_identical_inputs_produce_identical_decisions():
    inputs = dict(
        total_pages=20,
        ocr_required_pages=9,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    d1 = select_ocr_backend(**inputs)
    d2 = select_ocr_backend(**inputs)
    assert d1 == d2
    assert asdict(d1) == asdict(d2)
    # Frozen dataclass is hashable, which we exercise here.
    assert hash(d1) == hash(d2)


def test_determinism_fallback_case():
    inputs = dict(
        total_pages=10,
        ocr_required_pages=5,
        unlimited_ocr_availability=_uoc_model_not_installed(),
    )
    d1 = select_ocr_backend(**inputs)
    d2 = select_ocr_backend(**inputs)
    assert d1 == d2
    assert asdict(d1) == asdict(d2)


# ── 9. Frozen dataclass — cannot mutate after the fact ────────────────


def test_decision_is_frozen():
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=5,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        decision.selected_backend = "tesseract"  # type: ignore[misc]


# ── 10. requested_backend is always "auto" ────────────────────────────


def test_requested_backend_is_always_auto():
    for total, ocr in [(20, 9), (2, 0), (0, 0), (5, 3), (100, 30)]:
        decision = select_ocr_backend(
            total_pages=total,
            ocr_required_pages=ocr,
            unlimited_ocr_availability=_runnable_uoc(),
        )
        assert decision.requested_backend == "auto"


# ── 11. Fraction is recorded correctly on borderline cases ────────────


def test_ocr_required_fraction_recorded_when_uoc_preferred():
    decision = select_ocr_backend(
        total_pages=20,
        ocr_required_pages=9,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.ocr_required_fraction == pytest.approx(9 / 20)


def test_ocr_required_fraction_recorded_when_uoc_not_preferred():
    decision = select_ocr_backend(
        total_pages=20,
        ocr_required_pages=4,   # 20% < 30%
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.ocr_required_fraction == pytest.approx(4 / 20)
    assert decision.selected_backend == "tesseract"


# ── 12. Threshold constants are surfaced on the decision ──────────────


def test_thresholds_are_surfaced_on_the_decision():
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=3,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    assert decision.minimum_pages_threshold == 3
    assert decision.fraction_threshold == pytest.approx(0.30)


# ── 13. UOC preferred but unusual availability shape → catch-all ──────


def test_uoc_preferred_defensive_catch_all_reason():
    # An availability probe that is not-runnable but where none of the
    # three specific reason predicates match. The selector must not
    # crash — it labels the fallback ``"not_runnable"``.
    av = BackendAvailability(
        is_available=False,
        reason="Unspecified probe failure.",
        hardware_compatible=True,
        model_installed=True,
        runnable_now=False,
        # No details block — nothing to differentiate against.
        details=None,
        recommended_command=None,
    )
    decision = select_ocr_backend(
        total_pages=10, ocr_required_pages=5,
        unlimited_ocr_availability=av,
    )
    assert decision.fallback_occurred is True
    assert decision.fallback_reason == "not_runnable"
    assert decision.recommended_command is None


# ── 14. AutoOcrDecision is a dataclass with the expected field set ────


def test_decision_fields_are_stable():
    decision = select_ocr_backend(
        total_pages=10,
        ocr_required_pages=5,
        unlimited_ocr_availability=_runnable_uoc(),
    )
    expected_keys = {
        "requested_backend",
        "selected_backend",
        "preferred_backend",
        "policy_version",
        "total_pages",
        "ocr_required_pages",
        "ocr_required_fraction",
        "minimum_pages_threshold",
        "fraction_threshold",
        "preferred_backend_runnable",
        "fallback_occurred",
        "fallback_reason",
        "recommended_command",
    }
    assert set(asdict(decision).keys()) == expected_keys
    assert isinstance(decision, AutoOcrDecision)
