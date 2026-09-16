"""Firewall-rule lifecycle + per-invocation egress probe.

Two independent surfaces live here:

- ``FirewallRuleManager`` — creates / verifies / cleans up the
  Windows Defender Firewall outbound-block rule. Delegates OS calls
  to an injectable ``FirewallBackend``. The default backend targets
  Windows PowerShell; synthetic tests inject a fake backend that
  records the calls without touching the OS.
- ``run_probes`` — runs the two per-invocation network probes
  (TCP-connect and HTTPS-GET) against the bare IP canary. Delegates
  the actual socket work to an injectable ``ProbeBackend``. Synthetic
  tests inject a fake backend that returns canned exceptions or
  successes; production uses ``RealProbeBackend`` (still OS-touching,
  so held out of ordinary test runs by the conftest guards).

The classification logic lives here in pure Python and is exercised
by tests against ``FakeProbeBackend`` alone. The real OS is never
required for correctness testing.
"""
from __future__ import annotations

import errno
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Constants pinned by the smoke-spec (kept here so the classification logic
# is deterministic even before ``contracts.load_contracts`` is called; the
# smoke_runner still verifies these against the config canary + timeouts).

CANARY_HOST = "1.1.1.1"
CANARY_PORT_TCP = 443
CANARY_URL = "https://1.1.1.1/"
PROBE_TIMEOUT_SECONDS = 5.0
FIREWALL_RULE_DISPLAY_NAME = "AksharaMD-Smoke-Egress-Block"

# Expected-failure classification: a probe that ends up raising one of
# these classes / errno values counts as "egress blocked."
_EXPECTED_EXCEPTIONS = (
    ConnectionRefusedError,
    TimeoutError,
    OSError,
)
_EXPECTED_ERRNOS = {
    errno.ECONNREFUSED,
    errno.ETIMEDOUT,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.EACCES,
}

# DNS-only failures (``socket.gaierror``) are *not* on the expected list.
# The probe uses a bare IP so gaierror should not occur; if it does, we
# refuse to certify egress-blocked, per §4.3 of the smoke spec.
_DNS_FAILURE_EXCEPTIONS = (socket.gaierror,)


# ---------------------------------------------------------------------------
# Probe backend protocol.


@dataclass(frozen=True)
class ProbeResult:
    """One probe's outcome. ``ok`` is meaningful only in the sense
    that the caller decides what a successful probe means; the
    classifier reads ``exc_class_name`` and ``errno_int`` to decide
    whether egress is blocked."""

    name: str
    connected: bool
    exc_class_name: str | None
    errno_int: int | None
    detail: str


class ProbeBackend(Protocol):
    """Injectable network-probe backend.

    A production backend actually opens sockets. The default fake
    (see FakeProbeBackend below) returns canned results.
    """

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult: ...

    def https_get(self, url: str, timeout: float) -> ProbeResult: ...


# ---------------------------------------------------------------------------
# Real backend. Held behind an explicit constructor; conftest hard-blocks
# instantiation during tests.


class RealProbeBackend:
    """Real socket-touching backend. Never instantiate this from
    tests. Conftest hard-refuses this class."""

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return ProbeResult(
                name="tcp_connect", connected=True,
                exc_class_name=None, errno_int=None,
                detail="socket connected — egress NOT blocked",
            )
        except _DNS_FAILURE_EXCEPTIONS as exc:  # pragma: no cover
            return ProbeResult(
                name="tcp_connect", connected=False,
                exc_class_name=type(exc).__name__, errno_int=None,
                detail=f"dns failure (not admissible): {exc!r}",
            )
        except _EXPECTED_EXCEPTIONS as exc:
            return ProbeResult(
                name="tcp_connect", connected=False,
                exc_class_name=type(exc).__name__,
                errno_int=getattr(exc, "errno", None),
                detail=f"expected block-like exception: {exc!r}",
            )
        except Exception as exc:  # pragma: no cover
            return ProbeResult(
                name="tcp_connect", connected=False,
                exc_class_name=type(exc).__name__,
                errno_int=getattr(exc, "errno", None),
                detail=f"unexpected exception (not admissible): {exc!r}",
            )

    def https_get(self, url: str, timeout: float) -> ProbeResult:
        # Deliberately uses ``http.client.HTTPSConnection`` (HTTPS-only
        # by construction) rather than ``urllib.request.urlopen``. The
        # latter accepts any registered URL scheme including ``file:``
        # and custom schemes, which Bandit flags (B310) as an audit
        # concern. Since the probe is meaningful only for HTTPS, we
        # bypass URL-scheme dispatch entirely.
        import http.client
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if parts.scheme != "https":
            return ProbeResult(
                name="https_get", connected=False,
                exc_class_name="ValueError", errno_int=None,
                detail=f"refused non-https url: {url!r}",
            )
        host = parts.hostname or ""
        port = parts.port or 443
        path = parts.path or "/"

        try:
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
            try:
                conn.request("GET", path)
                resp = conn.getresponse()
                resp.read()  # drain
                return ProbeResult(
                    name="https_get", connected=True,
                    exc_class_name=None, errno_int=None,
                    detail=f"http {resp.status} — egress NOT blocked",
                )
            finally:
                conn.close()
        except _DNS_FAILURE_EXCEPTIONS as exc:  # pragma: no cover
            return ProbeResult(
                name="https_get", connected=False,
                exc_class_name=type(exc).__name__, errno_int=None,
                detail=f"dns failure (not admissible): {exc!r}",
            )
        except _EXPECTED_EXCEPTIONS as exc:
            return ProbeResult(
                name="https_get", connected=False,
                exc_class_name=type(exc).__name__,
                errno_int=getattr(exc, "errno", None),
                detail=f"expected block-like exception: {exc!r}",
            )
        except Exception as exc:  # pragma: no cover
            return ProbeResult(
                name="https_get", connected=False,
                exc_class_name=type(exc).__name__,
                errno_int=getattr(exc, "errno", None),
                detail=f"unexpected exception (not admissible): {exc!r}",
            )


# ---------------------------------------------------------------------------
# Classification.


def probe_is_admissible_block(pr: ProbeResult) -> bool:
    """True iff this probe result counts as evidence of egress block.

    Rules (spec §4.3):
    - connected -> False (probe reached the canary; egress NOT blocked).
    - DNS failure -> False (not admissible; bare IP shouldn't gaierror).
    - Exception class in expected list -> True IF class matches; if the
      exception is a plain OSError, admissibility depends on errno being
      in the expected errno set. Concrete subclasses
      (ConnectionRefusedError, TimeoutError) are admissible unconditionally.
    - Any other exception -> False.
    """
    if pr.connected:
        return False
    if pr.exc_class_name is None:
        # Something went sideways; refuse to certify.
        return False
    # DNS failure by class name.
    if pr.exc_class_name in {c.__name__ for c in _DNS_FAILURE_EXCEPTIONS}:
        return False
    # Concrete subclasses admissible unconditionally.
    if pr.exc_class_name in {"ConnectionRefusedError", "TimeoutError"}:
        return True
    # Plain OSError: admissible only if errno is on the expected list.
    if pr.exc_class_name == "OSError":
        return pr.errno_int in _EXPECTED_ERRNOS
    # A urllib.error.URLError wrapping an admissible OSError is
    # accepted via its errno_int; other class names are refused.
    if pr.errno_int in _EXPECTED_ERRNOS:
        return True
    return False


@dataclass(frozen=True)
class NetworkEgressObservation:
    """Combined observation from the two probes at parser-invocation
    entry. ``blocked`` is True iff both probes returned admissible
    block evidence."""

    probe_tcp_connect: ProbeResult
    probe_https_get: ProbeResult
    blocked: bool


def run_probes(backend: ProbeBackend) -> NetworkEgressObservation:
    """Execute both probes and combine to a single observation.

    Both probes must be admissible for ``blocked = True``.
    """
    tcp = backend.tcp_connect(CANARY_HOST, CANARY_PORT_TCP, PROBE_TIMEOUT_SECONDS)
    https = backend.https_get(CANARY_URL, PROBE_TIMEOUT_SECONDS)
    blocked = probe_is_admissible_block(tcp) and probe_is_admissible_block(https)
    return NetworkEgressObservation(
        probe_tcp_connect=tcp,
        probe_https_get=https,
        blocked=blocked,
    )


# ---------------------------------------------------------------------------
# Firewall rule lifecycle.


class FirewallBackend(Protocol):
    """Injectable OS-firewall backend for rule lifecycle.

    Production backend calls PowerShell (New-NetFirewallRule /
    Get-NetFirewallRule / Remove-NetFirewallRule). Tests inject a
    fake that records call sequences.
    """

    def create_outbound_block_rule(
        self, *, display_name: str, program_path: str
    ) -> None: ...

    def get_rule_state(self, *, display_name: str) -> dict[str, Any]: ...

    def remove_rule(self, *, display_name: str) -> None: ...


class FirewallRuleError(RuntimeError):
    """Rule cannot be created, verified, or cleaned up."""


@dataclass(frozen=True)
class FirewallRuleState:
    display_name: str
    enabled: bool
    verified_at_utc: str


class FirewallRuleManager:
    """Encapsulates the rule's lifecycle. Every state transition is
    verified by re-querying the backend; nothing is trusted on the
    strength of a successful create/remove call alone."""

    def __init__(self, backend: FirewallBackend) -> None:
        self._backend = backend

    def create_and_verify(self, *, program_path: str) -> FirewallRuleState:
        self._backend.create_outbound_block_rule(
            display_name=FIREWALL_RULE_DISPLAY_NAME, program_path=program_path,
        )
        state = self._backend.get_rule_state(display_name=FIREWALL_RULE_DISPLAY_NAME)
        enabled = bool(state.get("enabled"))
        if not enabled:
            raise FirewallRuleError(
                f"rule '{FIREWALL_RULE_DISPLAY_NAME}' created but not enabled"
            )
        return FirewallRuleState(
            display_name=FIREWALL_RULE_DISPLAY_NAME,
            enabled=True,
            verified_at_utc=datetime.now(UTC).isoformat(),
        )

    def cleanup(self) -> None:
        """Remove the rule and verify it is gone. If removal or the
        post-verify fails, raises FirewallRuleError — the smoke_runner
        treats this as a harness-level defect."""
        self._backend.remove_rule(display_name=FIREWALL_RULE_DISPLAY_NAME)
        try:
            state = self._backend.get_rule_state(display_name=FIREWALL_RULE_DISPLAY_NAME)
        except FirewallRuleError:
            return  # backend signals rule absent
        if state.get("enabled"):
            raise FirewallRuleError(
                f"rule '{FIREWALL_RULE_DISPLAY_NAME}' still enabled after cleanup"
            )
