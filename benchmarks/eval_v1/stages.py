"""Stage-status vocabulary for the V1 evaluation pipeline.

Under Authorization A the stage matrix used ``SKIPPED``, which conflated
"deliberately not attempted" with "attempted but no infrastructure to
support it." A.1 introduces an explicit vocabulary so every cell in the
stage matrix carries an unambiguous machine-readable status.

The five statuses:

- ``EXECUTED``: the stage ran to a well-defined output and that output is
  recorded in the pair's stage-result payload.
- ``NOT_APPLICABLE``: the stage genuinely does not apply to this
  ``(corpus, document, stage)`` combination — e.g. no table-ground-truth
  exists for QASPER, so a table-adjudication stage against a QASPER
  document is NOT_APPLICABLE. A machine-readable ``reason`` is required.
- ``DEFECT``: the stage was supposed to run but a defect prevented it.
  A ``reason`` is required. **The presence of any ``DEFECT`` cell in
  A.1's final stage matrix means A.1 is not complete.**
- ``REQUIRES_REVIEW``: the machinery correctly refused to produce a
  result because doing so would require a methodological decision that
  is not yet frozen — used by the adjudication stage when a
  ``(Q1, Q2, Q3)`` combination is not present in Appendix B's mapping.
- ``INFRASTRUCTURE_READY_NOT_EXECUTED``: the stage's plumbing exists and
  passes its own contract tests, but the stage was deliberately not
  executed under A.1 because doing so would engage a moving instrument
  (e.g. live LLM answer / judge / RAGAS). A ``reason`` is required and
  should cite the authorization boundary.

A.1 acceptance forbids ``DEFECT`` cells. The other four statuses are all
valid outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StageStatus(StrEnum):
    EXECUTED = "EXECUTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DEFECT = "DEFECT"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    INFRASTRUCTURE_READY_NOT_EXECUTED = "INFRASTRUCTURE_READY_NOT_EXECUTED"


@dataclass(frozen=True)
class StageResult:
    """One cell in the (document, parser, stage) matrix.

    Invariants (enforced in ``__post_init__``):
    - ``status == EXECUTED`` must not carry a ``reason``.
    - Any other status must carry a non-empty ``reason``.
    - ``payload`` is free-form JSON-serializable data. For ``EXECUTED``
      cells it typically records the stage's actual output; for
      non-EXECUTED cells it may carry diagnostic evidence for the reason.
    """

    stage: str
    status: StageStatus
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is StageStatus.EXECUTED:
            if self.reason:
                raise ValueError(
                    f"stage {self.stage!r} EXECUTED must not carry a reason; got {self.reason!r}"
                )
        else:
            if not self.reason:
                raise ValueError(
                    f"stage {self.stage!r} status {self.status.value} requires a non-empty reason"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "reason": self.reason,
            "payload": dict(self.payload),
        }


def executed(stage: str, payload: dict[str, Any] | None = None) -> StageResult:
    return StageResult(stage=stage, status=StageStatus.EXECUTED, payload=payload or {})


def not_applicable(stage: str, reason: str, payload: dict[str, Any] | None = None) -> StageResult:
    return StageResult(stage=stage, status=StageStatus.NOT_APPLICABLE, reason=reason, payload=payload or {})


def defect(stage: str, reason: str, payload: dict[str, Any] | None = None) -> StageResult:
    return StageResult(stage=stage, status=StageStatus.DEFECT, reason=reason, payload=payload or {})


def requires_review(stage: str, reason: str, payload: dict[str, Any] | None = None) -> StageResult:
    return StageResult(stage=stage, status=StageStatus.REQUIRES_REVIEW, reason=reason, payload=payload or {})


def infrastructure_ready_not_executed(
    stage: str, reason: str, payload: dict[str, Any] | None = None
) -> StageResult:
    return StageResult(
        stage=stage,
        status=StageStatus.INFRASTRUCTURE_READY_NOT_EXECUTED,
        reason=reason,
        payload=payload or {},
    )
