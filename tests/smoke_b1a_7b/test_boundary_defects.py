"""Parser-vs-harness defect boundary — spec §7.

Rules exercised:
- Parser-level DEFECT (timeout, exception, oom, cuda_unavailable,
  native_crash) records the outcome and continues.
- Empty-output-on-success is EXECUTED, not DEFECT.
- Schema/provenance/environment/network/blinding failures stop.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.eval_v1.smoke_b1a_7b.contracts import load_contracts
from benchmarks.eval_v1.smoke_b1a_7b.environment import EnvironmentSnapshot
from benchmarks.eval_v1.smoke_b1a_7b.execution_record import load_execution_record
from benchmarks.eval_v1.smoke_b1a_7b.smoke_runner import SmokeOutcome, SmokeRunner
from tests.smoke_b1a_7b.fakes import (
    BlockedProbeBackend,
    FakeFirewallBackend,
    FakeResourceSampler,
    defect_response,
    empty_success_response,
    executed_response,
    make_four_adapters,
    make_three_synthetic_docs,
    positive_control_passing,
)


def _pinned_env() -> EnvironmentSnapshot:
    c = load_contracts()
    return EnvironmentSnapshot(
        python_version=c.pinned_python_version(),
        platform_string=c.platform_prefix() + "-SP0",
        git_commit="deadbeef",
        cpu_physical_cores=8,
        cuda_version="12.6",
        cuda_driver_version="12.6",
        cuda_device_name="fake",
    )


def _runner(run_dir: Path, adapters) -> SmokeRunner:
    from tests.smoke_b1a_7b.fakes import fake_vram_sampler_factory
    return SmokeRunner(
        contracts=load_contracts(),
        environment=_pinned_env(),
        smoke_documents=make_three_synthetic_docs(),
        adapters=adapters,
        probe_backend=BlockedProbeBackend(),
        firewall_backend=FakeFirewallBackend(),
        positive_control=positive_control_passing(),
        run_dir=run_dir,
        program_path_for_firewall="/fake",
        resource_sampler_factory=FakeResourceSampler,
        vram_sampler_factory=fake_vram_sampler_factory,
    )


def test_parser_timeout_continues_and_records_defect(run_dir: Path) -> None:
    adapters = make_four_adapters(
        marker_response=defect_response("marker_timeout_600s"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    assert result.pairs_attempted == 12
    assert result.pairs_recorded == 12
    # Marker fired DEFECT on each of the 3 docs; parser_defects has 3 entries.
    marker_defects = [d for d in result.parser_defects if d["parser_id"] == "marker"]
    assert len(marker_defects) == 3
    assert all(d["defect_reason"] == "marker_timeout_600s" for d in marker_defects)


def test_parser_cuda_unavailable_continues(run_dir: Path) -> None:
    adapters = make_four_adapters(
        docling_response=defect_response("docling_cuda_unavailable"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    docling_defects = [d for d in result.parser_defects if d["parser_id"] == "docling"]
    assert len(docling_defects) == 3


def test_parser_native_crash_continues(run_dir: Path) -> None:
    adapters = make_four_adapters(
        reference_response=defect_response("reference_parser_native_crash"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    ref_defects = [d for d in result.parser_defects if d["parser_id"] == "aksharamd-reference"]
    assert len(ref_defects) == 3


def test_empty_output_on_success_is_executed_not_defect(run_dir: Path) -> None:
    """Docling's documented empty-output-on-success is EXECUTED with
    zero-length output, per parser-execution contract §6."""
    adapters = make_four_adapters(
        docling_response=empty_success_response(),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    # No parser defects (docling returned empty EXECUTED, not DEFECT).
    docling_defects = [d for d in result.parser_defects if d["parser_id"] == "docling"]
    assert docling_defects == []
    # But each docling record must record output_bytes=0.
    records = [
        load_execution_record(p) for p in run_dir.rglob("execution_record.json")
        if "docling" in str(p)
    ]
    assert len(records) == 3
    for r in records:
        assert r["exit_status"] == "EXECUTED"
        assert r["output_bytes"] == 0


def test_environment_drift_pre_run_fails_before_any_pair(run_dir: Path) -> None:
    """Simulated environment drift: python_version does not match pin.
    Runner must FAIL without invoking any parser."""
    c = load_contracts()
    env = EnvironmentSnapshot(
        python_version="3.11.0",  # drift from pinned 3.12.2
        platform_string=c.platform_prefix() + "-SP0",
        git_commit="deadbeef",
        cpu_physical_cores=8,
        cuda_version="12.6",
        cuda_driver_version="12.6",
        cuda_device_name="fake",
    )
    from tests.smoke_b1a_7b.fakes import fake_vram_sampler_factory
    runner = SmokeRunner(
        contracts=c,
        environment=env,
        smoke_documents=make_three_synthetic_docs(),
        adapters=make_four_adapters(),
        probe_backend=BlockedProbeBackend(),
        firewall_backend=FakeFirewallBackend(),
        positive_control=positive_control_passing(),
        run_dir=run_dir,
        program_path_for_firewall="/fake",
        resource_sampler_factory=FakeResourceSampler,
        vram_sampler_factory=fake_vram_sampler_factory,
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.FAIL
    assert result.harness_defect is not None
    assert result.harness_defect["kind"] == "environment_drift"
    assert result.pairs_attempted == 0


def test_multiple_parser_defects_across_slate_still_pass(run_dir: Path) -> None:
    """Different DEFECTs on different parsers, plus one healthy: still PASS."""
    adapters = make_four_adapters(
        reference_response=executed_response("# ok\n"),
        marker_response=defect_response("marker_timeout_600s"),
        docling_response=defect_response("docling_cuda_oom"),
        markitdown_response=defect_response("markitdown_exception:ValueError"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    assert len(result.parser_defects) == 9  # 3 parsers * 3 docs
    assert result.pairs_attempted == 12
    assert result.pairs_recorded == 12
