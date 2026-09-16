"""Preflight verification branch coverage.

Injects ``ProductionEnvironment`` observations and a fake ``fs_probe``
so every failure branch is exercised without touching the OS.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.contracts import load_contracts
from benchmarks.eval_v1.smoke_b1a_7b.preflight import (
    EXPECTED_SMOKE_CANONICAL_IDS,
    PreflightError,
    ProductionEnvironment,
    require_preflight_pass,
    run_preflight,
)

_HEX64 = "a" * 64


def _green_env(overrides: dict | None = None) -> ProductionEnvironment:
    """A ProductionEnvironment where every preflight check passes."""
    contracts = load_contracts()
    base = {
        "python_version": contracts.pinned_python_version(),
        "platform_string": contracts.platform_prefix() + "-SP0",
        "package_versions": dict(contracts.pinned_packages()),
        "cuda_available": True,
        "cuda_version": "12.6",
        "model_cache_paths": {
            "marker": Path("/fake/marker/cache"),
            "docling": Path("/fake/docling/cache"),
        },
        "env_vars": {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DOCLING_ARTIFACTS_OFFLINE": "1",
        },
        "adapter_source_bytes": {
            "aksharamd-reference": b"module bytes here",
            "marker": b"module bytes here",
            "docling": b"module bytes here",
            "markitdown": b"module bytes here",
        },
    }
    if overrides:
        base.update(overrides)
    return ProductionEnvironment(**base)


def _fake_fs_probe_all_present(_path: Path) -> tuple[bool, int]:
    return True, 1_000_000  # exists and has 1 MB of content


def _fake_fs_probe_missing(_path: Path) -> tuple[bool, int]:
    return False, 0


def _fake_fs_probe_empty(_path: Path) -> tuple[bool, int]:
    return True, 0


# ---------------------------------------------------------------------------
# Green path.


def test_all_checks_pass_in_green_environment() -> None:
    summary = run_preflight(
        contracts=load_contracts(),
        environment=_green_env(),
        fs_probe=_fake_fs_probe_all_present,
    )
    assert summary.all_passed, summary.failed_names()
    assert len(summary.checks) == 9


def test_require_preflight_pass_returns_none_on_green() -> None:
    summary = run_preflight(
        contracts=load_contracts(),
        environment=_green_env(),
        fs_probe=_fake_fs_probe_all_present,
    )
    require_preflight_pass(summary)  # no raise


# ---------------------------------------------------------------------------
# Individual failure branches.


def test_python_version_drift_fails() -> None:
    env = _green_env({"python_version": "3.11.0"})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "python_version" in summary.failed_names()


def test_platform_prefix_drift_fails() -> None:
    env = _green_env({"platform_string": "Linux-6.5.0-generic"})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "platform_prefix" in summary.failed_names()


def test_package_version_mismatch_fails() -> None:
    env = _green_env({"package_versions": {**dict(load_contracts().pinned_packages()),
                                            "marker-pdf": "9.9.9"}})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "package_versions" in summary.failed_names()


def test_cuda_unavailable_when_vlm_required_fails() -> None:
    env = _green_env({"cuda_available": False, "cuda_version": None})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "cuda_for_vlm" in summary.failed_names()


def test_missing_model_cache_path_fails() -> None:
    env = _green_env({"model_cache_paths": {}})  # no cache for marker/docling
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "model_cache_paths" in summary.failed_names()


def test_model_cache_path_missing_on_disk_fails() -> None:
    env = _green_env()
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_missing,
    )
    assert "model_cache_paths" in summary.failed_names()


def test_model_cache_path_empty_fails() -> None:
    env = _green_env()
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_empty,
    )
    assert "model_cache_paths" in summary.failed_names()


def test_missing_offline_env_var_fails() -> None:
    env = _green_env({"env_vars": {"HF_HUB_OFFLINE": "1",
                                    "TRANSFORMERS_OFFLINE": "1"}})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "offline_env_vars" in summary.failed_names()


def test_offline_env_var_set_to_zero_fails() -> None:
    env = _green_env({"env_vars": {"HF_HUB_OFFLINE": "0",
                                    "TRANSFORMERS_OFFLINE": "1",
                                    "DOCLING_ARTIFACTS_OFFLINE": "1"}})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "offline_env_vars" in summary.failed_names()


def test_empty_adapter_source_bytes_fails() -> None:
    env = _green_env({"adapter_source_bytes": {"aksharamd-reference": b""}})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "adapter_source_hashes" in summary.failed_names()


def test_no_adapter_source_bytes_at_all_fails() -> None:
    env = _green_env({"adapter_source_bytes": {}})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    assert "adapter_source_hashes" in summary.failed_names()


# ---------------------------------------------------------------------------
# Multi-failure aggregation + raise.


def test_multiple_failures_aggregate() -> None:
    env = _green_env({
        "python_version": "3.11.0",
        "cuda_available": False,
        "cuda_version": None,
        "env_vars": {},
    })
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    failed = set(summary.failed_names())
    assert "python_version" in failed
    assert "cuda_for_vlm" in failed
    assert "offline_env_vars" in failed


def test_require_preflight_pass_raises_on_any_failure() -> None:
    env = _green_env({"python_version": "3.11.0"})
    summary = run_preflight(
        contracts=load_contracts(),
        environment=env,
        fs_probe=_fake_fs_probe_all_present,
    )
    with pytest.raises(PreflightError, match="preflight failed"):
        require_preflight_pass(summary)


# ---------------------------------------------------------------------------
# Smoke-documents identity check — the three frozen canonical IDs.


def test_expected_smoke_canonical_ids_are_the_three_frozen_ones() -> None:
    assert EXPECTED_SMOKE_CANONICAL_IDS == frozenset({
        "PMC5773191.1",
        "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
        "2025-19924",
    })


def test_smoke_spec_json_matches_expected_ids() -> None:
    """The merged smoke-spec must contain exactly the frozen three IDs.
    Any drift is a critical harness/spec inconsistency."""
    contracts = load_contracts()
    summary = run_preflight(
        contracts=contracts,
        environment=_green_env(),
        fs_probe=_fake_fs_probe_all_present,
    )
    doc_check = next(c for c in summary.checks if c.name == "smoke_documents")
    assert doc_check.passed, doc_check.detail
