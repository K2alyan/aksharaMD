"""Preflight checks for real-provenance hashes and firewall-program binding.

These are the new preflight surfaces that block placeholder SHAs and
mis-bound firewall programs from ever reaching an execution record.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.contracts import load_contracts
from benchmarks.eval_v1.smoke_b1a_7b.preflight import (
    PreflightError,
    ProductionEnvironment,
    require_preflight_pass,
    run_preflight,
)

_HEX64_A = "a" * 64  # placeholder (single-char repetition)
_HEX64_ZEROS = "0" * 64  # canonical placeholder
_REAL_A = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_REAL_B = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
_REAL_C = "cafef00dcafef00dcafef00dcafef00dcafef00dcafef00dcafef00dcafef00d"
_REAL_D = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _fs_probe_ok(_p: Path) -> tuple[bool, int]:
    return True, 1_000_000


def _green_env(overrides: dict | None = None) -> ProductionEnvironment:
    """A green ProductionEnvironment where every check passes,
    including the new provenance + firewall-program checks."""
    contracts = load_contracts()
    base = {
        "python_version": contracts.pinned_python_version(),
        "platform_string": contracts.platform_prefix() + "-SP0",
        "package_versions": dict(contracts.pinned_packages()),
        "cuda_available": True,
        "cuda_version": "12.6",
        "model_cache_paths": {
            "marker": Path("/fake/marker"),
            "docling": Path("/fake/docling"),
        },
        "env_vars": {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DOCLING_ARTIFACTS_OFFLINE": "1",
        },
        "adapter_source_bytes": {
            "aksharamd-reference": b"x", "marker": b"x",
            "docling": b"x", "markitdown": b"x",
        },
        "package_source_shas": {
            "aksharamd-reference": _REAL_A,
            "marker": _REAL_B,
            "docling": _REAL_C,
            "markitdown": _REAL_D,
        },
        "model_artifact_shas": {
            "aksharamd-reference": None,
            "marker": _REAL_A,
            "docling": _REAL_B,
            "markitdown": None,
        },
        "firewall_program_path": sys.executable,
    }
    if overrides:
        base.update(overrides)
    return ProductionEnvironment(**base)


# ---------------------------------------------------------------------------
# Green path.


def test_green_env_passes_all_checks_including_new_ones() -> None:
    summary = run_preflight(
        contracts=load_contracts(),
        environment=_green_env(),
        fs_probe=_fs_probe_ok,
    )
    assert summary.all_passed, summary.failed_names()
    # The three new checks are visible in the summary.
    names = {c.name for c in summary.checks}
    assert "package_source_hashes" in names
    assert "model_artifact_hashes" in names
    assert "firewall_program_binding" in names


# ---------------------------------------------------------------------------
# package_source_hashes.


def test_package_source_all_zeros_placeholder_fails() -> None:
    env = _green_env({"package_source_shas": {
        "aksharamd-reference": _HEX64_ZEROS,
        "marker": _REAL_B, "docling": _REAL_C, "markitdown": _REAL_D,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "package_source_hashes" in summary.failed_names()


def test_package_source_single_char_repetition_fails() -> None:
    env = _green_env({"package_source_shas": {
        "aksharamd-reference": _HEX64_A,  # "a"*64 — placeholder
        "marker": _REAL_B, "docling": _REAL_C, "markitdown": _REAL_D,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "package_source_hashes" in summary.failed_names()


def test_package_source_missing_parser_fails() -> None:
    env = _green_env({"package_source_shas": {
        "aksharamd-reference": _REAL_A,
        "marker": _REAL_B,
        "docling": _REAL_C,
        # markitdown missing
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "package_source_hashes" in summary.failed_names()


def test_package_source_wrong_length_fails() -> None:
    env = _green_env({"package_source_shas": {
        "aksharamd-reference": "abc123",
        "marker": _REAL_B, "docling": _REAL_C, "markitdown": _REAL_D,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "package_source_hashes" in summary.failed_names()


def test_package_source_non_hex_fails() -> None:
    env = _green_env({"package_source_shas": {
        "aksharamd-reference": "z" * 64,
        "marker": _REAL_B, "docling": _REAL_C, "markitdown": _REAL_D,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "package_source_hashes" in summary.failed_names()


# ---------------------------------------------------------------------------
# model_artifact_hashes.


def test_marker_model_placeholder_fails() -> None:
    env = _green_env({"model_artifact_shas": {
        "aksharamd-reference": None,
        "marker": _HEX64_ZEROS,   # placeholder
        "docling": _REAL_B,
        "markitdown": None,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "model_artifact_hashes" in summary.failed_names()


def test_docling_model_placeholder_fails() -> None:
    env = _green_env({"model_artifact_shas": {
        "aksharamd-reference": None,
        "marker": _REAL_A,
        "docling": _HEX64_A,
        "markitdown": None,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "model_artifact_hashes" in summary.failed_names()


def test_missing_marker_model_hash_fails() -> None:
    env = _green_env({"model_artifact_shas": {
        "aksharamd-reference": None,
        # marker missing
        "docling": _REAL_B,
        "markitdown": None,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "model_artifact_hashes" in summary.failed_names()


def test_cpu_only_parser_none_model_is_admissible() -> None:
    """aksharamd-reference and markitdown have no model. Supplying
    ``None`` for their model_artifact_shas is admissible."""
    env = _green_env({"model_artifact_shas": {
        "aksharamd-reference": None,
        "marker": _REAL_A,
        "docling": _REAL_B,
        "markitdown": None,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "model_artifact_hashes" not in summary.failed_names()


def test_cpu_only_parser_placeholder_model_is_still_refused() -> None:
    """If a caller supplies a value for a CPU-only parser, it must
    not be a placeholder — otherwise the placeholder could reach an
    execution record."""
    env = _green_env({"model_artifact_shas": {
        "aksharamd-reference": _HEX64_ZEROS,  # placeholder
        "marker": _REAL_A,
        "docling": _REAL_B,
        "markitdown": None,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "model_artifact_hashes" in summary.failed_names()


# ---------------------------------------------------------------------------
# firewall_program_binding.


def test_firewall_program_empty_fails() -> None:
    env = _green_env({"firewall_program_path": ""})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "firewall_program_binding" in summary.failed_names()


def test_firewall_program_missing_on_disk_fails() -> None:
    env = _green_env({"firewall_program_path": "/does/not/exist/python.exe"})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "firewall_program_binding" in summary.failed_names()


def test_firewall_program_bound_to_sys_executable_passes() -> None:
    env = _green_env({"firewall_program_path": sys.executable})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    assert "firewall_program_binding" not in summary.failed_names()


# ---------------------------------------------------------------------------
# require_preflight_pass end-to-end.


def test_require_preflight_pass_raises_on_placeholder_pkg_sha() -> None:
    env = _green_env({"package_source_shas": {
        "aksharamd-reference": _HEX64_ZEROS,
        "marker": _REAL_B, "docling": _REAL_C, "markitdown": _REAL_D,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    with pytest.raises(PreflightError, match="package_source_hashes"):
        require_preflight_pass(summary)


def test_require_preflight_pass_raises_on_placeholder_model_sha() -> None:
    env = _green_env({"model_artifact_shas": {
        "aksharamd-reference": None,
        "marker": _HEX64_ZEROS,
        "docling": _REAL_B,
        "markitdown": None,
    }})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    with pytest.raises(PreflightError, match="model_artifact_hashes"):
        require_preflight_pass(summary)


def test_require_preflight_pass_raises_on_bad_firewall_binding() -> None:
    env = _green_env({"firewall_program_path": ""})
    summary = run_preflight(
        contracts=load_contracts(), environment=env, fs_probe=_fs_probe_ok,
    )
    with pytest.raises(PreflightError, match="firewall_program_binding"):
        require_preflight_pass(summary)


# ---------------------------------------------------------------------------
# Composition-integration: a placeholder in the composition-supplied
# hashes would halt at preflight, never reaching compose_and_run.


def test_placeholder_sha_cannot_reach_execution_record_via_composition(
    tmp_path: Path,
) -> None:
    """The composition path invokes preflight before constructing
    smoke adapters. A placeholder in ``package_source_shas`` therefore
    halts before any pair record can be written.
    """
    from benchmarks.eval_v1.smoke_b1a_7b.environment import EnvironmentSnapshot
    from benchmarks.eval_v1.smoke_b1a_7b.production_composition import (
        preflight_environment_from_snapshot,
        run_preflight_or_raise,
    )

    contracts = load_contracts()
    snap = EnvironmentSnapshot(
        python_version=contracts.pinned_python_version(),
        platform_string=contracts.platform_prefix() + "-SP0",
        git_commit="deadbeef", cpu_physical_cores=8,
        cuda_version="12.6", cuda_driver_version="12.6",
        cuda_device_name="fake",
    )
    prod_env = preflight_environment_from_snapshot(
        snap,
        package_versions=dict(contracts.pinned_packages()),
        model_cache_paths={"marker": Path("/x"), "docling": Path("/y")},
        env_vars={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "DOCLING_ARTIFACTS_OFFLINE": "1"},
        adapter_source_bytes={"aksharamd-reference": b"x", "marker": b"x",
                                "docling": b"x", "markitdown": b"x"},
        package_source_shas={pid: _HEX64_ZEROS for pid in
                              ("aksharamd-reference", "marker",
                               "docling", "markitdown")},
        model_artifact_shas={"aksharamd-reference": None, "marker": _REAL_A,
                              "docling": _REAL_B, "markitdown": None},
        firewall_program_path=sys.executable,
    )
    with pytest.raises(PreflightError, match="package_source_hashes"):
        run_preflight_or_raise(
            contracts=contracts, environment=prod_env, fs_probe=_fs_probe_ok,
        )
