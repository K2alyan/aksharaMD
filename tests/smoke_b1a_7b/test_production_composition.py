"""Production composition tests — end-to-end with fake dependencies.

The composition layer is factored so tests exercise the entire
decision path (contracts -> preflight -> adapter construction ->
runner) against fakes. Real backend factories are never invoked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.contracts import load_contracts
from benchmarks.eval_v1.smoke_b1a_7b.environment import EnvironmentSnapshot
from benchmarks.eval_v1.smoke_b1a_7b.production_composition import (
    EXIT_FAIL,
    EXIT_INCONCLUSIVE,
    EXIT_PASS,
    ProductionSmokeContext,
    build_positive_control_from_probe_backend,
    build_smoke_adapters,
    build_smoke_documents_from_spec,
    compose_and_run,
    preflight_environment_from_snapshot,
    result_to_exit_code,
    run_preflight_or_raise,
)
from benchmarks.eval_v1.smoke_b1a_7b.smoke_runner import (
    SmokeOutcome,
    SmokeResult,
)
from tests.smoke_b1a_7b.fakes import (
    BlockedProbeBackend,
    FakeFirewallBackend,
    FakeResourceSampler,
    fake_vram_sampler_factory,
    make_four_adapters,
    positive_control_passing,
)

# ---------------------------------------------------------------------------
# Fake payload resolver.


class _FakePayloadResolver:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = dict(mapping)
        self.calls: list[str] = []

    def pdf_bytes(self, canonical_id: str) -> bytes:
        self.calls.append(canonical_id)
        return self._mapping[canonical_id]


# ---------------------------------------------------------------------------
# build_smoke_documents_from_spec — loads the three frozen IDs.


def test_build_smoke_documents_loads_the_three_spec_ids() -> None:
    contracts = load_contracts()
    resolver = _FakePayloadResolver({
        "PMC5773191.1": b"%PDF-pmc",
        "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77": b"%PDF-dl",
        "2025-19924": b"%PDF-fr",
    })
    docs = build_smoke_documents_from_spec(
        contracts=contracts, payload_resolver=resolver,
    )
    assert len(docs) == 3
    ids = {d.canonical_id for d in docs}
    assert ids == {
        "PMC5773191.1",
        "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
        "2025-19924",
    }


def test_build_smoke_documents_defers_payload_reads_to_invocation_time() -> None:
    """The resolver should NOT be called until each SmokeDocument's
    ``pdf_bytes_source`` is invoked."""
    contracts = load_contracts()
    resolver = _FakePayloadResolver({
        "PMC5773191.1": b"a",
        "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77": b"b",
        "2025-19924": b"c",
    })
    docs = build_smoke_documents_from_spec(
        contracts=contracts, payload_resolver=resolver,
    )
    assert resolver.calls == []  # not called yet
    for d in docs:
        d.pdf_bytes_source()
    assert set(resolver.calls) == {
        "PMC5773191.1",
        "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
        "2025-19924",
    }


# ---------------------------------------------------------------------------
# build_smoke_adapters — one adapter per parser in the contract slate.


def test_build_smoke_adapters_returns_all_four_parsers() -> None:
    from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import SubprocessResult
    from tests.smoke_b1a_7b.test_subprocess_and_real_adapter import (
        FakeSubprocessInvoker,
    )

    def _handler(_inv):
        return SubprocessResult(
            stdout=b'{"status": "EXECUTED", "markdown": "ok"}',
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=_handler)
    contracts = load_contracts()
    hex64 = "a" * 64
    smoke_adapters = build_smoke_adapters(
        contracts=contracts,
        subprocess_invoker=invoker,
        package_source_shas={p: hex64 for p in ("aksharamd-reference", "marker",
                                                  "docling", "markitdown")},
        adapter_source_shas={p: hex64 for p in ("aksharamd-reference", "marker",
                                                  "docling", "markitdown")},
        model_artifact_shas={"aksharamd-reference": None, "marker": hex64,
                              "docling": hex64, "markitdown": None},
        offline_env={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                      "DOCLING_ARTIFACTS_OFFLINE": "1"},
    )
    ordered_ids = [a.parser_id for a in smoke_adapters.iter_ordered()]
    assert ordered_ids == [
        "aksharamd-reference", "marker", "docling", "markitdown",
    ]


# ---------------------------------------------------------------------------
# result_to_exit_code — deterministic mapping.


def _fake_result(outcome: SmokeOutcome) -> SmokeResult:
    return SmokeResult(
        outcome=outcome, reason="test", pairs_attempted=0, pairs_recorded=0,
    )


def test_result_to_exit_code_pass() -> None:
    assert result_to_exit_code(_fake_result(SmokeOutcome.PASS)) == EXIT_PASS
    assert EXIT_PASS == 0


def test_result_to_exit_code_fail() -> None:
    assert result_to_exit_code(_fake_result(SmokeOutcome.FAIL)) == EXIT_FAIL
    assert EXIT_FAIL == 1


def test_result_to_exit_code_inconclusive() -> None:
    assert result_to_exit_code(_fake_result(SmokeOutcome.INCONCLUSIVE)) == EXIT_INCONCLUSIVE
    assert EXIT_INCONCLUSIVE == 6


# ---------------------------------------------------------------------------
# build_positive_control_from_probe_backend — wraps a ProbeBackend.


def test_positive_control_wrapper_runs_the_probe() -> None:
    pc = build_positive_control_from_probe_backend(BlockedProbeBackend())
    obs = pc.run()
    # BlockedProbeBackend yields both-blocked -> blocked=True.
    assert obs.blocked is True


# ---------------------------------------------------------------------------
# preflight_environment_from_snapshot — bridge dataclass shapes.


def test_preflight_env_from_snapshot_carries_cuda_availability() -> None:
    snap = EnvironmentSnapshot(
        python_version="3.12.2", platform_string="Windows-11-...-SP0",
        git_commit="deadbeef", cpu_physical_cores=8,
        cuda_version="12.6", cuda_driver_version="12.6", cuda_device_name="fake",
    )
    prod = preflight_environment_from_snapshot(
        snap,
        package_versions={"aksharamd": "0.3.6"},
        model_cache_paths={"marker": Path("/fake")},
        env_vars={"HF_HUB_OFFLINE": "1"},
        adapter_source_bytes={"marker": b"x"},
    )
    assert prod.cuda_available is True
    assert prod.python_version == "3.12.2"
    assert prod.package_versions["aksharamd"] == "0.3.6"


def test_preflight_env_from_snapshot_cuda_absent_when_cuda_version_is_none() -> None:
    snap = EnvironmentSnapshot(
        python_version="3.12.2", platform_string="Linux-6", git_commit="d",
        cpu_physical_cores=4, cuda_version=None,
        cuda_driver_version=None, cuda_device_name=None,
    )
    prod = preflight_environment_from_snapshot(
        snap, package_versions={}, model_cache_paths={},
        env_vars={}, adapter_source_bytes={},
    )
    assert prod.cuda_available is False


# ---------------------------------------------------------------------------
# run_preflight_or_raise — happy path + failure path.


_REAL_A = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_REAL_B = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
_REAL_C = "cafef00dcafef00dcafef00dcafef00dcafef00dcafef00dcafef00dcafef00d"
_REAL_D = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _green_prod_env() -> object:
    import sys as _sys

    from benchmarks.eval_v1.smoke_b1a_7b.preflight import ProductionEnvironment
    contracts = load_contracts()
    return ProductionEnvironment(
        python_version=contracts.pinned_python_version(),
        platform_string=contracts.platform_prefix() + "-SP0",
        package_versions=dict(contracts.pinned_packages()),
        cuda_available=True, cuda_version="12.6",
        model_cache_paths={"marker": Path("/x"), "docling": Path("/y")},
        env_vars={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "DOCLING_ARTIFACTS_OFFLINE": "1"},
        adapter_source_bytes={"aksharamd-reference": b"x", "marker": b"x",
                                "docling": b"x", "markitdown": b"x"},
        package_source_shas={
            "aksharamd-reference": _REAL_A, "marker": _REAL_B,
            "docling": _REAL_C, "markitdown": _REAL_D,
        },
        model_artifact_shas={
            "aksharamd-reference": None, "marker": _REAL_A,
            "docling": _REAL_B, "markitdown": None,
        },
        firewall_program_path=_sys.executable,
    )


def test_run_preflight_or_raise_passes_in_green_env() -> None:
    run_preflight_or_raise(
        contracts=load_contracts(),
        environment=_green_prod_env(),  # type: ignore[arg-type]
        fs_probe=lambda _p: (True, 100),
    )


def test_run_preflight_or_raise_raises_on_drift() -> None:
    from benchmarks.eval_v1.smoke_b1a_7b.preflight import PreflightError, ProductionEnvironment

    env = _green_prod_env()
    env = ProductionEnvironment(  # type: ignore[arg-type]
        python_version="3.11.0",  # drift
        platform_string=env.platform_string,
        package_versions=env.package_versions,
        cuda_available=env.cuda_available,
        cuda_version=env.cuda_version,
        model_cache_paths=env.model_cache_paths,
        env_vars=env.env_vars,
        adapter_source_bytes=env.adapter_source_bytes,
    )
    with pytest.raises(PreflightError, match="preflight failed"):
        run_preflight_or_raise(
            contracts=load_contracts(),
            environment=env,
            fs_probe=lambda _p: (True, 100),
        )


# ---------------------------------------------------------------------------
# compose_and_run — end-to-end drive against a fake context.


def _build_fake_context(run_dir: Path) -> ProductionSmokeContext:
    contracts = load_contracts()
    resolver = _FakePayloadResolver({
        "PMC5773191.1": b"%PDF-pmc",
        "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77": b"%PDF-dl",
        "2025-19924": b"%PDF-fr",
    })
    smoke_documents = build_smoke_documents_from_spec(
        contracts=contracts, payload_resolver=resolver,
    )
    smoke_adapters = make_four_adapters()  # synthetic parser adapters
    env = EnvironmentSnapshot(
        python_version=contracts.pinned_python_version(),
        platform_string=contracts.platform_prefix() + "-SP0",
        git_commit="deadbeef",
        cpu_physical_cores=8,
        cuda_version="12.6",
        cuda_driver_version="12.6",
        cuda_device_name="fake",
    )
    return ProductionSmokeContext(
        contracts=contracts,
        environment=env,
        smoke_adapters=smoke_adapters,
        smoke_documents=smoke_documents,
        probe_backend=BlockedProbeBackend(),
        firewall_backend=FakeFirewallBackend(),
        positive_control=positive_control_passing(),
        resource_sampler_factory=FakeResourceSampler,
        vram_sampler_factory=fake_vram_sampler_factory,
        program_path_for_firewall="/fake/parser_worker.exe",
        run_dir=run_dir,
    )


def test_compose_and_run_happy_path_returns_pass(tmp_path: Path) -> None:
    ctx = _build_fake_context(tmp_path)
    result = compose_and_run(ctx)
    assert result.outcome is SmokeOutcome.PASS
    assert result.pairs_attempted == 12
    assert result.pairs_recorded == 12
    assert result_to_exit_code(result) == EXIT_PASS
