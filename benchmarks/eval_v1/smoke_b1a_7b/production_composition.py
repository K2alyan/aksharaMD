"""Production composition — decision logic factored from dependency construction.

``entrypoint.main`` calls ``build_production_context`` (which
constructs the real Windows / PowerShell / subprocess backends) and
then ``compose_and_run`` (which runs the ``SmokeRunner`` with the
composed dependencies). The two functions are separate so tests
inject a ``ProductionSmokeContext`` built entirely from fakes and
drive ``compose_and_run`` end-to-end without touching the OS.

Every real-backend constructor is a factory callable so a test-only
composition can substitute fakes. The default factories point at
``RealPowerShellInvoker`` / ``RealFirewallBackend`` /
``RealSubprocessInvoker`` / ``RealProbeBackend``, all of which are
themselves ``# pragma: no cover``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .adapter_protocol import ParserAdapterProtocol
from .contracts import ContractPair, load_contracts
from .environment import EnvironmentSnapshot, capture_environment
from .firewall import (
    FirewallBackend,
    NetworkEgressObservation,
    ProbeBackend,
    run_probes,
)
from .preflight import (
    PreflightError,
    ProductionEnvironment,
    require_preflight_pass,
    run_preflight,
)
from .real_adapters import RealAdapterConfig, SubprocessParserAdapter
from .smoke_runner import (
    PositiveControlProbe,
    SmokeAdapters,
    SmokeDocument,
    SmokeOutcome,
    SmokeResult,
    SmokeRunner,
)
from .subprocess_runner import SubprocessInvoker

# ---------------------------------------------------------------------------
# Payload resolution — abstract so tests substitute the disk-read path.


class SmokePayloadResolver(Protocol):
    """Given a canonical_id, return the PDF bytes of the smoke document
    that has been pre-cached on disk by the operator."""

    def pdf_bytes(self, canonical_id: str) -> bytes: ...


# ---------------------------------------------------------------------------
# Composition context.


@dataclass(frozen=True)
class ProductionSmokeContext:
    """Everything a smoke needs to run, composed from injected
    dependencies. Populated either by ``build_production_context``
    (real backends) or by a test factory (fake backends)."""

    contracts: ContractPair
    environment: EnvironmentSnapshot
    smoke_adapters: SmokeAdapters
    smoke_documents: list[SmokeDocument]
    probe_backend: ProbeBackend
    firewall_backend: FirewallBackend
    positive_control: PositiveControlProbe
    resource_sampler_factory: Callable[[], Any]
    vram_sampler_factory: Callable[[str], Any]
    program_path_for_firewall: str
    run_dir: Path


# ---------------------------------------------------------------------------
# Adapter construction from contract config.


_WORKER_ARGV_PREFIX_DEFAULT = [
    "python", "-m", "benchmarks.eval_v1.smoke_b1a_7b.workers.main",
]


def _adapter_config_from_parser_spec(
    parser_spec: Mapping[str, Any],
    *,
    package_source_sha256: str,
    adapter_source_sha256: str,
    model_artifact_sha256: str | None,
) -> RealAdapterConfig:
    return RealAdapterConfig(
        parser_id=str(parser_spec["parser_id"]),
        package_version=str(parser_spec["package_version"]),
        package_source_sha256=package_source_sha256,
        adapter_source_sha256=adapter_source_sha256,
        parser_model_version=parser_spec.get("model_version_recorded_at_runtime") and "runtime",
        parser_model_artifact_sha256=model_artifact_sha256,
        timeout_seconds=float(parser_spec["timeout_seconds"]),
        is_vlm=bool(parser_spec.get("gpu_required", False)),
    )


def build_smoke_adapters(
    *,
    contracts: ContractPair,
    subprocess_invoker: SubprocessInvoker,
    package_source_shas: Mapping[str, str],
    adapter_source_shas: Mapping[str, str],
    model_artifact_shas: Mapping[str, str | None],
    offline_env: Mapping[str, str],
    worker_argv_prefix: list[str] | None = None,
) -> SmokeAdapters:
    """Construct one ``SubprocessParserAdapter`` per parser in the
    contract slate, keyed by parser_id.

    The three ``*_shas`` mappings come from the preflight-computed
    environment (adapter source bytes, wheel hashes, model hashes).
    """
    by_id: dict[str, ParserAdapterProtocol] = {}
    for spec in contracts.parsers():
        pid = str(spec["parser_id"])
        cfg = _adapter_config_from_parser_spec(
            spec,
            package_source_sha256=package_source_shas[pid],
            adapter_source_sha256=adapter_source_shas[pid],
            model_artifact_sha256=model_artifact_shas.get(pid),
        )
        by_id[pid] = SubprocessParserAdapter(
            config=cfg,
            subprocess_invoker=subprocess_invoker,
            worker_argv_prefix=list(worker_argv_prefix or _WORKER_ARGV_PREFIX_DEFAULT),
            offline_env=dict(offline_env),
        )

    missing = {"aksharamd-reference", "marker", "docling", "markitdown"} - by_id.keys()
    if missing:
        raise RuntimeError(
            f"contract slate missing required parser_id(s): {sorted(missing)}"
        )
    return SmokeAdapters(
        aksharamd_reference=by_id["aksharamd-reference"],
        marker=by_id["marker"],
        docling=by_id["docling"],
        markitdown=by_id["markitdown"],
    )


# ---------------------------------------------------------------------------
# Smoke-document construction.


def build_smoke_documents_from_spec(
    *,
    contracts: ContractPair,
    payload_resolver: SmokePayloadResolver,
) -> list[SmokeDocument]:
    """Load exactly the three canonical IDs from the merged smoke
    spec's ``smoke_documents.documents`` and pair each with a bytes
    source that defers to ``payload_resolver`` at invocation time.

    The reviewer contract requires that document IDs come from the
    spec, not the CLI. ``payload_resolver`` is the only degree of
    freedom the operator exposes, and it is not user-controlled — it
    resolves bytes from a pre-cached location on disk.
    """
    docs: list[SmokeDocument] = []
    for entry in contracts.smoke_documents():
        canonical_id = str(entry["canonical_id"])
        corpus = str(entry["corpus"])

        def _resolve(canonical_id: str = canonical_id) -> bytes:
            return payload_resolver.pdf_bytes(canonical_id)

        docs.append(
            SmokeDocument(
                canonical_id=canonical_id,
                corpus=corpus,
                pdf_bytes_source=_resolve,
            ),
        )
    return docs


# ---------------------------------------------------------------------------
# Positive control adapter (wraps a ProbeBackend into a PositiveControlProbe).


def build_positive_control_from_probe_backend(
    probe_backend: ProbeBackend,
) -> PositiveControlProbe:
    """Turn a plain ProbeBackend into a PositiveControlProbe by
    calling ``run_probes`` on the backend. The distinction is that
    the positive-control probe runs OUTSIDE the firewall context
    (its own probe backend), while the per-invocation probe runs
    INSIDE it. Both use the same classifier."""

    def _run() -> NetworkEgressObservation:
        return run_probes(probe_backend)

    return PositiveControlProbe(run=_run)


# ---------------------------------------------------------------------------
# End-to-end composition + run.


def compose_and_run(context: ProductionSmokeContext) -> SmokeResult:
    """Given a fully composed context, construct the ``SmokeRunner``
    and run it. This function is the boundary between composition
    and execution; it is safe to call with a fake context."""
    runner = SmokeRunner(
        contracts=context.contracts,
        environment=context.environment,
        smoke_documents=context.smoke_documents,
        adapters=context.smoke_adapters,
        probe_backend=context.probe_backend,
        firewall_backend=context.firewall_backend,
        positive_control=context.positive_control,
        run_dir=context.run_dir,
        program_path_for_firewall=context.program_path_for_firewall,
        resource_sampler_factory=context.resource_sampler_factory,
        vram_sampler_factory=context.vram_sampler_factory,
    )
    return runner.run()


# ---------------------------------------------------------------------------
# Preflight bridging.


def preflight_environment_from_snapshot(
    snapshot: EnvironmentSnapshot,
    *,
    package_versions: Mapping[str, str],
    model_cache_paths: Mapping[str, Path],
    env_vars: Mapping[str, str],
    adapter_source_bytes: Mapping[str, bytes],
    package_source_shas: Mapping[str, str] | None = None,
    model_artifact_shas: Mapping[str, str | None] | None = None,
    firewall_program_path: str = "",
) -> ProductionEnvironment:
    """Build a ``ProductionEnvironment`` from a ``EnvironmentSnapshot``
    plus the additional preflight-only observations. Kept as a small
    adapter so the two dataclasses do not have to share fields."""
    return ProductionEnvironment(
        python_version=snapshot.python_version,
        platform_string=snapshot.platform_string,
        package_versions=package_versions,
        cuda_available=snapshot.cuda_version is not None,
        cuda_version=snapshot.cuda_version,
        model_cache_paths=model_cache_paths,
        env_vars=env_vars,
        adapter_source_bytes=adapter_source_bytes,
        package_source_shas=dict(package_source_shas or {}),
        model_artifact_shas=dict(model_artifact_shas or {}),
        firewall_program_path=firewall_program_path,
    )


def run_preflight_or_raise(
    *,
    contracts: ContractPair,
    environment: ProductionEnvironment,
    fs_probe: Callable[[Path], tuple[bool, int]] | None = None,
) -> None:
    """Run preflight; raise ``PreflightError`` on any failure."""
    summary = run_preflight(
        contracts=contracts, environment=environment, fs_probe=fs_probe,
    )
    require_preflight_pass(summary)


# ---------------------------------------------------------------------------
# Result -> exit code mapping.


EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 6


def result_to_exit_code(result: SmokeResult) -> int:
    """Deterministic mapping from ``SmokeResult.outcome`` to a Unix
    exit code. Any outcome not on the ``SmokeOutcome`` enum is a
    harness defect; the mapping raises rather than returning a
    surprising default."""
    if result.outcome is SmokeOutcome.PASS:
        return EXIT_PASS
    if result.outcome is SmokeOutcome.FAIL:
        return EXIT_FAIL
    if result.outcome is SmokeOutcome.INCONCLUSIVE:
        return EXIT_INCONCLUSIVE
    raise RuntimeError(f"unrecognized SmokeOutcome: {result.outcome!r}")


# ---------------------------------------------------------------------------
# Real-backend context builder — every real branch behind #pragma no cover.


def build_production_context(  # pragma: no cover
    *,
    run_dir: Path,
    payload_resolver: SmokePayloadResolver,
    powershell_invoker_factory: Callable[[], Any] | None = None,
    firewall_backend_factory: Callable[[Any], FirewallBackend] | None = None,
    probe_backend_factory: Callable[[], ProbeBackend] | None = None,
    positive_control_probe_backend_factory: Callable[[], ProbeBackend] | None = None,
    subprocess_invoker_factory: Callable[[], SubprocessInvoker] | None = None,
    resource_sampler_factory: Callable[[], Any] | None = None,
    vram_sampler_factory: Callable[[str], Any] | None = None,
    package_source_shas: Mapping[str, str],
    adapter_source_shas: Mapping[str, str],
    model_artifact_shas: Mapping[str, str | None],
    offline_env: Mapping[str, str],
    package_versions: Mapping[str, str],
    model_cache_paths: Mapping[str, Path],
    env_vars: Mapping[str, str],
    adapter_source_bytes: Mapping[str, bytes],
    fs_probe: Callable[[Path], tuple[bool, int]] | None = None,
) -> ProductionSmokeContext:
    """Compose a production context by calling the real backend
    factories. ``# pragma: no cover``: real-backend construction is
    exercised only at real-smoke authorization time.

    Every construction step is injectable via a factory kwarg. When a
    factory is not supplied, its production default is used. Tests
    substitute all factories with fakes and drive the same code path.
    """
    from .firewall import RealProbeBackend
    from .real_firewall import RealFirewallBackend, RealPowerShellInvoker
    from .resources import NoopVramSampler, RealPsutilSampler, TorchVramSampler
    from .subprocess_runner import RealSubprocessInvoker

    ps_invoker_ctor = powershell_invoker_factory or RealPowerShellInvoker
    fw_backend_ctor = firewall_backend_factory or (
        lambda ps: RealFirewallBackend(invoker=ps)
    )
    probe_backend_ctor = probe_backend_factory or RealProbeBackend
    pc_probe_backend_ctor = positive_control_probe_backend_factory or RealProbeBackend
    subprocess_invoker_ctor = subprocess_invoker_factory or RealSubprocessInvoker
    resource_sampler_ctor = resource_sampler_factory or RealPsutilSampler
    vram_ctor = vram_sampler_factory or (
        lambda parser_id: (
            NoopVramSampler() if parser_id in {"aksharamd-reference", "markitdown"}
            else TorchVramSampler()
        )
    )

    # Firewall must block the exact executable RealSubprocessInvoker
    # will spawn. Derived from sys.executable — the operator does not
    # supply this; supplying a different path would risk successfully
    # blocking one executable while the parser subprocess uses another.
    import sys as _sys
    program_path_for_firewall = _sys.executable

    contracts = load_contracts()
    snapshot = capture_environment()
    prod_env = preflight_environment_from_snapshot(
        snapshot,
        package_versions=package_versions,
        model_cache_paths=model_cache_paths,
        env_vars=env_vars,
        adapter_source_bytes=adapter_source_bytes,
        package_source_shas=package_source_shas,
        model_artifact_shas=model_artifact_shas,
        firewall_program_path=program_path_for_firewall,
    )
    run_preflight_or_raise(
        contracts=contracts, environment=prod_env, fs_probe=fs_probe,
    )

    subprocess_invoker = subprocess_invoker_ctor()
    smoke_adapters = build_smoke_adapters(
        contracts=contracts,
        subprocess_invoker=subprocess_invoker,
        package_source_shas=package_source_shas,
        adapter_source_shas=adapter_source_shas,
        model_artifact_shas=model_artifact_shas,
        offline_env=offline_env,
    )
    smoke_documents = build_smoke_documents_from_spec(
        contracts=contracts, payload_resolver=payload_resolver,
    )
    probe_backend = probe_backend_ctor()
    firewall_backend = fw_backend_ctor(ps_invoker_ctor())
    positive_control = build_positive_control_from_probe_backend(pc_probe_backend_ctor())

    return ProductionSmokeContext(
        contracts=contracts,
        environment=snapshot,
        smoke_adapters=smoke_adapters,
        smoke_documents=smoke_documents,
        probe_backend=probe_backend,
        firewall_backend=firewall_backend,
        positive_control=positive_control,
        resource_sampler_factory=resource_sampler_ctor,
        vram_sampler_factory=vram_ctor,
        program_path_for_firewall=program_path_for_firewall,
        run_dir=run_dir,
    )


__all__ = [
    "EXIT_FAIL",
    "EXIT_INCONCLUSIVE",
    "EXIT_PASS",
    "PreflightError",
    "ProductionSmokeContext",
    "SmokePayloadResolver",
    "build_positive_control_from_probe_backend",
    "build_production_context",
    "build_smoke_adapters",
    "build_smoke_documents_from_spec",
    "compose_and_run",
    "preflight_environment_from_snapshot",
    "result_to_exit_code",
    "run_preflight_or_raise",
]
