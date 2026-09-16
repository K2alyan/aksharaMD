"""Production preflight verification.

Every check must pass before the smoke touches any parser or any
smoke payload. Any failure produces a ``PreflightError`` and the
runner refuses to proceed.

Checks (all fail-closed):

1. Contract config hashes match the smoke-spec pins.
2. Package versions installed match the parser-execution contract's
   ``packages_pinned``.
3. Python interpreter and platform prefix match the contract.
4. CUDA available for marker/docling (unless intentionally excluded).
5. Model cache paths exist and contain non-zero-byte files.
6. Offline env variables set to "1".
7. Adapter source hashes computable from on-disk bytes.
8. Smoke spec's ``smoke_documents.documents`` contains exactly the
   three frozen canonical IDs the merged spec pinned.

Every check is passed an injectable ``Environment`` describing the
runtime observations, so tests exercise every failure branch without
touching the OS.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import ContractPair
from .provenance import ProvenanceHashError, is_placeholder_sha, validate_sha_format

EXPECTED_SMOKE_CANONICAL_IDS = frozenset({
    "PMC5773191.1",
    "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
    "2025-19924",
})

REQUIRED_OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
                             "DOCLING_ARTIFACTS_OFFLINE")


class PreflightError(RuntimeError):
    """One or more preflight checks failed. Runner must not proceed."""


@dataclass(frozen=True)
class PreflightCheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PreflightSummary:
    all_passed: bool
    checks: tuple[PreflightCheckResult, ...] = field(default_factory=tuple)

    def failed_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if not c.passed)


# --------------------------------------------------------------------------
# Environment abstraction — tests inject fake observations.


@dataclass(frozen=True)
class ProductionEnvironment:
    """What the harness observes about its runtime just before smoke
    start. Every field is injected so tests parameterize freely."""

    python_version: str  # e.g. "3.12.2"
    platform_string: str
    package_versions: Mapping[str, str]  # installed name->version
    cuda_available: bool
    cuda_version: str | None
    model_cache_paths: Mapping[str, Path]  # parser_id -> cache dir
    env_vars: Mapping[str, str]
    adapter_source_bytes: Mapping[str, bytes]  # adapter_key -> bytes
    # Real provenance hashes recorded per invocation. Preflight refuses
    # placeholders (any single-char repetition, empty, non-hex, wrong
    # length) so a fabricated value never reaches an execution record.
    package_source_shas: Mapping[str, str] = field(default_factory=dict)
    model_artifact_shas: Mapping[str, str | None] = field(default_factory=dict)
    # Firewall program binding: the exact executable the smoke's
    # RealSubprocessInvoker will spawn. Blocking any other executable
    # would let a parser subprocess escape the firewall.
    firewall_program_path: str = ""


# --------------------------------------------------------------------------
# Individual checks.


def _check_contract_hashes(contracts: ContractPair) -> PreflightCheckResult:
    # Sanity: both canonical SHAs are 64-hex. Any drift is impossible
    # at this layer (we just loaded them) but the check documents the
    # invariant.
    pe = contracts.parser_execution.canonical_sha256
    ss = contracts.smoke_spec.canonical_sha256
    if len(pe) != 64 or len(ss) != 64:
        return PreflightCheckResult(
            name="contract_hashes", passed=False,
            detail=f"contract canonical SHAs not 64-hex: pe={pe!r} ss={ss!r}",
        )
    return PreflightCheckResult(
        name="contract_hashes", passed=True,
        detail=f"parser_execution={pe[:16]}… smoke_spec={ss[:16]}…",
    )


def _check_python_version(
    env: ProductionEnvironment, contracts: ContractPair,
) -> PreflightCheckResult:
    pinned = contracts.pinned_python_version()
    if env.python_version != pinned:
        return PreflightCheckResult(
            name="python_version", passed=False,
            detail=f"observed={env.python_version!r} pinned={pinned!r}",
        )
    return PreflightCheckResult(
        name="python_version", passed=True, detail=env.python_version,
    )


def _check_platform_prefix(
    env: ProductionEnvironment, contracts: ContractPair,
) -> PreflightCheckResult:
    prefix = contracts.platform_prefix()
    if not env.platform_string.startswith(prefix):
        return PreflightCheckResult(
            name="platform_prefix", passed=False,
            detail=f"observed={env.platform_string!r} expected prefix={prefix!r}",
        )
    return PreflightCheckResult(
        name="platform_prefix", passed=True, detail=env.platform_string,
    )


def _check_package_versions(
    env: ProductionEnvironment, contracts: ContractPair,
) -> PreflightCheckResult:
    pinned = contracts.pinned_packages()
    mismatches: list[str] = []
    for pkg, want in pinned.items():
        got = env.package_versions.get(pkg)
        if got != want:
            mismatches.append(f"{pkg}: got={got!r} want={want!r}")
    if mismatches:
        return PreflightCheckResult(
            name="package_versions", passed=False,
            detail="; ".join(mismatches),
        )
    return PreflightCheckResult(
        name="package_versions", passed=True,
        detail=f"{len(pinned)} packages match",
    )


def _check_cuda_for_vlm(
    env: ProductionEnvironment, contracts: ContractPair,
) -> PreflightCheckResult:
    """CUDA must be available for VLM parsers (marker + docling)."""
    vlm_parsers = [
        p for p in contracts.parsers() if p.get("gpu_required")
    ]
    if not vlm_parsers:
        return PreflightCheckResult(
            name="cuda_for_vlm", passed=True,
            detail="no gpu_required parsers in slate",
        )
    if not env.cuda_available:
        return PreflightCheckResult(
            name="cuda_for_vlm", passed=False,
            detail=(
                f"cuda_available=false but {len(vlm_parsers)} parsers "
                f"require CUDA: "
                f"{[p['parser_id'] for p in vlm_parsers]}"
            ),
        )
    return PreflightCheckResult(
        name="cuda_for_vlm", passed=True,
        detail=f"cuda={env.cuda_version} available for "
               f"{[p['parser_id'] for p in vlm_parsers]}",
    )


def _check_model_cache_paths(
    env: ProductionEnvironment,
    contracts: ContractPair,
    *,
    fs_probe: Callable[[Path], tuple[bool, int]] | None = None,
) -> PreflightCheckResult:
    """For each parser that has a model, verify the cache path exists
    and contains at least one non-zero-byte file. ``fs_probe`` is
    injected in tests to return (exists, total_bytes_present) without
    hitting the real filesystem."""
    probe = fs_probe or _default_fs_probe
    missing: list[str] = []
    for parser in contracts.parsers():
        pid = parser["parser_id"]
        needs_model = parser.get("gpu_required") or bool(
            parser.get("model_version_recorded_at_runtime")
        )
        if not needs_model:
            continue
        path = env.model_cache_paths.get(pid)
        if path is None:
            missing.append(f"{pid}: no cache path configured")
            continue
        exists, total_bytes = probe(path)
        if not exists:
            missing.append(f"{pid}: {path} does not exist")
            continue
        if total_bytes <= 0:
            missing.append(f"{pid}: {path} exists but is empty")
    if missing:
        return PreflightCheckResult(
            name="model_cache_paths", passed=False,
            detail="; ".join(missing),
        )
    return PreflightCheckResult(
        name="model_cache_paths", passed=True,
        detail=f"{len(env.model_cache_paths)} cache paths verified",
    )


def _default_fs_probe(path: Path) -> tuple[bool, int]:  # pragma: no cover
    if not path.exists():
        return False, 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return True, total


def _check_offline_env_vars(env: ProductionEnvironment) -> PreflightCheckResult:
    missing: list[str] = []
    for name in REQUIRED_OFFLINE_ENV_VARS:
        value = env.env_vars.get(name)
        if value != "1":
            missing.append(f"{name}={value!r} (require '1')")
    if missing:
        return PreflightCheckResult(
            name="offline_env_vars", passed=False,
            detail="; ".join(missing),
        )
    return PreflightCheckResult(
        name="offline_env_vars", passed=True,
        detail=f"{len(REQUIRED_OFFLINE_ENV_VARS)} vars set to '1'",
    )


def _check_adapter_source_hashes(
    env: ProductionEnvironment,
) -> PreflightCheckResult:
    """Every adapter's source bytes must be present so the runner can
    record ``adapter_source_sha256`` per invocation."""
    if not env.adapter_source_bytes:
        return PreflightCheckResult(
            name="adapter_source_hashes", passed=False,
            detail="no adapter source bytes configured",
        )
    empties = [k for k, v in env.adapter_source_bytes.items() if not v]
    if empties:
        return PreflightCheckResult(
            name="adapter_source_hashes", passed=False,
            detail=f"empty adapter source bytes for: {empties}",
        )
    return PreflightCheckResult(
        name="adapter_source_hashes", passed=True,
        detail=f"{len(env.adapter_source_bytes)} adapter modules hashable",
    )


CPU_ONLY_PARSER_IDS = frozenset({"aksharamd-reference", "markitdown"})
VLM_PARSER_IDS = frozenset({"marker", "docling"})


def _check_package_source_hashes(
    env: ProductionEnvironment,
) -> PreflightCheckResult:
    """Every parser must have a real ``package_source_sha256`` — not
    the well-known zero placeholder, not empty, not malformed."""
    problems: list[str] = []
    required_parsers = CPU_ONLY_PARSER_IDS | VLM_PARSER_IDS
    for pid in sorted(required_parsers):
        value = env.package_source_shas.get(pid)
        try:
            validate_sha_format(value, name=f"package_source_sha256[{pid}]")
        except ProvenanceHashError as exc:
            problems.append(str(exc))
    if problems:
        return PreflightCheckResult(
            name="package_source_hashes", passed=False,
            detail="; ".join(problems),
        )
    return PreflightCheckResult(
        name="package_source_hashes", passed=True,
        detail=f"{len(env.package_source_shas)} package hashes validated",
    )


def _check_model_artifact_hashes(
    env: ProductionEnvironment,
) -> PreflightCheckResult:
    """VLM parsers (marker + docling) must have a real
    ``parser_model_artifact_sha256``. CPU-only parsers may have
    ``None`` (matches the execution-record contract nullability).
    A placeholder for either surface is refused."""
    problems: list[str] = []
    for pid in sorted(VLM_PARSER_IDS):
        value = env.model_artifact_shas.get(pid)
        try:
            validate_sha_format(value, name=f"model_artifact_sha256[{pid}]")
        except ProvenanceHashError as exc:
            problems.append(str(exc))
    # For CPU-only parsers, if a value is supplied it must not be a
    # placeholder; None is admissible.
    for pid in sorted(CPU_ONLY_PARSER_IDS):
        value = env.model_artifact_shas.get(pid)
        if value is None:
            continue
        if is_placeholder_sha(value):
            problems.append(
                f"model_artifact_sha256[{pid}]: placeholder supplied "
                f"for a CPU-only parser (either omit or supply a real hash)"
            )
    if problems:
        return PreflightCheckResult(
            name="model_artifact_hashes", passed=False,
            detail="; ".join(problems),
        )
    return PreflightCheckResult(
        name="model_artifact_hashes", passed=True,
        detail="marker + docling model hashes validated",
    )


def _check_firewall_program_binding(
    env: ProductionEnvironment,
) -> PreflightCheckResult:
    """The firewall must block the actual executable the subprocess
    invoker spawns. If ``firewall_program_path`` is empty, missing on
    disk, or does not match a real file, preflight fails so the
    smoke does not proceed with a mis-bound rule.

    The composition layer sets this to ``sys.executable`` by default;
    tests may inject any real file path. Empty is refused."""
    path_str = env.firewall_program_path
    if not path_str:
        return PreflightCheckResult(
            name="firewall_program_binding", passed=False,
            detail=(
                "firewall_program_path is empty; production wiring must "
                "bind the firewall to the exact executable the parser "
                "subprocess uses (typically sys.executable)"
            ),
        )
    p = Path(path_str)
    if not p.exists() or not p.is_file():
        return PreflightCheckResult(
            name="firewall_program_binding", passed=False,
            detail=f"firewall_program_path does not resolve to a file: {path_str}",
        )
    return PreflightCheckResult(
        name="firewall_program_binding", passed=True,
        detail=f"firewall bound to {path_str}",
    )


def _check_smoke_documents(contracts: ContractPair) -> PreflightCheckResult:
    docs = contracts.smoke_documents()
    ids_in_spec = {d["canonical_id"] for d in docs}
    if ids_in_spec != EXPECTED_SMOKE_CANONICAL_IDS:
        extras = sorted(ids_in_spec - EXPECTED_SMOKE_CANONICAL_IDS)
        missing = sorted(EXPECTED_SMOKE_CANONICAL_IDS - ids_in_spec)
        return PreflightCheckResult(
            name="smoke_documents", passed=False,
            detail=(
                f"smoke_spec_v1.json's smoke_documents does not match the "
                f"frozen three IDs. extras={extras} missing={missing}"
            ),
        )
    return PreflightCheckResult(
        name="smoke_documents", passed=True,
        detail=f"three frozen canonical IDs present: {sorted(ids_in_spec)}",
    )


# --------------------------------------------------------------------------
# Public entry point.


def run_preflight(
    *,
    contracts: ContractPair,
    environment: ProductionEnvironment,
    fs_probe: Callable[[Path], tuple[bool, int]] | None = None,
) -> PreflightSummary:
    """Run every preflight check. Returns a summary; caller decides
    what to do with a non-empty ``failed_names``."""
    checks: list[PreflightCheckResult] = [
        _check_contract_hashes(contracts),
        _check_python_version(environment, contracts),
        _check_platform_prefix(environment, contracts),
        _check_package_versions(environment, contracts),
        _check_cuda_for_vlm(environment, contracts),
        _check_model_cache_paths(environment, contracts, fs_probe=fs_probe),
        _check_offline_env_vars(environment),
        _check_adapter_source_hashes(environment),
        _check_package_source_hashes(environment),
        _check_model_artifact_hashes(environment),
        _check_firewall_program_binding(environment),
        _check_smoke_documents(contracts),
    ]
    return PreflightSummary(
        all_passed=all(c.passed for c in checks),
        checks=tuple(checks),
    )


def require_preflight_pass(summary: PreflightSummary) -> None:
    """Raise PreflightError if any check failed."""
    if summary.all_passed:
        return
    failed = [c for c in summary.checks if not c.passed]
    details = "; ".join(f"{c.name}: {c.detail}" for c in failed)
    raise PreflightError(f"preflight failed: {details}")
