"""Layered preference labelling for the OCR Auto Policy v1 harness.

Three labels per document, computed in order:

* ``automatic_preference`` — deterministic rule from the two explicit runs
  (Tesseract vs. UOC). Never uses the ``auto`` run's own selection.
* ``human_preference`` — nullable; populated by a manual review pass that is
  out of scope for this PR but reserved in the schema.
* ``final_preference`` — human_preference if set, otherwise
  automatic_preference. Feeds the auto correctness confusion matrix in the
  report.

Thresholds are documented as heuristic:

* ``READINESS_DELTA`` (5): UOC must beat Tesseract by at least this many
  readiness points before we prefer it — one metric alone is insufficient.
* ``READINESS_REGRESSION_TOLERANCE`` (2): Tesseract wins if it is at most
  this many points below UOC AND UOC has any hard-fail signal.
* ``RUNTIME_MULTIPLIER_MAX`` (10x): even a small readiness win is not worth
  a 10x runtime blow-up.

Readiness alone does NOT determine preference: repetition, exit status, and
runtime gates all veto a UOC preference.
"""
from __future__ import annotations

from .schema import DocumentSummary, HumanPreferenceLabel, PreferenceLabel, RunResult

READINESS_DELTA = 5
READINESS_REGRESSION_TOLERANCE = 2
RUNTIME_MULTIPLIER_MAX = 10.0
NEAR_EMPTY_MARKDOWN_CHARS = 200
MATERIAL_DISAGREEMENT_READINESS_WINDOW = 3


def _runtime_multiplier(tesseract: RunResult, uoc: RunResult) -> float:
    """UOC runtime divided by Tesseract runtime; safe on zero baseline."""
    if tesseract.runtime_seconds <= 0:
        return float("inf") if uoc.runtime_seconds > 0 else 1.0
    return uoc.runtime_seconds / tesseract.runtime_seconds


def compute_automatic_preference(
    tesseract: RunResult, unlimited_ocr: RunResult
) -> PreferenceLabel:
    """Deterministic layered rule described in the module docstring."""
    tess_r = tesseract.readiness_score if tesseract.readiness_score is not None else -1
    uoc_r = (
        unlimited_ocr.readiness_score
        if unlimited_ocr.readiness_score is not None
        else -1
    )
    runtime_mult = _runtime_multiplier(tesseract, unlimited_ocr)

    uoc_hard_fail = (
        unlimited_ocr.repetition_flag
        or unlimited_ocr.exit_status != 0
        or runtime_mult > RUNTIME_MULTIPLIER_MAX
    )

    # Prefer UOC only when it clearly wins on readiness AND has no hard-fail
    # signal. This is a conservative rule: we do not risk hallucination or
    # 10x latency for a marginal quality gain.
    if uoc_r >= tess_r + READINESS_DELTA and not uoc_hard_fail:
        return "unlimited_ocr"

    # Prefer Tesseract when it is comparable to or better than UOC, OR when
    # UOC hard-fails (regardless of readiness).
    if uoc_hard_fail or tess_r >= uoc_r - READINESS_REGRESSION_TOLERANCE:
        return "tesseract"

    return "undetermined"


def compute_final_preference(
    automatic: PreferenceLabel, human: HumanPreferenceLabel | None
) -> PreferenceLabel:
    """Human review overrides the automatic label when populated."""
    if human is not None:
        return human  # type: ignore[return-value]
    return automatic


def compute_auto_match(
    auto_selected_backend: str | None, final: PreferenceLabel
) -> bool | None:
    """None when the final preference is undetermined; else strict equality."""
    if final == "undetermined":
        return None
    if auto_selected_backend is None:
        return False
    return auto_selected_backend == final


def build_document_summary(
    *,
    document_id: str,
    profile_class: str,
    tesseract: RunResult,
    unlimited_ocr: RunResult,
    auto: RunResult,
    human_preference: HumanPreferenceLabel | None = None,
    review_reasons: list[str] | None = None,
) -> DocumentSummary:
    """Assemble a DocumentSummary with all layered labels computed."""
    automatic = compute_automatic_preference(tesseract, unlimited_ocr)
    final = compute_final_preference(automatic, human_preference)
    matched = compute_auto_match(auto.auto_selected_backend, final)
    return DocumentSummary(
        document_id=document_id,
        profile_class=profile_class,
        tesseract=tesseract,
        unlimited_ocr=unlimited_ocr,
        auto=auto,
        automatic_preference=automatic,
        human_preference=human_preference,
        final_preference=final,
        auto_matched_final_preference=matched,
        review_reasons=list(review_reasons or []),
    )
