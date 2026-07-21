"""Typed public views over the signals ``infer_pdf_portable`` returns.

The underlying runtime returns a rich ``dict[str, Any]`` for backward
compatibility with the benchmark harness. This module gives callers a
minimal typed handle over the most common fields — construct it with
:func:`InferenceResult.from_signals` when a typed reference is
convenient. The dict form remains the source of truth; this dataclass
does not attempt to cover every field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InferenceResult:
    """A typed view over one call to ``infer_pdf_portable``.

    Attributes
    ----------
    text:
        Extracted markdown text. Empty string on failure.
    exception:
        Category-label string from the underlying runner. Empty on
        success. Never a raw exception message.
    signals:
        Raw signals dict returned by ``infer_pdf_portable``. Keep as
        the source of truth for detail the typed fields don't cover.
    initial_chunk_size:
        Chunk size the orchestrator started with, after portable
        sizing resolution.
    final_chunk_size_used:
        Chunk size at the point of the final attempt (equal to
        initial on a clean first pass, otherwise the shrunk value).
    restart_count:
        Number of subprocess restarts (OOM-driven halvings) that
        preceded the terminal outcome. Zero on a clean first attempt.
    total_wall_seconds:
        Aggregate wall-clock across all worker invocations.
    """

    text: str
    exception: str
    signals: dict[str, Any] = field(default_factory=dict)
    initial_chunk_size: int = 0
    final_chunk_size_used: int = 0
    restart_count: int = 0
    total_wall_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return not self.exception

    @classmethod
    def from_signals(
        cls, text: str, exception: str, signals: dict[str, Any],
    ) -> InferenceResult:
        return cls(
            text=text or "",
            exception=exception or "",
            signals=signals or {},
            initial_chunk_size=int(signals.get("initial_chunk_size") or 0),
            final_chunk_size_used=int(signals.get("final_chunk_size_used") or 0),
            restart_count=int(signals.get("restart_count") or 0),
            total_wall_seconds=float(signals.get("total_wall_seconds") or 0.0),
        )
