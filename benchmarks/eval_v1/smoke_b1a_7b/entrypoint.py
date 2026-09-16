"""Explicit CLI entry point for the 12-run smoke execution.

The entry point refuses to execute unless **all three** of the
following are satisfied:

1. The environment sentinel ``AKSHARAMD_SMOKE_AUTHORIZED`` equals the
   token ``"b1a_7b_2b_authorized"``.
2. The command line includes ``--execute-frozen-smoke`` — no default
   execution shape; running the module without this flag prints usage
   and exits.
3. ``--run-dir <path>`` points to a directory that exists and is empty.
   The runner refuses to overwrite prior smoke output.

Document IDs are loaded from
``benchmarks/eval_v1/config/smoke_spec_v1.json`` and are never
accepted from the command line. The frozen three IDs cannot be
overridden by the operator.

Console output during a run is operational-only: pair number,
blinded pair_id, parser_id, exit_status, wall_clock_seconds,
defect_reason, phase. No markdown excerpts, AksharaMD scores,
warning codes, ground-truth comparisons, or parser-quality summaries
are ever printed.

The main body's real backends are guarded by
``_check_execute_mode_enabled`` and never touched by the synthetic
tests in this PR. Real coverage happens at real-smoke authorization.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

AUTHORIZATION_ENV_VAR = "AKSHARAMD_SMOKE_AUTHORIZED"
AUTHORIZATION_TOKEN = "b1a_7b_2b_authorized"

EXECUTE_FLAG = "--execute-frozen-smoke"


class UnauthorizedInvocationError(RuntimeError):
    """The entry point was invoked without the explicit authorization
    sentinel or without the required CLI flag."""


class RunDirectoryError(RuntimeError):
    """--run-dir does not point to a directory that is safe to write
    into. Directory must exist and be empty."""


class InvalidCliArgumentsError(RuntimeError):
    """The operator passed an unknown flag, tried to supply document
    IDs (which are contract-pinned, not operator-configurable), or
    otherwise violated the CLI contract."""


@dataclass(frozen=True)
class ParsedCliArgs:
    execute_frozen_smoke: bool
    run_dir: Path | None


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smoke-b1a-7b-2",
        description=(
            "Execute the frozen B1a-7b.2 infrastructure smoke. "
            "This program refuses to run without the "
            "AKSHARAMD_SMOKE_AUTHORIZED environment sentinel and the "
            "--execute-frozen-smoke flag. Document IDs are loaded "
            "from the merged smoke-spec config only; they cannot be "
            "supplied on the command line."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        EXECUTE_FLAG,
        dest="execute_frozen_smoke",
        action="store_true",
        help=(
            "Enable frozen-smoke execution. Required. Without this flag "
            "the program prints usage and exits with a non-zero status."
        ),
    )
    parser.add_argument(
        "--run-dir",
        dest="run_dir",
        type=Path,
        default=None,
        help=(
            "Directory that will receive smoke output. Must exist and "
            "be empty. Required when --execute-frozen-smoke is passed."
        ),
    )
    return parser


def parse_cli(argv: list[str]) -> ParsedCliArgs:
    """Parse CLI without executing anything. Raises
    ``InvalidCliArgumentsError`` on unknown flags."""
    parser = _build_argparser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits on -h / --help / unknown flags; convert unknown
        # to a clean exception so callers (and tests) can distinguish.
        raise InvalidCliArgumentsError(str(exc)) from exc
    return ParsedCliArgs(
        execute_frozen_smoke=bool(namespace.execute_frozen_smoke),
        run_dir=namespace.run_dir,
    )


def _check_authorization() -> None:
    token = os.environ.get(AUTHORIZATION_ENV_VAR, "")
    if token != AUTHORIZATION_TOKEN:
        raise UnauthorizedInvocationError(
            f"Entry point requires {AUTHORIZATION_ENV_VAR}="
            f"{AUTHORIZATION_TOKEN!r}. The 12-run smoke is a separate "
            f"authorization step and was not authorized by this PR."
        )


def _check_execute_flag(args: ParsedCliArgs) -> None:
    if not args.execute_frozen_smoke:
        raise UnauthorizedInvocationError(
            f"Entry point requires the explicit {EXECUTE_FLAG} flag. "
            f"Running without it prints usage; running with it triggers "
            f"the frozen smoke."
        )


def _check_run_dir(args: ParsedCliArgs) -> Path:
    if args.run_dir is None:
        raise RunDirectoryError(
            "--run-dir is required when --execute-frozen-smoke is passed."
        )
    p = args.run_dir
    if not p.exists():
        raise RunDirectoryError(
            f"--run-dir {p} does not exist. The operator must create the "
            f"directory before invoking the smoke; the harness refuses "
            f"to create output directories on the operator's behalf."
        )
    if not p.is_dir():
        raise RunDirectoryError(f"--run-dir {p} is not a directory.")
    if any(p.iterdir()):
        raise RunDirectoryError(
            f"--run-dir {p} is not empty. The harness refuses to overwrite "
            f"prior smoke output; supply a fresh empty directory."
        )
    return p


def check_execute_mode_enabled(argv: list[str]) -> Path:
    """Perform all three admission checks. Returns the validated
    run-dir path on success; raises the corresponding error on any
    admission-check failure.

    This function is the single choke point every real-backend code
    path passes through before touching the network / firewall / any
    parser package.
    """
    _check_authorization()
    args = parse_cli(argv)
    _check_execute_flag(args)
    return _check_run_dir(args)


def print_usage_to_stderr() -> None:
    _build_argparser().print_usage(sys.stderr)


# --------------------------------------------------------------------------
# Operational-only console output.


def emit_operational(**fields: object) -> None:  # pragma: no cover
    """Emit one operational-only line to stdout.

    Allowed fields: ``phase``, ``pair_number``, ``pair_id``,
    ``parser_id``, ``exit_status``, ``wall_clock_seconds``,
    ``defect_reason``, ``harness_state``. Passing any other key raises
    to force operator-visible surface to stay operational.
    """
    allowed = {
        "phase",
        "pair_number",
        "pair_id",
        "parser_id",
        "exit_status",
        "wall_clock_seconds",
        "defect_reason",
        "harness_state",
        "positive_control_pass",
        "network_egress_blocked",
    }
    disallowed = set(fields) - allowed
    if disallowed:
        raise ValueError(
            f"emit_operational refuses non-operational fields: {sorted(disallowed)}"
        )
    parts = " ".join(f"{k}={v!r}" for k, v in fields.items())
    sys.stdout.write(parts + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------
# main() — real-backend wiring behind the admission checks. Not
# covered by the synthetic tests in this PR. The admission checks
# themselves ARE tested.


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    """Real-backend orchestration behind the admission-check choke
    point. ``# pragma: no cover``: exercised only when the reviewer
    authorizes real execution and the operator supplies both the env
    sentinel and the CLI flag.

    All construction goes through
    ``production_composition.build_production_context``; the same
    decision logic is exercised in tests against a fake
    ``ProductionSmokeContext`` via ``compose_and_run``.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        run_dir = check_execute_mode_enabled(argv)
    except UnauthorizedInvocationError as exc:
        sys.stderr.write(f"unauthorized: {exc}\n")
        print_usage_to_stderr()
        return 2
    except RunDirectoryError as exc:
        sys.stderr.write(f"run-dir invalid: {exc}\n")
        return 3
    except InvalidCliArgumentsError as exc:
        sys.stderr.write(f"cli-invalid: {exc}\n")
        return 4

    from .production_composition import (
        PreflightError,
        compose_and_run,
        result_to_exit_code,
    )

    try:
        context = _build_real_production_context(run_dir=run_dir)
    except PreflightError as exc:
        emit_operational(phase="preflight_failed", harness_state=str(exc))
        return 7

    emit_operational(phase="smoke_started", pair_number=0)
    result = compose_and_run(context)
    for i, defect in enumerate(result.parser_defects, start=1):
        emit_operational(
            phase="parser_defect_observed",
            pair_number=i,
            parser_id=str(defect.get("parser_id")),
            defect_reason=str(defect.get("defect_reason")),
        )
    emit_operational(
        phase="smoke_finished",
        exit_status=str(result.outcome),
        harness_state=str(result.reason),
    )
    return result_to_exit_code(result)


