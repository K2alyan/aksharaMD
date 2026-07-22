"""OCR backend Auto Policy v1 — decision engine.

PR 100 introduces ``--ocr-backend auto`` as a third value. When the
caller passes ``auto``, pdf.py runs page classification first and then
consults :func:`select_ocr_backend` here to pick either
``unlimited_ocr`` or ``tesseract`` for the whole document.

The policy is deliberately simple and heuristic:

    choose unlimited_ocr when ALL of:
        ocr_required_pages >= _MIN_UOC_PAGES              (3)
        ocr_required_pages / total_pages >= _UOC_FRACTION (0.30)
        unlimited_ocr.runnable_now is True
    otherwise choose tesseract

If UOC is preferred but not runnable, the selector falls back to
Tesseract and records a ``fallback_reason`` derived deterministically
from :class:`BackendAvailability`. Explicit ``--ocr-backend`` values
(``tesseract``, ``unlimited_ocr``) never enter this module — they
dispatch as before.

This module is pure and dependency-light:

* No Click, no Rich, no PDF mutation.
* No network, no filesystem probes.
* No heavy imports.

Determinism is a contract: identical inputs must produce identical
``AutoOcrDecision`` instances. See tests for the equality assertions.

The 3-page floor and 30% threshold are heuristic and have NOT been
calibrated against a labeled benchmark. Any semantic change to the
rule (thresholds, floors, fallback semantics) requires bumping
``AUTO_POLICY_VERSION`` and updating
``docs/adr/ocr-auto-policy-v1.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aksharamd.plugins.ocr_backends._protocol import BackendAvailability

# ---------------------------------------------------------------------------
# Policy version. Bump whenever any of the rule constants below, the fallback
# semantics, or the warning content changes. The version appears in the
# compile manifest so downstream tooling can pin against a specific policy.
# ---------------------------------------------------------------------------
AUTO_POLICY_VERSION = "1"

# Minimum absolute number of OCR-required pages before UOC is preferred.
# Below this floor, a scan of a 1-2 page ID card would pay UOC startup cost
# for very little benefit.
_MIN_UOC_PAGES = 3

# Minimum fraction of pages needing OCR before UOC is preferred. Below this
# fraction, a mostly-digital 200-page report with a few scanned inserts
# should stay on Tesseract to avoid loading a large model for a handful of
# pages.
_UOC_FRACTION_THRESHOLD = 0.30


@dataclass(frozen=True)
class AutoOcrDecision:
    """Structured record of one Auto Policy v1 decision.

    All fields are JSON-friendly primitives. The dataclass is ``frozen``
    so an accidental mutation after the decision was captured on the
    compile context becomes a runtime error rather than a silent
    corruption of the manifest.

    Field notes:

    * ``requested_backend`` is always ``"auto"`` here — this dataclass
      only exists when the user asked for auto.
    * ``preferred_backend`` reflects what the policy chose based on the
      classification alone (before considering runnability). When both
      thresholds are met the preferred backend is ``unlimited_ocr``;
      otherwise ``tesseract``.
    * ``selected_backend`` is the FINAL choice actually used to run OCR.
      It differs from ``preferred_backend`` only when UOC was preferred
      but not runnable.
    * ``fallback_occurred`` is True iff
      ``selected_backend != preferred_backend``.
    * ``fallback_reason`` is one of the strings documented in
      :func:`select_ocr_backend` when a fallback happened; ``None``
      otherwise.
    * ``recommended_command`` mirrors
      :attr:`BackendAvailability.recommended_command` when a fallback
      happened AND the availability probe supplied one; ``None`` if
      the remediation is not a single command (e.g. hardware
      incompatibility) or no fallback occurred.
    """

    requested_backend: Literal["auto"]
    selected_backend: Literal["tesseract", "unlimited_ocr"]
    preferred_backend: Literal["tesseract", "unlimited_ocr"]
    policy_version: str
    total_pages: int
    ocr_required_pages: int
    ocr_required_fraction: float
    minimum_pages_threshold: int
    fraction_threshold: float
    preferred_backend_runnable: bool
    fallback_occurred: bool
    fallback_reason: str | None
    recommended_command: str | None


def _classify_fallback_reason(av: BackendAvailability) -> str:
    """Derive a stable fallback-reason label from a backend availability.

    Only invoked when the caller has determined the preferred backend is
    not runnable. Returns one of:

    * ``"hardware_incompatible"`` — the physical device does not meet
      requirements; the user cannot fix this with a CLI command.
    * ``"model_not_installed"`` — the snapshot is missing entirely; the
      user should run ``aksharamd models install unlimited_ocr``.
    * ``"model_not_verified"`` — the snapshot is present but its
      verification receipt is missing or stale; the user should run
      ``aksharamd models verify unlimited_ocr``.
    * ``"not_runnable"`` — defensive catch-all for future signals that
      don't fit the three categories above.

    The rules read the structured predicates on
    :class:`BackendAvailability` and its optional
    :class:`BackendAvailabilityDetails`; they do not parse
    ``reason`` strings, which are display-only.
    """
    if not av.hardware_compatible:
        return "hardware_incompatible"

    # Hardware OK — differentiate "no snapshot at all" from
    # "snapshot present but not verified" using the structured details.
    details = av.details
    if details is not None:
        if details.model_snapshot_present is False:
            return "model_not_installed"
        if (
            details.model_snapshot_present is True
            and details.model_snapshot_verified is False
        ):
            return "model_not_verified"

    # Fall back on the coarse ``model_installed`` predicate when the
    # backend didn't report structured snapshot details.
    if not av.model_installed:
        return "model_not_installed"

    return "not_runnable"


def select_ocr_backend(
    *,
    total_pages: int,
    ocr_required_pages: int,
    unlimited_ocr_availability: BackendAvailability,
) -> AutoOcrDecision:
    """Apply Auto Policy v1 and return a structured :class:`AutoOcrDecision`.

    Called ONLY when the user passes ``--ocr-backend auto``. Explicit
    backend choices never enter this module.

    The rule:

    * If no page classified as OCR-required — return with
      ``preferred_backend="tesseract"``. There is nothing for UOC to
      do, so no fallback is possible and no warning is emitted later.
    * Else if both thresholds are met AND UOC is runnable — choose UOC.
    * Else if both thresholds are met but UOC is NOT runnable — choose
      Tesseract; record the fallback with a categorical reason and the
      availability probe's recommended remediation command (if any).
    * Otherwise — choose Tesseract; no fallback (Tesseract was the
      policy choice from the start).

    Guards:

    * ``total_pages == 0`` returns a defensive Tesseract choice with
      ``ocr_required_fraction=0.0`` — the caller has already produced
      no pages, so downstream OCR dispatch will be a no-op anyway.
    """
    # Defensive guard: zero-page document. No OCR needed, no probing,
    # no crashes on division by zero.
    if total_pages <= 0:
        return AutoOcrDecision(
            requested_backend="auto",
            selected_backend="tesseract",
            preferred_backend="tesseract",
            policy_version=AUTO_POLICY_VERSION,
            total_pages=max(0, total_pages),
            ocr_required_pages=max(0, ocr_required_pages),
            ocr_required_fraction=0.0,
            minimum_pages_threshold=_MIN_UOC_PAGES,
            fraction_threshold=_UOC_FRACTION_THRESHOLD,
            preferred_backend_runnable=True,
            fallback_occurred=False,
            fallback_reason=None,
            recommended_command=None,
        )

    fraction = ocr_required_pages / total_pages

    # Zero-OCR-required path: there is nothing for UOC to do. Always
    # choose Tesseract, never emit a fallback (UOC was never preferred).
    if ocr_required_pages == 0:
        return AutoOcrDecision(
            requested_backend="auto",
            selected_backend="tesseract",
            preferred_backend="tesseract",
            policy_version=AUTO_POLICY_VERSION,
            total_pages=total_pages,
            ocr_required_pages=0,
            ocr_required_fraction=0.0,
            minimum_pages_threshold=_MIN_UOC_PAGES,
            fraction_threshold=_UOC_FRACTION_THRESHOLD,
            preferred_backend_runnable=True,
            fallback_occurred=False,
            fallback_reason=None,
            recommended_command=None,
        )

    meets_page_floor = ocr_required_pages >= _MIN_UOC_PAGES
    meets_fraction = fraction >= _UOC_FRACTION_THRESHOLD
    uoc_preferred = meets_page_floor and meets_fraction

    if not uoc_preferred:
        # Tesseract is the policy choice — no fallback, no warning
        # regarding fallback. The document simply doesn't benefit
        # enough from UOC to justify its startup cost.
        return AutoOcrDecision(
            requested_backend="auto",
            selected_backend="tesseract",
            preferred_backend="tesseract",
            policy_version=AUTO_POLICY_VERSION,
            total_pages=total_pages,
            ocr_required_pages=ocr_required_pages,
            ocr_required_fraction=fraction,
            minimum_pages_threshold=_MIN_UOC_PAGES,
            fraction_threshold=_UOC_FRACTION_THRESHOLD,
            preferred_backend_runnable=unlimited_ocr_availability.runnable_now,
            fallback_occurred=False,
            fallback_reason=None,
            recommended_command=None,
        )

    # UOC is preferred. Decide selection based on runnability.
    if unlimited_ocr_availability.runnable_now:
        return AutoOcrDecision(
            requested_backend="auto",
            selected_backend="unlimited_ocr",
            preferred_backend="unlimited_ocr",
            policy_version=AUTO_POLICY_VERSION,
            total_pages=total_pages,
            ocr_required_pages=ocr_required_pages,
            ocr_required_fraction=fraction,
            minimum_pages_threshold=_MIN_UOC_PAGES,
            fraction_threshold=_UOC_FRACTION_THRESHOLD,
            preferred_backend_runnable=True,
            fallback_occurred=False,
            fallback_reason=None,
            recommended_command=None,
        )

    # UOC preferred but not runnable → loud fallback to Tesseract.
    reason = _classify_fallback_reason(unlimited_ocr_availability)
    return AutoOcrDecision(
        requested_backend="auto",
        selected_backend="tesseract",
        preferred_backend="unlimited_ocr",
        policy_version=AUTO_POLICY_VERSION,
        total_pages=total_pages,
        ocr_required_pages=ocr_required_pages,
        ocr_required_fraction=fraction,
        minimum_pages_threshold=_MIN_UOC_PAGES,
        fraction_threshold=_UOC_FRACTION_THRESHOLD,
        preferred_backend_runnable=False,
        fallback_occurred=True,
        fallback_reason=reason,
        recommended_command=unlimited_ocr_availability.recommended_command,
    )


__all__ = [
    "AUTO_POLICY_VERSION",
    "AutoOcrDecision",
    "select_ocr_backend",
]
