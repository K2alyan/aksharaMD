"""Fake backends + parser adapters used by the synthetic tests.

None of these fakes touches the network, the OS firewall, or a real
parser. They implement the same Protocols as the real code so the
runner accepts them without special-casing.
"""
from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import (
    ParseOutcome,
    ParseStatus,
)
from benchmarks.eval_v1.smoke_b1a_7b.firewall import (
    NetworkEgressObservation,
    ProbeResult,
)
from benchmarks.eval_v1.smoke_b1a_7b.resources import (
    NoopVramSampler,
    ResourceReading,
    VramSampler,
)
from benchmarks.eval_v1.smoke_b1a_7b.smoke_runner import (
    PositiveControlProbe,
    SmokeAdapters,
    SmokeDocument,
)

# CPU-only parsers use NoopVramSampler which returns (None, None) —
# matches the parser-execution contract nullability for CPU-only.
CPU_ONLY_PARSERS = {"aksharamd-reference", "markitdown"}


class FakeVramSampler:
    """VLM-parser VRAM sampler for tests. Never touches CUDA."""

    def before(self) -> None:
        pass

    def after(self) -> tuple[int | None, int | None]:
        return 512_000_000, 42


def fake_vram_sampler_factory(parser_id: str) -> VramSampler:
    if parser_id in CPU_ONLY_PARSERS:
        return NoopVramSampler()
    return FakeVramSampler()

# ---------------------------------------------------------------------------
# Probe backend fakes.


def _blocked_tcp_result() -> ProbeResult:
    return ProbeResult(
        name="tcp_connect",
        connected=False,
        exc_class_name="ConnectionRefusedError",
        errno_int=errno.ECONNREFUSED,
        detail="fake: connection refused (egress blocked)",
    )


def _blocked_https_result() -> ProbeResult:
    return ProbeResult(
        name="https_get",
        connected=False,
        exc_class_name="OSError",
        errno_int=errno.ENETUNREACH,
        detail="fake: network unreachable (egress blocked)",
    )


def _open_tcp_result() -> ProbeResult:
    return ProbeResult(
        name="tcp_connect", connected=True,
        exc_class_name=None, errno_int=None,
        detail="fake: connected — egress NOT blocked",
    )


def _open_https_result() -> ProbeResult:
    return ProbeResult(
        name="https_get", connected=True,
        exc_class_name=None, errno_int=None,
        detail="fake: http response — egress NOT blocked",
    )


class BlockedProbeBackend:
    """Returns admissible-block results for both probes."""

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult:
        return _blocked_tcp_result()

    def https_get(self, url: str, timeout: float) -> ProbeResult:
        return _blocked_https_result()


class OpenProbeBackend:
    """Returns "connected" for both probes (egress reachable)."""

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult:
        return _open_tcp_result()

    def https_get(self, url: str, timeout: float) -> ProbeResult:
        return _open_https_result()


class DnsFailureProbeBackend:
    """Returns socket.gaierror-shaped result — DNS failure alone, not
    admissible per §4.3."""

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult:
        return ProbeResult(
            name="tcp_connect", connected=False,
            exc_class_name="gaierror", errno_int=None,
            detail="fake: dns lookup failure (not admissible)",
        )

    def https_get(self, url: str, timeout: float) -> ProbeResult:
        return ProbeResult(
            name="https_get", connected=False,
            exc_class_name="gaierror", errno_int=None,
            detail="fake: dns lookup failure (not admissible)",
        )


class SequencedProbeBackend:
    """Returns pre-programmed observations in sequence — first for
    invocation 1, then 2, and so on. Used to simulate a mid-smoke
    firewall failure."""

    def __init__(self, sequence: list[tuple[ProbeResult, ProbeResult]]) -> None:
        self._seq = list(sequence)
        self._i = 0

    def _next(self) -> tuple[ProbeResult, ProbeResult]:
        if self._i >= len(self._seq):
            # If exhausted, keep returning the last (blocked) pair.
            return _blocked_tcp_result(), _blocked_https_result()
        pair = self._seq[self._i]
        self._i += 1
        return pair

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult:
        tcp, _ = self._next()
        return tcp

    def https_get(self, url: str, timeout: float) -> ProbeResult:
        # https for a given invocation is the second element of the
        # sequence's tuple; SequencedProbeBackend returns the tuple in
        # tcp_connect, so we call the sequence a second time here.
        # Simpler: use a separate index for https.
        raise NotImplementedError(
            "SequencedProbeBackend does not support the two-call pattern; "
            "use SequencedPairProbeBackend"
        )