def _build_real_production_context(*, run_dir):  # pragma: no cover
    """Construct the production ``ProductionSmokeContext``.

    Reads environment variables to determine payload root and cache
    locations; nothing beyond ``--run-dir`` is user-configurable via
    CLI. Document IDs are loaded from the merged smoke spec by
    ``build_smoke_documents_from_spec`` inside the composition.
    """
    import hashlib
    import importlib.metadata as im
    import os
    from pathlib import Path

    from .production_composition import build_production_context

    payload_root = Path(
        os.environ.get("AKSHARAMD_SMOKE_PAYLOAD_ROOT", "")
    ).resolve()

    class _DiskPayloadResolver:
        """Reads each smoke payload from
        ``<payload_root>/<canonical_id>.pdf``. The operator is
        responsible for pre-caching the three payloads under this
        layout. The resolver refuses to read outside of ``payload_root``.
        """

        def pdf_bytes(self, canonical_id: str) -> bytes:
            candidate = (payload_root / f"{canonical_id}.pdf").resolve()
            if payload_root not in candidate.parents and candidate != payload_root:
                raise RuntimeError(
                    f"resolved payload path escapes root: {candidate}"
                )
            return candidate.read_bytes()

    adapters_dir = Path(__file__).resolve().parent
    adapter_source_bytes: dict[str, bytes] = {}
    for pid, filename in {
        "aksharamd-reference": "workers/main.py",
        "marker": "real_adapters.py",
        "docling": "real_adapters.py",
        "markitdown": "real_adapters.py",
    }.items():
        adapter_source_bytes[pid] = (adapters_dir / filename).read_bytes()
    adapter_source_shas = {
        pid: hashlib.sha256(b).hexdigest()
        for pid, b in adapter_source_bytes.items()
    }

    package_source_shas = {pid: "0" * 64 for pid in adapter_source_bytes}
    model_artifact_shas: dict[str, str | None] = {
        "aksharamd-reference": None,
        "marker": "0" * 64,
        "docling": "0" * 64,
        "markitdown": None,
    }

    package_versions: dict[str, str] = {}
    for pkg in ("aksharamd", "marker-pdf", "docling", "markitdown",
                "pymupdf", "pymupdf4llm", "torch"):
        try:
            package_versions[pkg] = im.version(pkg)
        except im.PackageNotFoundError:
            package_versions[pkg] = "missing"

    model_cache_paths: dict[str, Path] = {
        "marker": Path(os.environ.get("MARKER_MODELS_CACHE", "")).resolve(),
        "docling": Path(os.environ.get("DOCLING_ARTIFACTS_CACHE", "")).resolve(),
    }

    return build_production_context(
        run_dir=run_dir,
        payload_resolver=_DiskPayloadResolver(),
        program_path_for_firewall=os.environ.get(
            "AKSHARAMD_SMOKE_PARSER_WORKER_PROGRAM", ""
        ),
        package_source_shas=package_source_shas,
        adapter_source_shas=adapter_source_shas,
        model_artifact_shas=model_artifact_shas,
        offline_env={
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DOCLING_ARTIFACTS_OFFLINE": "1",
        },
        package_versions=package_versions,
        model_cache_paths=model_cache_paths,
        env_vars=dict(os.environ),
        adapter_source_bytes=adapter_source_bytes,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
