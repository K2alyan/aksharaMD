"""Smoke orchestrator.

Coordinates: contracts load -> environment capture -> firewall rule
setup + positive control -> per-invocation (probe -> parser call ->
timing -> record write -> normalization -> reviewer artifact) ->
positive control -> firewall cleanup -> PASS/FAIL/INCONCLUSIVE.

DEFECT semantics (spec §7):
- Parser-level DEFECT (timeout/exception/oom/cuda_unavailable/native
  crash): record the outcome, continue.
- Harness/environment-level DEFECT (any listed condition, including
  network_egress_blocked=false on any invocation): halt, mark FAIL.
- INCONCLUSIVE reserved for positive-control failures only.

Every runtime dependency is injectable so tests exercise the runner
end-to-end against fakes.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .adapter_protocol import ParseOutcome, ParserAdapterProtocol, ParseStatus
from .contracts import ContractPair
from .environment import EnvironmentDriftError, EnvironmentSnapshot, verify_no_drift
from .execution_record import (
    CPU_ONLY_PARSERS,
    ExecutionRecord,
    ExecutionRecordSchemaError,
    compute_pair_id,
    write_execution_record,
)
from .firewall import (
    FirewallBackend,
    FirewallRuleError,
    FirewallRuleManager,
    NetworkEgressObservation,
    ProbeBackend,
    run_probes,
)
from .normalize import NORMALIZATION_VERSION, normalize
from .resources import (
    InvocationTimer,
    NoopVramSampler,
    ResourceSampler,
    VramSampler,
)
from .reviewer_artifact import build_smoke_reviewer_artifact
from .stdio_capture import write_stderr, write_stdout


class SmokeOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class SmokeResult:
    outcome: SmokeOutcome
    reason: str
    pairs_attempted: int
    pairs_recorded: int
    parser_defects: list[dict[str, Any]] = field(default_factory=list)
    harness_defect: dict[str, Any] | None = None
    positive_control_pre: dict[str, Any] | None = None
    positive_control_post: dict[str, Any] | None = None
    smoke_started_at_utc: str = ""
    smoke_finished_at_utc: str = ""


@dataclass(frozen=True)
class SmokeDocument:
    """Frozen smoke-document reference. The runner does NOT open the
    file for content inspection; it only reads bytes to hand to
    parser adapters. In tests, ``pdf_bytes_source`` is a synthetic
    callable returning canned bytes rather than a real file read."""

    canonical_id: str
    corpus: str
    pdf_bytes_source: Callable[[], bytes]


@dataclass(frozen=True)
class SmokeAdapters:
    """The four parser adapters in a fixed order.

    Order matters only for run reproducibility; the smoke does not
    treat any parser as primary.
    """

    aksharamd_reference: ParserAdapterProtocol
    marker: ParserAdapterProtocol
    docling: ParserAdapterProtocol
    markitdown: ParserAdapterProtocol

    def iter_ordered(self) -> list[ParserAdapterProtocol]:
        return [
            self.aksharamd_reference,
            self.marker,
            self.docling,
            self.markitdown,
        ]


@dataclass(frozen=True)
class PositiveControlProbe:
    """Runs the same two probes as run_probes but against a probe
    backend that is NOT under the firewall rule — i.e., an unblocked
    process. Injected as a callable so tests substitute a fake."""

    run: Callable[[], NetworkEgressObservation]


class SmokeRunner:
    """The one entry point. All dependencies injected for testability.
    Nothing here reads a V2 payload, invokes a real parser, or hits
    the OS network / firewall by default."""

    def __init__(
        self,
        *,
        contracts: ContractPair,
        environment: EnvironmentSnapshot,
        smoke_documents: list[SmokeDocument],
        adapters: SmokeAdapters,
        probe_backend: ProbeBackend,
        firewall_backend: FirewallBackend,
        positive_control: PositiveControlProbe,
        run_dir: Path,
        program_path_for_firewall: str,
        resource_sampler_factory: Callable[[], ResourceSampler],
        vram_sampler_factory: Callable[[str], VramSampler] | None = None,
    ) -> None:
        self._c = contracts
        self._env = environment
        self._docs = list(smoke_documents)
        self._adapters = adapters
        self._probe = probe_backend
        self._fw = FirewallRuleManager(firewall_backend)
        self._program_path = program_path_for_firewall
        self._pc = positive_control
        self._run_dir = run_dir
        self._run_id = uuid.uuid4().hex
        self._resource_sampler_factory = resource_sampler_factory
        self._vram_sampler_factory = vram_sampler_factory or (
            lambda parser_id: NoopVramSampler()
        )

    # ------------------------------------------------------------------
    # Public entry point.

    def run(self) -> SmokeResult:
        started = datetime.now(UTC).isoformat()

        # 1) Verify environment does not drift from pinned.
        try:
            verify_no_drift(
                captured=self._env,
                pinned_python_version=self._c.pinned_python_version(),
                pinned_platform_prefix=self._c.platform_prefix(),
            )
        except EnvironmentDriftError as exc:
            return SmokeResult(
                outcome=SmokeOutcome.FAIL,
                reason=f"environment_drift: {exc}",
                pairs_attempted=0, pairs_recorded=0,
                harness_defect={"kind": "environment_drift", "detail": str(exc)},
                smoke_started_at_utc=started,
                smoke_finished_at_utc=datetime.now(UTC).isoformat(),
            )

        # 2) Positive control BEFORE smoke. If it fails, do not proceed.
        pre = self._pc.run()
        pre_pass = _pc_pass(pre)
        pc_pre_record = _pc_to_dict(pre, pre_pass)
        if not pre_pass:
            return SmokeResult(
                outcome=SmokeOutcome.INCONCLUSIVE,
                reason="pre_smoke_positive_control_failed",
                pairs_attempted=0, pairs_recorded=0,
                positive_control_pre=pc_pre_record,
                smoke_started_at_utc=started,
                smoke_finished_at_utc=datetime.now(UTC).isoformat(),
            )

        # 3) Create firewall rule + verify.
        try:
            self._fw.create_and_verify(program_path=self._program_path)
        except FirewallRuleError as exc:
            return SmokeResult(
                outcome=SmokeOutcome.FAIL,
                reason=f"firewall_verification_broken: {exc}",
                pairs_attempted=0, pairs_recorded=0,
                positive_control_pre=pc_pre_record,
                harness_defect={"kind": "firewall_verification_broken", "detail": str(exc)},
                smoke_started_at_utc=started,
                smoke_finished_at_utc=datetime.now(UTC).isoformat(),
            )

        parser_defects: list[dict[str, Any]] = []
        pairs_attempted = 0
        pairs_recorded = 0
        harness_defect: dict[str, Any] | None = None

        try:
            for doc in self._docs:
                if harness_defect is not None:
                    break
                for adapter in self._adapters.iter_ordered():
                    pairs_attempted += 1
                    try:
                        outcome, record = self._run_pair(doc, adapter)
                    except _HarnessLevelStop as stop:
                        harness_defect = stop.detail
                        # The stop may have been raised AFTER the
                        # per-pair execution record was written to
                        # disk (e.g., the network-egress-not-blocked
                        # halt is signaled *after* the record write
                        # so the observation is captured on disk).
                        # In that case count the record as recorded.
                        if stop.detail.get("_record_written"):
                            pairs_recorded += 1
                        break
                    pairs_recorded += 1  # record written before this line
                    if outcome.status is ParseStatus.DEFECT:
                        parser_defects.append({
                            "canonical_id": doc.canonical_id,
                            "parser_id": adapter.parser_id,
                            "defect_reason": outcome.defect_reason,
                        })
        finally:
            # 4) Positive control AFTER smoke, then cleanup.
            post = self._pc.run()
            post_pass = _pc_pass(post)
            pc_post_record = _pc_to_dict(post, post_pass)

            cleanup_error: str | None = None
            try:
                self._fw.cleanup()
            except FirewallRuleError as exc:
                cleanup_error = str(exc)

        # 5) Decide outcome.
        finished = datetime.now(UTC).isoformat()

        if harness_defect is not None:
            return SmokeResult(
                outcome=SmokeOutcome.FAIL,
                reason=f"harness_defect: {harness_defect.get('kind')}",
                pairs_attempted=pairs_attempted,
                pairs_recorded=pairs_recorded,
                parser_defects=parser_defects,
                harness_defect=harness_defect,
                positive_control_pre=pc_pre_record,
                positive_control_post=pc_post_record,
                smoke_started_at_utc=started,
                smoke_finished_at_utc=finished,
            )
        if cleanup_error is not None:
            return SmokeResult(
                outcome=SmokeOutcome.FAIL,
                reason=f"firewall_rule_cleanup_broken: {cleanup_error}",
                pairs_attempted=pairs_attempted,
                pairs_recorded=pairs_recorded,
                parser_defects=parser_defects,
                harness_defect={"kind": "firewall_rule_cleanup_broken",
                                "detail": cleanup_error},
                positive_control_pre=pc_pre_record,
                positive_control_post=pc_post_record,
                smoke_started_at_utc=started,
                smoke_finished_at_utc=finished,
            )
        if not post_pass:
            return SmokeResult(
                outcome=SmokeOutcome.INCONCLUSIVE,
                reason="post_smoke_positive_control_failed",
                pairs_attempted=pairs_attempted,
                pairs_recorded=pairs_recorded,
                parser_defects=parser_defects,
                positive_control_pre=pc_pre_record,
                positive_control_post=pc_post_record,
                smoke_started_at_utc=started,
                smoke_finished_at_utc=finished,
            )
        if pairs_recorded != pairs_attempted:
            return SmokeResult(
                outcome=SmokeOutcome.FAIL,
                reason="schema_writer_broken_or_incomplete_run",
                pairs_attempted=pairs_attempted,
                pairs_recorded=pairs_recorded,
                parser_defects=parser_defects,
                harness_defect={"kind": "schema_writer_broken",
                                "detail": f"attempted={pairs_attempted} "
                                          f"recorded={pairs_recorded}"},
                positive_control_pre=pc_pre_record,
                positive_control_post=pc_post_record,
                smoke_started_at_utc=started,
                smoke_finished_at_utc=finished,
            )
        return SmokeResult(
            outcome=SmokeOutcome.PASS,
            reason="ok",
            pairs_attempted=pairs_attempted,
            pairs_recorded=pairs_recorded,
            parser_defects=parser_defects,
            positive_control_pre=pc_pre_record,
            positive_control_post=pc_post_record,
            smoke_started_at_utc=started,
            smoke_finished_at_utc=finished,
        )

    # ------------------------------------------------------------------
    # Per-pair execution.

    def _run_pair(
        self,
        doc: SmokeDocument,
        adapter: ParserAdapterProtocol,
    ) -> tuple[ParseOutcome, ExecutionRecord]:
        pair_dir = self._run_dir / adapter.parser_id / doc.canonical_id
        pair_dir.mkdir(parents=True, exist_ok=True)

        # 1) Per-invocation network probe.
        observation = run_probes(self._probe)

        # 2) Invoke the parser regardless of probe outcome, so the
        #    record captures a valid ParseOutcome. The runner decides
        #    from the observation whether to halt AFTER writing.
        timer = InvocationTimer(
            resource_sampler=self._resource_sampler_factory(),
            vram_sampler=self._vram_sampler_factory(adapter.parser_id),
        )
        timer.__enter__()
        try:
            pdf_bytes = doc.pdf_bytes_source()
            outcome = adapter.compile(pdf_bytes, doc.canonical_id)
        finally:
            timing = timer.finalize()

        # 3) Write raw output + normalized output.
        if outcome.status is ParseStatus.EXECUTED:
            raw_md = outcome.markdown or ""
            normalized_md = normalize(raw_md)
        else:
            raw_md = ""
            normalized_md = ""
        raw_path = pair_dir / "raw_output.md"
        raw_path.write_text(raw_md, encoding="utf-8")
        normalized_path = pair_dir / "normalized_output.md"
        normalized_path.write_text(normalized_md, encoding="utf-8")

        # 4) Write stdout + stderr.
        stdout_capture = write_stdout(pair_dir / "stdout.txt", outcome.stdout)
        stderr_capture = write_stderr(pair_dir / "stderr.txt", outcome.stderr)

        # 5) Compute output hash/length.
        raw_bytes = raw_md.encode("utf-8")
        output_bytes = len(raw_bytes)
        output_sha = hashlib.sha256(raw_bytes).hexdigest()

        # 6) Build + validate + write execution record.
        parser_id = adapter.parser_id
        is_cpu_only = parser_id in CPU_ONLY_PARSERS
        record = ExecutionRecord(
            pair_id=compute_pair_id(
                canonical_id=doc.canonical_id,
                parser_id=parser_id,
            ),
            canonical_id=doc.canonical_id,
            corpus=doc.corpus,
            parser_id=parser_id,
            parser_package_version=adapter.package_version(),
            parser_package_source_sha256=adapter.package_source_sha256(),
            parser_model_version=adapter.parser_model_version() if not is_cpu_only else None,
            parser_model_artifact_sha256=adapter.parser_model_artifact_sha256() if not is_cpu_only else None,
            adapter_source_sha256=adapter.adapter_source_sha256(),
            python_version=self._env.python_version,
            platform_string=self._env.platform_string,
            git_commit=self._env.git_commit,
            cpu_physical_cores=self._env.cpu_physical_cores or 0,
            cuda_version=None if is_cpu_only else self._env.cuda_version,
            cuda_driver_version=None if is_cpu_only else self._env.cuda_driver_version,
            cuda_device_name=None if is_cpu_only else self._env.cuda_device_name,
            model_cache_path=None if is_cpu_only else "<model_cache_path_to_be_locked_at_B1a_7c>",
            network_egress_blocked=observation.blocked,
            pair_started_at=timing.pair_started_at,
            pair_finished_at=timing.pair_finished_at,
            wall_clock_seconds=timing.wall_clock_seconds,
            cpu_seconds_user=timing.cpu_seconds_user,
            cpu_seconds_system=timing.cpu_seconds_system,
            peak_rss_bytes=timing.peak_rss_bytes,
            peak_vram_bytes=None if is_cpu_only else timing.peak_vram_bytes,
            cuda_events=None if is_cpu_only else timing.cuda_events,
            output_bytes=output_bytes,
            output_sha256=output_sha,
            stdout_bytes=stdout_capture.byte_length,
            stdout_sha256=stdout_capture.sha256,
            stderr_bytes=stderr_capture.byte_length,
            stderr_sha256=stderr_capture.sha256,
            exit_status=("EXECUTED" if outcome.status is ParseStatus.EXECUTED else "DEFECT"),
            defect_reason=outcome.defect_reason,
            normalization_version=NORMALIZATION_VERSION,
            parser_execution_contract_version="v1",
            parser_execution_contract_config_sha256=self._c.parser_execution.canonical_sha256,
            smoke_spec_config_sha256=self._c.smoke_spec.canonical_sha256,
        )

        record_path = pair_dir / "execution_record.json"
        try:
            write_execution_record(record_path, record)
        except ExecutionRecordSchemaError as exc:
            raise _HarnessLevelStop(
                {"kind": "schema_writer_broken",
                 "canonical_id": doc.canonical_id,
                 "parser_id": parser_id,
                 "detail": str(exc)}
            ) from exc

        # 7) Downstream: reviewer artifact for EXECUTED invocations.
        # Reviewer artifact paths are pair-id-scoped (blinded), NOT
        # parser-slug-scoped. The on-disk record path uses the
        # contract's <parser>/<canonical_id>/ layout for audit; the
        # reviewer surface never sees the parser slug in a path.
        blinded_prefix = self._run_dir / f"pair_{record.pair_id}"
        analysis_record: dict[str, Any] = {"pair_id": record.pair_id,
                                           "canonical_id": doc.canonical_id,
                                           "parser_id": parser_id,
                                           "exit_status": record.exit_status,
                                           "reviewer_artifact": None}
        if outcome.status is ParseStatus.EXECUTED:
            try:
                artifact = build_smoke_reviewer_artifact(
                    canonical_id=doc.canonical_id,
                    parser_id=parser_id,
                    source_pdf_path=blinded_prefix / "source.pdf",
                    extraction_markdown_path=blinded_prefix / "raw_output.md",
                    normalized_markdown_path=blinded_prefix / "normalized_output.md",
                )
            except Exception as exc:
                raise _HarnessLevelStop(
                    {"kind": "blinding_broken",
                     "canonical_id": doc.canonical_id,
                     "parser_id": parser_id,
                     "detail": str(exc)}
                ) from exc
            # Provenance link check.
            if artifact.get("pair_id") != record.pair_id:
                raise _HarnessLevelStop(
                    {"kind": "provenance_mismatch",
                     "canonical_id": doc.canonical_id,
                     "parser_id": parser_id,
                     "detail": (f"reviewer_artifact.pair_id={artifact.get('pair_id')!r} "
                                f"!= execution_record.pair_id={record.pair_id!r}")}
                )
            analysis_record["reviewer_artifact"] = artifact

        # 8) Write analysis record + project the review surface for the log.
        (pair_dir / "analysis_record.json").write_text(
            _json_dump(analysis_record), encoding="utf-8"
        )

        # 9) Halt-on-egress-not-blocked. The execution_record.json
        # for this invocation is already on disk (step 6); the record
        # captures network_egress_blocked=false so a downstream audit
        # can identify the specific invocation that observed the
        # policy failure.
        if not observation.blocked:
            raise _HarnessLevelStop(
                {"kind": "network_egress_not_blocked",
                 "canonical_id": doc.canonical_id,
                 "parser_id": parser_id,
                 "detail": ("per-invocation firewall probe indicated egress "
                            "reachable; the frozen network policy did not hold"),
                 "_record_written": True}
            )

        # 10) Halt-on-known-good-but-drifting-contract-config-sha256.
        if record.parser_execution_contract_config_sha256 != self._c.parser_execution.canonical_sha256:
            raise _HarnessLevelStop(
                {"kind": "environment_drift",
                 "canonical_id": doc.canonical_id,
                 "parser_id": parser_id,
                 "detail": "parser_execution_contract_config_sha256 drifted between load and write",
                 "_record_written": True}
            )

        # 11) Parser-level DEFECT is fine here: the record is complete
        # and downstream-flow is not required for DEFECT. Continue.
        return outcome, record


class _HarnessLevelStop(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail))


def _pc_pass(obs: NetworkEgressObservation) -> bool:
    # Positive control passes iff both probes reached the canary.
    return obs.probe_tcp_connect.connected and obs.probe_https_get.connected


def _pc_to_dict(obs: NetworkEgressObservation, passed: bool) -> dict[str, Any]:
    return {
        "positive_control_pass": passed,
        "tcp_connect_probe": obs.probe_tcp_connect.__dict__,
        "https_get_probe": obs.probe_https_get.__dict__,
    }


def _json_dump(obj: Any) -> str:
    import json
    return json.dumps(obj, indent=2, sort_keys=True, default=str)
