"""Per-detector wall-clock budgets for the scoring pipeline (P0.2).

Detectors are wrapped in a `DetectorBudget` context manager that records
their elapsed wall time on the CompilationContext and emits an
informational `W_DETECTOR_TIMEOUT` warning when they exceed their budget.

Enforcement model: **measure-and-warn, not interrupt.** A slow detector
still returns its result; the manifest records that it exceeded its
budget so downstream operators can decide whether to intervene. Signal-
based interruption (SIGALRM) is rejected because it is not portable to
Windows and not thread-safe; cooperative interruption via a shared flag
is available to detectors that opt in by reading `budget.expired`.

Foundation PR (P0.2). Compiler-pipeline wiring is intentionally out of
scope — individual detectors opt in as they are written or refactored,
starting with the substance detectors landing in Phase 1/2/3.
"""
from __future__ import annotations

import os
import time
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aksharamd.context import CompilationContext

# Default per-detector wall-clock budget in milliseconds. Override globally
# via the ``AKSHARAMD_DETECTOR_BUDGET_MS`` environment variable, or per
# detector by passing ``budget_ms=`` to the constructor.
DEFAULT_BUDGET_MS: int = int(
    os.environ.get("AKSHARAMD_DETECTOR_BUDGET_MS", "2000")
)


class DetectorBudget:
    """Context manager that measures a detector's wall time.

    On exit, records elapsed milliseconds on ``ctx.detector_timings[name]``.
    If the elapsed time exceeds ``budget_ms``, appends ``name`` to
    ``ctx.detector_timeouts`` and emits an informational
    ``W_DETECTOR_TIMEOUT`` warning. Never raises — a slow detector is
    not a failed detector.

    Cooperative interruption is available via the ``expired`` property:
    detectors that opt in can poll ``budget.expired`` in their inner
    loops and return early rather than continue past the budget.

    Example (opt-in cooperative):
        with DetectorBudget(ctx, "W_MULTICOLUMN_ORDER", budget_ms=5000) as budget:
            for page in document.pages:
                if budget.expired:
                    break
                score_page(page)
    """

    __slots__ = ("ctx", "name", "budget_ms", "_start")

    def __init__(
        self,
        ctx: CompilationContext,
        name: str,
        budget_ms: int | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("DetectorBudget name must not be blank")
        self.ctx = ctx
        self.name = name
        self.budget_ms = budget_ms if budget_ms is not None else DEFAULT_BUDGET_MS
        self._start: float | None = None

    def __enter__(self) -> DetectorBudget:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._start is not None
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self.ctx.detector_timings[self.name] = elapsed_ms
        if elapsed_ms > self.budget_ms:
            self.ctx.detector_timeouts.append(self.name)
            self.ctx.warn(
                "W_DETECTOR_TIMEOUT",
                (
                    f"Detector {self.name!r} took {elapsed_ms:.0f}ms, "
                    f"exceeding budget of {self.budget_ms}ms"
                ),
                metadata={
                    "detector": self.name,
                    "elapsed_ms": elapsed_ms,
                    "budget_ms": self.budget_ms,
                },
            )

    @property
    def elapsed_ms(self) -> float:
        """Elapsed milliseconds since __enter__. Zero before enter."""
        if self._start is None:
            return 0.0
        return (time.perf_counter() - self._start) * 1000.0

    @property
    def expired(self) -> bool:
        """True when the elapsed time has exceeded the budget.

        Detectors that opt in to cooperative interruption should poll
        this in their inner loops and return early on True.
        """
        return self.elapsed_ms > self.budget_ms
