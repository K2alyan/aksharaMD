"""Network boundary — smoke behavior on firewall/probe/positive-control states.

Rules exercised (spec §5.3, §5.9, §7):
- Pre-smoke positive control failure -> INCONCLUSIVE, no invocations.
- Per-invocation egress reachable -> DEFECT record written, then smoke halts FAIL.
- Post-smoke positive control failure -> INCONCLUSIVE.
- Firewall cleanup failure -> FAIL.
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
    SequencedPairProbeBackend,
    _open_https_result,
    _open_tcp_result,
    make_four_adapters,
    make_three_synthetic_docs,
    positive_control_failing,
    positive_control_passing,
    positive_control_sequence,
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
        cuda_device_name="fake NVIDIA",
    )


def _build_runner(
    *,
    run_dir: Path,
    probe_backend,
    positive_control,
    firewall_backend=None,
    adapters=None,
) -> SmokeRunner:
    from tests.smoke_b1a_7b.fakes import fake_vram_sampler_factory
    return SmokeRunner(
        contracts=load_contracts(),
        environment=_pinned_env(),
        smoke_documents=make_three_synthetic_docs(),
        adapters=adapters or make_four_adapters(),
        probe_backend=probe_backend,
        firewall_backend=firewall_backend or FakeFirewallBackend(),
        positive_control=positive_control,
        run_dir=run_dir,
        program_path_for_firewall="/fake/parser_worker.exe",
        resource_sampler_factory=FakeResourceSampler,
        vram_sampler_factory=fake_vram_sampler_factory,
    )


def test_pre_smoke_positive_control_fail_marks_inconclusive_no_records(run_dir: Path) -> None:
    runner = _build_runner(
        run_dir=run_dir,
        probe_backend=BlockedProbeBackend(),
        positive_control=positive_control_failing(),
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.INCONCLUSIVE
    assert result.reason == "pre_smoke_positive_control_failed"
    assert result.pairs_attempted == 0
    assert result.pairs_recorded == 0


def test_per_invocation_egress_reachable_writes_defect_then_halts_fail(run_dir: Path) -> None:
    # First invocation returns "open" (egress reachable); the runner
    # must still write a DEFECT record for that invocation, then halt.
    probe = SequencedPairProbeBackend([
        (_open_tcp_result(), _open_https_result()),  # invocation 1: egress reachable
    ])
    runner = _build_runner(
        run_dir=run_dir,
        probe_backend=probe,
        positive_control=positive_control_passing(),
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.FAIL
    assert result.harness_defect is not None
    assert result.harness_defect["kind"] == "network_egress_not_blocked"
    # Only one pair attempted before the halt.
    assert result.pairs_attempted == 1
    assert result.pairs_recorded == 1
    # The one record on disk should be marked with network_egress_blocked=false
    # AND still be a valid EXECUTED/DEFECT record (the invocation's
    # ParseOutcome was EXECUTED synthetic, so exit_status=EXECUTED,
    # but network_egress_blocked=false).
    records = list(run_dir.rglob("execution_record.json"))
    assert len(records) == 1
    record_data = load_execution_record(records[0])
    assert record_data["network_egress_blocked"] is False


def test_post_smoke_positive_control_fail_marks_inconclusive(run_dir: Path) -> None:
    runner = _build_runner(
        run_dir=run_dir,
        probe_backend=BlockedProbeBackend(),
        positive_control=positive_control_sequence([True, False]),  # pre passes, post fails
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.INCONCLUSIVE
    assert result.reason == "post_smoke_positive_control_failed"
    # All 12 pairs still attempted before the post-fail was observed.
    assert result.pairs_attempted == 12
    assert result.pairs_recorded == 12


def test_firewall_cleanup_failure_marks_fail(run_dir: Path) -> None:
    fw_backend = FakeFirewallBackend()
    fw_backend.linger_after_remove = True  # remove call fails to remove
    runner = _build_runner(
        run_dir=run_dir,
        probe_backend=BlockedProbeBackend(),
        positive_control=positive_control_passing(),
        firewall_backend=fw_backend,
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.FAIL
    assert result.harness_defect is not None
    assert result.harness_defect["kind"] == "firewall_rule_cleanup_broken"


def test_happy_path_all_blocked_12_records(run_dir: Path) -> None:
    runner = _build_runner(
        run_dir=run_dir,
        probe_backend=BlockedProbeBackend(),
        positive_control=positive_control_passing(),
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    assert result.pairs_attempted == 12
    assert result.pairs_recorded == 12
    # All 12 execution records on disk.
    records = list(run_dir.rglob("execution_record.json"))
    assert len(records) == 12
