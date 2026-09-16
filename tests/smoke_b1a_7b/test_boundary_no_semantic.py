"""No-semantic-inspection boundary — spec §6.

Harness acceptance decisions (PASS/FAIL) must be computable entirely
from metadata, hashes, schemas, filesystem mechanics, and structural
reviewer-artifact checks. No code path in the smoke_runner reads
markdown text for quality.

These tests set the markdown to nonsense that would fail any quality
gauge, and verify the runner still returns PASS.
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


def _runner(run_dir: Path, markdown: str) -> SmokeRunner:
    adapters = make_four_adapters(
        reference_response=executed_response(markdown),
        marker_response=executed_response(markdown),
        docling_response=executed_response(markdown),
        markitdown_response=executed_response(markdown),
    )
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


def test_gibberish_markdown_still_passes_infrastructure(run_dir: Path) -> None:
    gibberish = "asdf asdf asdf ☠☠☠ \x00 not-real-markdown  wat"
    runner = _runner(run_dir, gibberish)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS


def test_stub_only_markdown_still_passes(run_dir: Path) -> None:
    stub = "[OMITTED]\n[OMITTED]\n[OMITTED]\n"
    runner = _runner(run_dir, stub)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS


def test_replacement_char_markdown_still_passes(run_dir: Path) -> None:
    junk = "�" * 500
    runner = _runner(run_dir, junk)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS


def test_no_ground_truth_overlap_fields_in_records(run_dir: Path) -> None:
    """Verify that execution records contain none of the prohibited
    quality fields. This is a structural test on the record schema —
    the harness has no place to write these even if a downstream stage
    tried."""
    runner = _runner(run_dir, "# ok\n")
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    prohibited = {
        "aksharamd_readiness_score", "aksharamd_severity_band",
        "aksharamd_warning_list",
        "W_DROPPED_CONTENT", "W_GIBBERISH", "W_PLACEHOLDER_STUB",
        "W_ENCODING_ARTIFACTS", "W_TABLE_MISSING", "W_MULTICOLUMN_ORDER",
        "W_HEADER_FOOTER_TABLE_GARBLED",
        "pmc_word_overlap_ratio", "doclaynet_region_agreement",
        "q1_answer", "q2_answer", "q3_answer",
        "severity_label", "derived_label",
    }
    for record_path in run_dir.rglob("execution_record.json"):
        record = load_execution_record(record_path)
        overlap = set(record.keys()) & prohibited
        assert overlap == set(), f"{record_path} contains prohibited: {overlap}"
