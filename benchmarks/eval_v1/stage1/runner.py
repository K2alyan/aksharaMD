"""Stage 1 parser runner (B1a-9).

Orchestrates execution of the four frozen parsers across a list of
``CorpusItem`` objects (olmOCR-Bench PDFs or DocLayNet pages), writes
one ``Stage1ExecutionRecord`` per (item, parser) invocation, and
returns a ``Stage1RunSummary``.

Reuses the smoke_b1a_7b subprocess adapter and firewall/probe
infrastructure without modification.  The only Stage 1-specific
additions are:

- Corpus items come from selection manifests, not the smoke spec.
- Execution records carry ``stage1_execution_manifest_sha256`` (the
  raw-byte SHA-256 of STAGE1_EXECUTION_MANIFEST.json) instead of
  ``smoke_spec_config_sha256``.
- The run directory is taken from ``run_roots`` in the manifest.

Fail-closed at record level: a record that fails schema validation
is NOT written; the pair is instead recorded as a harness-level
defect in the summary.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import ParseStatus
from benchmarks.eval_v1.smoke_b1a_7b.real_adapters import (
    RealAdapterConfig,
    SubprocessParserAdapter,
)
from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import (
    RealSubprocessInvoker,
    SubprocessInvoker,
)

from .record import (
    Stage1ExecutionRecord,
    Stage1RecordSchemaError,
    compute_pair_id,
    write_execution_record,
)

# ---------------------------------------------------------------------------
# Contract-level constants (mirror smoke_b1a_7b values; must not drift).

PARSER_EXECUTION_CONTRACT_VERSION = "v1"
NORMALIZATION_VERSION = "2"

# Canonical-LF SHA-256 of benchmarks/eval_v1/config/parser_execution_contract_v1.json
CONTRACT_CONFIG_SHA256 = (
    "a0ca496e562cee200c393c146c7ed16efee9b01ee2f94ebdd4e623c936f9baa4"
)

WORKER_ARGV_PREFIX = [
    "python", "-m", "benchmarks.eval_v1.smoke_b1a_7b.workers.main",
]

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "DOCLING_ARTIFACTS_OFFLINE": "1",
}

# Package-source SHA-256 placeholder (populated by preflight in production;
# uses "pending" sentinel per parser-execution contract §env.package_source_sha256_pending).
_PENDING_SHA = "0" * 64


# ---------------------------------------------------------------------------
# Data structures.


@dataclass(frozen=True)
class CorpusItem:
    canonical_id: str
    corpus: str
    pdf_bytes_source: Callable[[], bytes]


@dataclass
class PairResult:
    canonical_id: str
    parser_id: str
    pair_id: str
    exit_status: str          # EXECUTED | DEFECT | HARNESS_DEFECT
    wall_clock_seconds: float
    record_path: Path | None  # None on harness-level defect
    defect_reason: str | None = None
    harness_detail: str | None = None


@dataclass
class Stage1RunSummary:
    run_dir: Path
    n_items: int
    n_parsers: int
    n_invocations: int
    n_executed: int
    n_defect: int
    n_harness_defect: int
    started_at: str
    finished_at: str
    wall_clock_seconds: float
    pairs: list[PairResult] = field(default_factory=list)

    @property
    def structurally_valid(self) -> bool:
        return self.n_harness_defect == 0 and self.n_invocations == (
            self.n_items * self.n_parsers
        )


# ---------------------------------------------------------------------------
# Environment snapshot (minimal — mirrors smoke_b1a_7b.environment).


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5
            )
            .decode("ascii")
            .strip()
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


def _cuda_state() -> tuple[str | None, str | None, str | None]:
    try:
        import torch
    except ImportError:
        return None, None, None
    if not torch.cuda.is_available():
        return None, None, None
    return torch.version.cuda, torch.version.cuda, torch.cuda.get_device_name(0)


def _cpu_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=False) or 1
    except ImportError:
        return os.cpu_count() or 1


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Adapter construction (one per parser; reuses smoke adapter config).


def build_adapters(
    *,
    subprocess_invoker: SubprocessInvoker,
    model_artifact_shas: dict[str, str | None],
) -> dict[str, SubprocessParserAdapter]:
    """Construct one adapter per parser.

    ``model_artifact_shas`` must supply real (non-placeholder) SHA-256
    strings for VLM parsers (marker, docling); None is accepted for
    CPU-only parsers.  The values come from the frozen execution manifest.
    """
    configs = [
        RealAdapterConfig(
            parser_id="aksharamd-reference",
            package_version="0.3.6",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version=None,
            parser_model_artifact_sha256=None,
            timeout_seconds=120.0,
            is_vlm=False,
        ),
        RealAdapterConfig(
            parser_id="marker",
            package_version="1.10.2",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version="runtime",
            parser_model_artifact_sha256=model_artifact_shas["marker"],
            timeout_seconds=600.0,
            is_vlm=True,
        ),
        RealAdapterConfig(
            parser_id="docling",
            package_version="2.107.0",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version="runtime",
            parser_model_artifact_sha256=model_artifact_shas["docling"],
            timeout_seconds=600.0,
            is_vlm=True,
        ),
        RealAdapterConfig(
            parser_id="markitdown",
            package_version="0.1.6",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version=None,
            parser_model_artifact_sha256=None,
            timeout_seconds=120.0,
            is_vlm=False,
        ),
    ]
    return {
        cfg.parser_id: SubprocessParserAdapter(
            config=cfg,
            subprocess_invoker=subprocess_invoker,
            worker_argv_prefix=WORKER_ARGV_PREFIX,
            offline_env=OFFLINE_ENV,
        )
        for cfg in configs
    }


# ---------------------------------------------------------------------------
# Record builder.


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_record(
    *,
    canonical_id: str,
    corpus: str,
    parser_id: str,
    adapter: SubprocessParserAdapter,
    outcome: Any,
    started_at: str,
    finished_at: str,
    wall_clock_seconds: float,
    cpu_user: float,
    cpu_system: float,
    peak_rss: int,
    peak_vram: int | None,
    cuda_events: int | None,
    network_egress_blocked: bool,
    git_commit: str,
    python_version: str,
    platform_string: str,
    cpu_cores: int,
    cuda_version: str | None,
    cuda_device: str | None,
    model_cache_path: str | None,
    stage1_manifest_sha256: str,
) -> Stage1ExecutionRecord:

    is_executed = outcome.status is ParseStatus.EXECUTED
    output_bytes = outcome.markdown.encode("utf-8") if (
        is_executed and outcome.markdown
    ) else b""
    stdout_bytes = (outcome.stdout or "").encode("utf-8")
    stderr_bytes = (outcome.stderr or "").encode("utf-8")

    is_cpu_only = parser_id in {"aksharamd-reference", "markitdown"}

    return Stage1ExecutionRecord(
        pair_id=compute_pair_id(canonical_id=canonical_id, parser_id=parser_id),
        canonical_id=canonical_id,
        corpus=corpus,
        parser_id=parser_id,
        parser_package_version=adapter.package_version(),
        parser_package_source_sha256=adapter.package_source_sha256(),
        parser_model_version=adapter.parser_model_version(),
        parser_model_artifact_sha256=adapter.parser_model_artifact_sha256(),
        adapter_source_sha256=adapter.adapter_source_sha256(),
        python_version=python_version,
        platform_string=platform_string,
        git_commit=git_commit,
        cpu_physical_cores=cpu_cores,
        cuda_version=None if is_cpu_only else cuda_version,
        cuda_driver_version=None if is_cpu_only else cuda_version,
        cuda_device_name=None if is_cpu_only else cuda_device,
        model_cache_path=None if is_cpu_only else model_cache_path,
        network_egress_blocked=network_egress_blocked,
        pair_started_at=started_at,
        pair_finished_at=finished_at,
        wall_clock_seconds=wall_clock_seconds,
        cpu_seconds_user=cpu_user,
        cpu_seconds_system=cpu_system,
        peak_rss_bytes=peak_rss,
        peak_vram_bytes=None if is_cpu_only else peak_vram,
        cuda_events=None if is_cpu_only else cuda_events,
        output_bytes=len(output_bytes),
        output_sha256=_sha256_hex(output_bytes),
        stdout_bytes=len(stdout_bytes),
        stdout_sha256=_sha256_hex(stdout_bytes),
        stderr_bytes=len(stderr_bytes),
        stderr_sha256=_sha256_hex(stderr_bytes),
        exit_status="EXECUTED" if is_executed else "DEFECT",
        defect_reason=None if is_executed else outcome.defect_reason,
        normalization_version=NORMALIZATION_VERSION,
        parser_execution_contract_version=PARSER_EXECUTION_CONTRACT_VERSION,
        parser_execution_contract_config_sha256=CONTRACT_CONFIG_SHA256,
        stage1_execution_manifest_sha256=stage1_manifest_sha256,
    )


# ---------------------------------------------------------------------------
# Runner.


class Stage1Runner:
    """Runs one (corpus_item × parser) pair at a time; writes a
    Stage1ExecutionRecord for each.  Caller is responsible for
    firewall setup/teardown around ``run()``."""

    PARSER_ORDER = ["aksharamd-reference", "marker", "docling", "markitdown"]

    def __init__(
        self,
        *,
        items: list[CorpusItem],
        run_dir: Path,
        stage1_manifest_sha256: str,
        model_artifact_shas: dict[str, str | None],
        model_cache_paths: dict[str, str],
        network_egress_blocked: bool = False,
        subprocess_invoker: SubprocessInvoker | None = None,
        verbose: bool = True,
        resume: bool = False,
    ) -> None:
        self._items = items
        self._run_dir = run_dir
        self._manifest_sha = stage1_manifest_sha256
        self._model_artifact_shas = model_artifact_shas
        self._model_cache_paths = model_cache_paths
        self._blocked = network_egress_blocked
        self._invoker = subprocess_invoker or RealSubprocessInvoker()
        self._verbose = verbose
        self._resume = resume
        self._adapters = build_adapters(
            subprocess_invoker=self._invoker,
            model_artifact_shas=model_artifact_shas,
        )

        # Environment snapshot (captured once at construction).
        self._git_commit = _git_commit()
        self._python_version = sys.version.split()[0]
        self._platform_string = platform.platform()
        self._cpu_cores = _cpu_cores()
        self._cuda_version, _, self._cuda_device = _cuda_state()

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(msg, flush=True)

    def _validate_resume_record(
        self,
        path: Path,
        *,
        canonical_id: str,
        parser_id: str,
    ) -> tuple[bool, str]:
        """Return (skip, reason).  skip=True means the record is valid to reuse.

        Validates:
          1. File is parseable JSON
          2. canonical_id and parser_id match this invocation
          3. stage1_execution_manifest_sha256 matches the pinned manifest SHA
          4. exit_status is a terminal study state (EXECUTED or DEFECT)
        """
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return False, f"parse error: {exc}"

        if d.get("canonical_id") != canonical_id:
            return False, (
                f"canonical_id mismatch: "
                f"record={d.get('canonical_id')!r} != expected={canonical_id!r}"
            )
        if d.get("parser_id") != parser_id:
            return False, (
                f"parser_id mismatch: "
                f"record={d.get('parser_id')!r} != expected={parser_id!r}"
            )
        if d.get("stage1_execution_manifest_sha256") != self._manifest_sha:
            return False, (
                f"manifest SHA mismatch: "
                f"record={str(d.get('stage1_execution_manifest_sha256'))[:16]}… "
                f"!= pinned={self._manifest_sha[:16]}…"
            )
        status = d.get("exit_status", "")
        if status not in {"EXECUTED", "DEFECT"}:
            return False, f"non-terminal exit_status={status!r}"

        return True, ""

    def run(self) -> Stage1RunSummary:
        started_at = _now_utc()
        t0_wall = time.monotonic()
        pairs: list[PairResult] = []

        n_items = len(self._items)
        n_parsers = len(self.PARSER_ORDER)
        total = n_items * n_parsers
        done = 0

        for item in self._items:
            try:
                pdf_bytes = item.pdf_bytes_source()
            except Exception as exc:  # noqa: BLE001
                for pid in self.PARSER_ORDER:
                    pairs.append(PairResult(
                        canonical_id=item.canonical_id,
                        parser_id=pid,
                        pair_id=compute_pair_id(
                            canonical_id=item.canonical_id, parser_id=pid
                        ),
                        exit_status="HARNESS_DEFECT",
                        wall_clock_seconds=0.0,
                        record_path=None,
                        harness_detail=f"pdf_bytes_source failed: {exc}",
                    ))
                    done += 1
                self._log(
                    f"  [HARNESS_DEFECT] {item.canonical_id}: "
                    f"pdf_bytes_source failed: {exc}"
                )
                continue

            for parser_id in self.PARSER_ORDER:
                done += 1
                adapter = self._adapters[parser_id]
                pair_id = compute_pair_id(
                    canonical_id=item.canonical_id, parser_id=parser_id
                )

                # Resume: skip only if the existing record passes full
                # frozen-identity validation.  Checks:
                #   1. canonical_id and parser_id match this invocation
                #   2. stage1_execution_manifest_sha256 matches pinned SHA
                #   3. exit_status is a terminal study state (not HARNESS_DEFECT)
                # Any mismatch or parse error causes re-execution.
                record_path_candidate = (
                    self._run_dir
                    / item.corpus
                    / item.canonical_id
                    / parser_id
                    / "execution_record.json"
                )
                if self._resume and record_path_candidate.exists():
                    skip, skip_reason = self._validate_resume_record(
                        record_path_candidate,
                        canonical_id=item.canonical_id,
                        parser_id=parser_id,
                    )
                    if skip:
                        existing = json.loads(
                            record_path_candidate.read_text(encoding="utf-8")
                        )
                        self._log(
                            f"  [{done}/{total}] SKIP (resume) "
                            f"{item.canonical_id[:24]}… × {parser_id} "
                            f"[{existing.get('exit_status','?')}]"
                        )
                        pairs.append(PairResult(
                            canonical_id=item.canonical_id,
                            parser_id=parser_id,
                            pair_id=pair_id,
                            exit_status=existing["exit_status"],
                            wall_clock_seconds=existing.get("wall_clock_seconds", 0.0),
                            record_path=record_path_candidate,
                            defect_reason=existing.get("defect_reason"),
                        ))
                        continue
                    else:
                        self._log(
                            f"  [{done}/{total}] RE-RUN (resume invalid: {skip_reason}) "
                            f"{item.canonical_id[:24]}… × {parser_id}"
                        )

                self._log(
                    f"  [{done}/{total}] {item.canonical_id[:24]}…"
                    f" × {parser_id}"
                )

                t_pair_start = time.monotonic()
                started = _now_utc()

                try:
                    import psutil
                    proc = psutil.Process()
                    cpu_times_before = proc.cpu_times()
                    rss_before = proc.memory_info().rss
                except Exception:  # noqa: BLE001
                    proc = None
                    cpu_times_before = None
                    rss_before = 0

                outcome = adapter.compile(pdf_bytes, item.canonical_id)

                t_pair_end = time.monotonic()
                finished = _now_utc()
                wall_secs = t_pair_end - t_pair_start

                cpu_user, cpu_sys, peak_rss = 0.0, 0.0, 0
                if proc is not None and cpu_times_before is not None:
                    try:
                        cpu_times_after = proc.cpu_times()
                        cpu_user = max(
                            0.0,
                            cpu_times_after.user - cpu_times_before.user,
                        )
                        cpu_sys = max(
                            0.0,
                            cpu_times_after.system - cpu_times_before.system,
                        )
                        peak_rss = max(
                            proc.memory_info().rss - rss_before, 0
                        )
                    except Exception:  # noqa: BLE001
                        pass

                is_vlm = parser_id in {"marker", "docling"}
                peak_vram: int | None = None
                if is_vlm:
                    try:
                        import torch
                        if torch.cuda.is_available():
                            peak_vram = torch.cuda.max_memory_allocated()
                            torch.cuda.reset_peak_memory_stats()
                    except Exception:  # noqa: BLE001
                        pass

                try:
                    record = _build_record(
                        canonical_id=item.canonical_id,
                        corpus=item.corpus,
                        parser_id=parser_id,
                        adapter=adapter,
                        outcome=outcome,
                        started_at=started,
                        finished_at=finished,
                        wall_clock_seconds=wall_secs,
                        cpu_user=cpu_user,
                        cpu_system=cpu_sys,
                        peak_rss=peak_rss,
                        peak_vram=peak_vram,
                        cuda_events=0 if is_vlm else None,
                        network_egress_blocked=self._blocked,
                        git_commit=self._git_commit,
                        python_version=self._python_version,
                        platform_string=self._platform_string,
                        cpu_cores=self._cpu_cores,
                        cuda_version=self._cuda_version,
                        cuda_device=self._cuda_device,
                        model_cache_path=self._model_cache_paths.get(parser_id),
                        stage1_manifest_sha256=self._manifest_sha,
                    )
                    record_path = (
                        self._run_dir
                        / item.corpus
                        / item.canonical_id
                        / parser_id
                        / "execution_record.json"
                    )
                    write_execution_record(record_path, record)
                    status_str = record.exit_status
                    self._log(
                        f"    -> {status_str}"
                        + (f" ({record.defect_reason})" if record.defect_reason else "")
                        + f" ({wall_secs:.1f}s)"
                    )
                    pairs.append(PairResult(
                        canonical_id=item.canonical_id,
                        parser_id=parser_id,
                        pair_id=pair_id,
                        exit_status=status_str,
                        wall_clock_seconds=wall_secs,
                        record_path=record_path,
                        defect_reason=record.defect_reason,
                    ))
                except (Stage1RecordSchemaError, Exception) as exc:  # noqa: BLE001
                    self._log(f"    -> HARNESS_DEFECT (schema): {exc}")
                    pairs.append(PairResult(
                        canonical_id=item.canonical_id,
                        parser_id=parser_id,
                        pair_id=pair_id,
                        exit_status="HARNESS_DEFECT",
                        wall_clock_seconds=wall_secs,
                        record_path=None,
                        harness_detail=str(exc),
                    ))

        finished_at = _now_utc()
        total_wall = time.monotonic() - t0_wall

        n_executed = sum(1 for p in pairs if p.exit_status == "EXECUTED")
        n_defect = sum(1 for p in pairs if p.exit_status == "DEFECT")
        n_harness = sum(1 for p in pairs if p.exit_status == "HARNESS_DEFECT")

        return Stage1RunSummary(
            run_dir=self._run_dir,
            n_items=n_items,
            n_parsers=n_parsers,
            n_invocations=len(pairs),
            n_executed=n_executed,
            n_defect=n_defect,
            n_harness_defect=n_harness,
            started_at=started_at,
            finished_at=finished_at,
            wall_clock_seconds=total_wall,
            pairs=pairs,
        )