class SequencedPairProbeBackend:
    """Yields a full ``(tcp, https)`` pair per invocation. The runner
    calls tcp then https once per invocation."""

    def __init__(self, pairs: list[tuple[ProbeResult, ProbeResult]]) -> None:
        self._pairs = list(pairs)
        self._i = 0
        self._served_tcp_for_current = False

    def _advance_if_needed(self) -> tuple[ProbeResult, ProbeResult]:
        if self._i >= len(self._pairs):
            return _blocked_tcp_result(), _blocked_https_result()
        return self._pairs[self._i]

    def tcp_connect(self, host: str, port: int, timeout: float) -> ProbeResult:
        pair = self._advance_if_needed()
        self._served_tcp_for_current = True
        return pair[0]

    def https_get(self, url: str, timeout: float) -> ProbeResult:
        pair = self._advance_if_needed()
        result = pair[1]
        # Advance to the next pair after both probes of this
        # invocation have been served.
        if self._served_tcp_for_current:
            self._i += 1
            self._served_tcp_for_current = False
        return result


# ---------------------------------------------------------------------------
# Positive control fakes.


def positive_control_passing() -> PositiveControlProbe:
    def _run() -> NetworkEgressObservation:
        return NetworkEgressObservation(
            probe_tcp_connect=_open_tcp_result(),
            probe_https_get=_open_https_result(),
            blocked=False,
        )
    return PositiveControlProbe(run=_run)


def positive_control_failing() -> PositiveControlProbe:
    def _run() -> NetworkEgressObservation:
        return NetworkEgressObservation(
            probe_tcp_connect=_blocked_tcp_result(),
            probe_https_get=_blocked_https_result(),
            blocked=True,  # canary unreachable — positive control fails
        )
    return PositiveControlProbe(run=_run)


def positive_control_sequence(
    outcomes: list[bool],
) -> PositiveControlProbe:
    """Returns True/False alternately per call, in order. Useful for
    simulating pre-pass and post-fail."""
    it = iter(outcomes)

    def _run() -> NetworkEgressObservation:
        try:
            passing = next(it)
        except StopIteration:
            passing = False
        if passing:
            return NetworkEgressObservation(
                probe_tcp_connect=_open_tcp_result(),
                probe_https_get=_open_https_result(),
                blocked=False,
            )
        return NetworkEgressObservation(
            probe_tcp_connect=_blocked_tcp_result(),
            probe_https_get=_blocked_https_result(),
            blocked=True,
        )
    return PositiveControlProbe(run=_run)


# ---------------------------------------------------------------------------
# Firewall backend fake.


