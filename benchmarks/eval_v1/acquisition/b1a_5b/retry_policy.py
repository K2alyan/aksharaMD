"""HTTP retry policy for the B1a-5b.1 acquisition orchestrator.

Contract locked with the human on 2026-09-15:

- 429: honor ``Retry-After`` if present; else exponential backoff.
- 5xx / timeout: bounded exponential backoff.
- Other 4xx (except 408 and 429): fail immediately.
- Maximum attempts: 5.

After exhaustion the caller emits ``SELECTED_ACQUISITION_FAILURE`` and
STOPS the run. It never substitutes a cutoff neighbor, moves to CAL,
or re-runs B1a-5a.

The policy is expressed as a pure callable so tests can inject a fake
``sleep`` and a fake ``time`` monotonic clock. In production the
policy sleeps between attempts; in tests we pass ``sleep=lambda s: None``
to keep the suite fast and deterministic.
"""
from __future__ import annotations

import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential-backoff retry policy for HTTP-shaped errors."""

    max_attempts: int = 5
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must be >= 0")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= initial_backoff_seconds")


class RetryExhaustedError(RuntimeError):
    """Raised when a callable failed on every attempt within the policy budget.

    Wraps the final exception so callers can inspect what specifically
    exhausted the budget. Callers translate this into
    ``AcquisitionStatus.SELECTED_ACQUISITION_FAILURE``.
    """

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(
            f"retry policy exhausted after {attempts} attempts; "
            f"last error: {type(last_error).__name__}: {last_error}"
        )
        self.attempts = attempts
        self.last_error = last_error


def _is_transient(exc: BaseException) -> bool:
    """True when the error is retryable per the locked policy."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code == 429 or code == 408:
            return True
        if 500 <= code <= 599:
            return True
        return False
    if isinstance(exc, urllib.error.URLError):
        # Network-layer error (DNS, connection reset, refused, ...);
        # treated as transient because it is not an HTTP-level 4xx.
        return True
    if isinstance(exc, OSError):
        # e.g. socket.timeout via `ConnectionResetError` on Windows;
        # covers rare cases HTTPError doesn't wrap.
        return True
    return False


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Parse an integer-seconds ``Retry-After`` header when present."""
    if not isinstance(exc, urllib.error.HTTPError):
        return None
    hdr = None
    try:
        hdr = exc.headers.get("Retry-After")  # type: ignore[union-attr]
    except AttributeError:
        return None
    if hdr is None:
        return None
    try:
        return max(0.0, float(hdr))
    except (TypeError, ValueError):
        # HTTP-date form ("Retry-After: Wed, 21 Oct 2015 07:28:00 GMT")
        # is legal but rare from the endpoints we use; if we hit one
        # we fall back to exponential backoff.
        return None


def with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
) -> T:
    """Run ``fn`` under the locked retry policy.

    Non-transient exceptions (permanent HTTP 4xx besides 408/429) are
    NOT retried; they propagate immediately so the caller can classify
    them (typically as ``IDENTITY_MISMATCH`` or ``UPSTREAM_STATE_CHANGED``
    depending on which HTTP code and which fetch).
    """
    attempt = 0
    last: BaseException | None = None
    backoff = policy.initial_backoff_seconds
    while attempt < policy.max_attempts:
        attempt += 1
        try:
            result = fn()
            if on_attempt is not None:
                on_attempt(attempt, None)
            return result
        except Exception as e:  # noqa: BLE001 — deliberate: classify below
            last = e
            if on_attempt is not None:
                on_attempt(attempt, e)
            if not _is_transient(e):
                raise
            if attempt >= policy.max_attempts:
                break
            ra = _retry_after_seconds(e)
            wait = ra if ra is not None else backoff
            if wait > 0:
                sleep(wait)
            backoff = min(policy.max_backoff_seconds, backoff * policy.backoff_multiplier)
    if last is None:  # unreachable — the loop only exits via an exception
        raise RuntimeError("retry loop exited without recording the last error")
    raise RetryExhaustedError(attempts=attempt, last_error=last)


__all__ = [
    "RetryExhaustedError",
    "RetryPolicy",
    "with_retry",
]
