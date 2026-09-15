"""Deterministic HTTP throttle + bounded backoff shared by the selection layer.

Two policies:

- ``FrHttpPolicy`` — Federal Register API.
  Normal pacing: >= 750 ms between requests (~1.33 req/s).
  On 429: honor ``Retry-After`` header if present, else 30/60/120/240/300s,
  max 5 retries, deterministic (no jitter).

- ``PmcHttpPolicy`` — PMC S3-hosted anonymous reads.
  Normal pacing: >= 100 ms between requests.
  On transient failure (429 / 5xx / network): 15/30/60/120/240s, max 5
  retries. After exhaustion the caller MUST stop — a network failure
  must never be interpreted as document ineligibility.

Deterministic pacing (no random jitter) is chosen deliberately so
reproducibility is a property of the code, not of luck.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class NetworkExhaustionError(RuntimeError):
    """Raised when bounded retries have been exhausted on a request.

    The caller MUST propagate this upward — it is NEVER acceptable to
    treat network exhaustion as evidence that a candidate is
    ineligible. That would silently change the selected set based on
    transient network conditions.
    """


@dataclass
class _PolicyState:
    """Shared mutable state — last-request wall-clock for pacing."""

    last_request_wall_utc: float = 0.0


@dataclass
class HttpPolicy:
    """A concrete retry + pacing policy applied by :func:`get`."""

    name: str
    min_interval_s: float
    backoff_schedule_s: tuple[int, ...]
    max_retries: int
    user_agent: str
    honor_retry_after: bool = True
    _state: _PolicyState = field(default_factory=_PolicyState)

    def _sleep_for_pacing(self) -> None:
        elapsed = time.monotonic() - self._state.last_request_wall_utc
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._state.last_request_wall_utc = time.monotonic()

    def _sleep_after_429(self, attempt: int, retry_after: str | None) -> None:
        if self.honor_retry_after and retry_after:
            try:
                delay = float(retry_after)
                time.sleep(min(delay, 300.0))
                return
            except ValueError:
                pass
        idx = min(attempt, len(self.backoff_schedule_s) - 1)
        time.sleep(self.backoff_schedule_s[idx])

    def get(self, url: str) -> tuple[bytes, dict[str, str]]:
        """GET ``url`` under this policy. Returns ``(body, headers)``.

        Raises :class:`NetworkExhaustionError` after exhausting retries.
        Non-transient HTTP errors (4xx other than 429) propagate
        immediately as :class:`urllib.error.HTTPError`.
        """
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._sleep_for_pacing()
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                # nosec B310: URL-scheme + host allowlisting is enforced by
                # the corpus-specific wrappers that call into this policy.
                with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
                    body = resp.read()
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return body, headers
            except urllib.error.HTTPError as e:
                last_exc = e
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    if attempt >= self.max_retries:
                        break
                    self._sleep_after_429(attempt, retry_after)
                    continue
                if 500 <= e.code < 600:
                    if attempt >= self.max_retries:
                        break
                    self._sleep_after_429(attempt, None)
                    continue
                # 4xx (not 429): permanent, do not retry.
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_exc = e
                if attempt >= self.max_retries:
                    break
                self._sleep_after_429(attempt, None)
                continue
        raise NetworkExhaustionError(
            f"{self.name}: retries exhausted for {url!r} "
            f"({self.max_retries + 1} attempts); last error: "
            f"{type(last_exc).__name__}: {last_exc}"
        ) from last_exc


USER_AGENT = "aksharamd-eval-v1/1.0 (+contact: ksrkklabs@gmail.com)"


def make_fr_policy() -> HttpPolicy:
    return HttpPolicy(
        name="federal_register",
        min_interval_s=0.75,
        backoff_schedule_s=(30, 60, 120, 240, 300),
        max_retries=5,
        user_agent=USER_AGENT,
    )


def make_pmc_policy() -> HttpPolicy:
    return HttpPolicy(
        name="pmc_oa_s3",
        min_interval_s=0.10,
        backoff_schedule_s=(15, 30, 60, 120, 240),
        max_retries=5,
        user_agent=USER_AGENT,
    )


__all__ = [
    "HttpPolicy",
    "NetworkExhaustionError",
    "USER_AGENT",
    "make_fr_policy",
    "make_pmc_policy",
]