@dataclass
class FakeFirewallBackend:
    """Records calls without touching the OS. ``rule_state`` returns
    ``enabled=True`` after a create and ``enabled=False`` after a
    remove. Failure modes are triggered by setting attributes."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    rule_present: bool = False
    fail_on_create: bool = False
    fail_on_get: bool = False
    fail_on_remove: bool = False
    linger_after_remove: bool = False

    def create_outbound_block_rule(self, *, display_name: str, program_path: str) -> None:
        self.calls.append(("create", {"display_name": display_name, "program_path": program_path}))
        if self.fail_on_create:
            raise RuntimeError("fake: create failed")
        self.rule_present = True

    def get_rule_state(self, *, display_name: str) -> dict[str, Any]:
        self.calls.append(("get", {"display_name": display_name}))
        if self.fail_on_get:
            raise RuntimeError("fake: get failed")
        return {"display_name": display_name, "enabled": self.rule_present}

    def remove_rule(self, *, display_name: str) -> None:
        self.calls.append(("remove", {"display_name": display_name}))
        if self.fail_on_remove:
            raise RuntimeError("fake: remove failed")
        if not self.linger_after_remove:
            self.rule_present = False

    def verify_rule_absent(self, *, display_name: str) -> bool:
        self.calls.append(("verify_absent", {"display_name": display_name}))
        return not self.rule_present


# ---------------------------------------------------------------------------
# Resource sampler fake.


class FakeResourceSampler:
    """Returns fixed CPU + peak RSS numbers. Never touches psutil."""

    def start(self) -> None:
        pass

    def stop(self) -> ResourceReading:
        return ResourceReading(
            cpu_seconds_user=0.001,
            cpu_seconds_system=0.0005,
            peak_rss_bytes=8_000_000,
        )


# ---------------------------------------------------------------------------
# Parser adapter fakes.


@dataclass
class SyntheticAdapter:
    """Parser adapter that returns pre-programmed ParseOutcomes.

    The adapter's identity fields (versions, hashes) are canned. The
    outcome per-call is determined by ``response_factory`` — a callable
    receiving (pdf_bytes, canonical_id) and returning ParseOutcome.
    """

    parser_id: str
    _package_version: str
    _package_sha: str
    _adapter_sha: str
    _model_version: str | None
    _model_sha: str | None
    response_factory: Callable[[bytes, str], ParseOutcome]

    def package_version(self) -> str:
        return self._package_version

    def package_source_sha256(self) -> str:
        return self._package_sha

    def adapter_source_sha256(self) -> str:
        return self._adapter_sha

    def parser_model_version(self) -> str | None:
        return self._model_version

    def parser_model_artifact_sha256(self) -> str | None:
        return self._model_sha

    def compile(self, pdf_bytes: bytes, canonical_id: str) -> ParseOutcome:
        return self.response_factory(pdf_bytes, canonical_id)


def executed_response(markdown: str = "# fake heading\n\nsynthetic body.") -> Callable[[bytes, str], ParseOutcome]:
    return lambda _b, _c: ParseOutcome(
        status=ParseStatus.EXECUTED,
        markdown=markdown,
        stdout="",
        stderr="",
        defect_reason=None,
    )


def empty_success_response() -> Callable[[bytes, str], ParseOutcome]:
    return lambda _b, _c: ParseOutcome(
        status=ParseStatus.EXECUTED,
        markdown="",
        stdout="",
        stderr="",
        defect_reason=None,
    )


def defect_response(reason: str) -> Callable[[bytes, str], ParseOutcome]:
    return lambda _b, _c: ParseOutcome(
        status=ParseStatus.DEFECT,
        markdown=None,
        stdout="",
        stderr="fake defect: " + reason,
        defect_reason=reason,
    )


# Convenience factories.

_HEX64_A = "a" * 64
_HEX64_B = "b" * 64
_HEX64_C = "c" * 64
_HEX64_D = "d" * 64


def make_four_adapters(
    *,
    reference_response: Callable[[bytes, str], ParseOutcome] | None = None,
    marker_response: Callable[[bytes, str], ParseOutcome] | None = None,
    docling_response: Callable[[bytes, str], ParseOutcome] | None = None,
    markitdown_response: Callable[[bytes, str], ParseOutcome] | None = None,
) -> SmokeAdapters:
    ref = reference_response or executed_response("# reference synthetic\n")
    mk = marker_response or executed_response("# marker synthetic\n")
    dl = docling_response or executed_response("# docling synthetic\n")
    md = markitdown_response or executed_response("# markitdown synthetic\n")
    return SmokeAdapters(
        aksharamd_reference=SyntheticAdapter(
            parser_id="aksharamd-reference",
            _package_version="0.3.6",
            _package_sha=_HEX64_A,
            _adapter_sha=_HEX64_A,
            _model_version=None,
            _model_sha=None,
            response_factory=ref,
        ),
        marker=SyntheticAdapter(
            parser_id="marker",
            _package_version="1.10.2",
            _package_sha=_HEX64_B,
            _adapter_sha=_HEX64_B,
            _model_version="fake-marker-model-v0",
            _model_sha=_HEX64_B,
            response_factory=mk,
        ),
        docling=SyntheticAdapter(
            parser_id="docling",
            _package_version="2.107.0",
            _package_sha=_HEX64_C,
            _adapter_sha=_HEX64_C,
            _model_version="fake-docling-model-v0",
            _model_sha=_HEX64_C,
            response_factory=dl,
        ),
        markitdown=SyntheticAdapter(
            parser_id="markitdown",
            _package_version="0.1.6",
            _package_sha=_HEX64_D,
            _adapter_sha=_HEX64_D,
            _model_version=None,
            _model_sha=None,
            response_factory=md,
        ),
    )


# ---------------------------------------------------------------------------
# Smoke document fakes.


def make_three_synthetic_docs() -> list[SmokeDocument]:
    """Three synthetic docs standing in for the frozen smoke set.
    Canonical IDs are synthetic tokens that intentionally do NOT
    match any real V2 selection; the conftest guard would fire if a
    test tried to open a frozen payload."""
    return [
        SmokeDocument(
            canonical_id="SYN-PMC-1",
            corpus="pmc_oa",
            pdf_bytes_source=lambda: b"%PDF-1.5\nsynthetic-pmc-payload",
        ),
        SmokeDocument(
            canonical_id="SYN-DL-1",
            corpus="doclaynet",
            pdf_bytes_source=lambda: b"%PDF-1.5\nsynthetic-doclaynet-payload",
        ),
        SmokeDocument(
            canonical_id="SYN-FR-1",
            corpus="federal_register",
            pdf_bytes_source=lambda: b"%PDF-1.5\nsynthetic-federalregister-payload",
        ),
    ]
