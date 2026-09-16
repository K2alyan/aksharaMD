"""Capture the runtime environment snapshot at smoke start.

The snapshot is compared against the pinned values from the parser-
execution contract. Drift on any field that must be exact
(python_version, platform_string prefix, package versions, contract
config SHAs) is a harness-level defect.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentSnapshot:
    python_version: str
    platform_string: str
    git_commit: str
    cpu_physical_cores: int | None
    # CUDA fields are populated when torch reports CUDA. For CPU-only
    # parsers they are recorded per-invocation as null; for VLM
    # parsers they are validated to be non-null.
    cuda_version: str | None
    cuda_driver_version: str | None
    cuda_device_name: str | None


def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5
        )
        return out.decode("ascii").strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


def _cpu_physical_cores() -> int | None:
    try:
        import psutil
        return psutil.cpu_count(logical=False)
    except ImportError:
        return None


def _cuda_state() -> tuple[str | None, str | None, str | None]:
    """Return (cuda_runtime_version, cuda_driver_version, device_name)
    or (None, None, None) if CUDA is unavailable."""
    try:
        import torch
    except ImportError:
        return None, None, None
    if not torch.cuda.is_available():
        return None, None, None
    runtime = torch.version.cuda
    device_name = torch.cuda.get_device_name(0)
    # torch does not expose a driver version directly; nvidia-smi
    # would be a subprocess call, and we prefer not to shell out here.
    # Report torch.version.cuda for both fields when we cannot separate.
    # The B1a-7c freeze will decide whether the harness should shell to
    # nvidia-smi for the true driver number.
    driver = runtime
    return runtime, driver, device_name


def capture_environment() -> EnvironmentSnapshot:
    cuda_v, cuda_drv, cuda_name = _cuda_state()
    return EnvironmentSnapshot(
        python_version=sys.version.split()[0],
        platform_string=platform.platform(),
        git_commit=_git_commit(),
        cpu_physical_cores=_cpu_physical_cores(),
        cuda_version=cuda_v,
        cuda_driver_version=cuda_drv,
        cuda_device_name=cuda_name,
    )


class EnvironmentDriftError(RuntimeError):
    """Runtime environment does not match the pinned values."""


def verify_no_drift(
    *,
    captured: EnvironmentSnapshot,
    pinned_python_version: str,
    pinned_platform_prefix: str,
) -> None:
    if captured.python_version != pinned_python_version:
        raise EnvironmentDriftError(
            f"python_version drift: pinned={pinned_python_version!r} "
            f"captured={captured.python_version!r}"
        )
    if not captured.platform_string.startswith(pinned_platform_prefix):
        raise EnvironmentDriftError(
            f"platform_string prefix drift: pinned prefix={pinned_platform_prefix!r} "
            f"captured={captured.platform_string!r}"
        )
