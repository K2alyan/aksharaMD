"""Timing + resource capture for one parser invocation.

Wall-clock via monotonic. CPU + peak RSS via psutil (sampled by an
injectable ``ResourceSampler``, so tests can drive the fields
without depending on real process activity). Peak VRAM via
``torch.cuda.max_memory_allocated`` when available.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass
class ResourceReading:
    cpu_seconds_user: float
    cpu_seconds_system: float
    peak_rss_bytes: int


class ResourceSampler(Protocol):
    def start(self) -> None: ...
    def stop(self) -> ResourceReading: ...


class RealPsutilSampler:
    """Production sampler using psutil.Process. Held behind an
    explicit class so tests never accidentally sample the real
    process."""

    def __init__(self) -> None:
        import psutil
        self._psutil = psutil
        self._proc = psutil.Process()
        self._start: ResourceReading | None = None
        self._peak_rss = 0

    def _snapshot(self) -> ResourceReading:
        times = self._proc.cpu_times()
        mem = self._proc.memory_info()
        return ResourceReading(
            cpu_seconds_user=times.user,
            cpu_seconds_system=times.system,
            peak_rss_bytes=mem.rss,
        )

    def start(self) -> None:
        self._start = self._snapshot()
        self._peak_rss = self._start.peak_rss_bytes

    def stop(self) -> ResourceReading:
        if self._start is None:
            raise RuntimeError("stop() before start()")
        end = self._snapshot()
        peak_rss = max(self._peak_rss, end.peak_rss_bytes)
        return ResourceReading(
            cpu_seconds_user=max(0.0, end.cpu_seconds_user - self._start.cpu_seconds_user),
            cpu_seconds_system=max(0.0, end.cpu_seconds_system - self._start.cpu_seconds_system),
            peak_rss_bytes=peak_rss,
        )


@dataclass(frozen=True)
class InvocationTiming:
    pair_started_at: str
    pair_finished_at: str
    wall_clock_seconds: float
    cpu_seconds_user: float
    cpu_seconds_system: float
    peak_rss_bytes: int
    peak_vram_bytes: int | None
    cuda_events: int | None


class VramSampler(Protocol):
    def before(self) -> None: ...
    def after(self) -> tuple[int | None, int | None]: ...


class NoopVramSampler:
    """VRAM sampler for CPU-only parsers. Both fields are None per
    the parser-execution contract §7."""

    def before(self) -> None:
        return None

    def after(self) -> tuple[int | None, int | None]:
        return None, None


class TorchVramSampler:
    """VRAM sampler for CUDA-capable parsers. Held behind an explicit
    class; test fixtures inject fakes for CUDA paths."""

    def before(self) -> None:  # pragma: no cover - real CUDA path
        import torch
        torch.cuda.reset_peak_memory_stats(device=0)

    def after(self) -> tuple[int | None, int | None]:  # pragma: no cover
        import torch
        return int(torch.cuda.max_memory_allocated(device=0)), 0


class InvocationTimer:
    """Context manager coordinating wall-clock, CPU+RSS, and VRAM
    sampling. Emits an ``InvocationTiming`` at the end.
    """

    def __init__(
        self,
        *,
        resource_sampler: ResourceSampler,
        vram_sampler: VramSampler,
    ) -> None:
        self._resource_sampler = resource_sampler
        self._vram_sampler = vram_sampler
        self._monotonic_start: float | None = None
        self._utc_start: str | None = None

    def __enter__(self) -> InvocationTimer:
        self._utc_start = datetime.now(UTC).isoformat()
        self._monotonic_start = time.monotonic()
        self._resource_sampler.start()
        self._vram_sampler.before()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Exceptions are the caller's responsibility to translate to
        # ParseOutcome; we still finalize timing.
        pass

    def finalize(self) -> InvocationTiming:
        if self._monotonic_start is None or self._utc_start is None:
            raise RuntimeError("finalize() before __enter__()")
        monotonic_end = time.monotonic()
        utc_end = datetime.now(UTC).isoformat()
        reading = self._resource_sampler.stop()
        peak_vram, cuda_events = self._vram_sampler.after()
        return InvocationTiming(
            pair_started_at=self._utc_start,
            pair_finished_at=utc_end,
            wall_clock_seconds=max(0.0, monotonic_end - self._monotonic_start),
            cpu_seconds_user=reading.cpu_seconds_user,
            cpu_seconds_system=reading.cpu_seconds_system,
            peak_rss_bytes=reading.peak_rss_bytes,
            peak_vram_bytes=peak_vram,
            cuda_events=cuda_events,
        )
