"""Subprocess invocation contract + real parser adapter branch cases.

Uses a FakeSubprocessInvoker so no real subprocess is spawned. The
tests exercise every branch of ``SubprocessParserAdapter.compile``:
happy EXECUTED, empty-output EXECUTED (Docling), timeout DEFECT,
non-zero-exit DEFECT, worker-reported DEFECT with coded reason,
malformed JSON DEFECT, non-dict payload DEFECT, wrong-status DEFECT,
non-string markdown DEFECT.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import ParseStatus
from benchmarks.eval_v1.smoke_b1a_7b.real_adapters import (
    RealAdapterConfig,
    SubprocessParserAdapter,
)
from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import (
    SubprocessInvocation,
    SubprocessResult,
)

_HEX64 = "a" * 64


# ---------------------------------------------------------------------------
# Fake subprocess invoker.


@dataclass
class FakeSubprocessInvoker:
    """Delegates each invocation to a caller-supplied function. Records
    every invocation so command construction can be asserted."""

    handler: Callable[[SubprocessInvocation], SubprocessResult]
    invocations: list[SubprocessInvocation] = field(default_factory=list)

    def invoke(self, invocation: SubprocessInvocation) -> SubprocessResult:
        self.invocations.append(invocation)
        return self.handler(invocation)


def _worker_prefix() -> list[str]:
    # A fixed prefix that a synthetic test can assert on but that never
    # gets executed by anything in-process.
    return [
        "python", "-m",
        "benchmarks.eval_v1.smoke_b1a_7b.workers.main",
    ]


def _cfg(parser_id: str = "aksharamd-reference",
         *,
         is_vlm: bool = False,
         timeout: float = 120.0,
         model_version: str | None = None,
         model_sha: str | None = None,
) -> RealAdapterConfig:
    return RealAdapterConfig(
        parser_id=parser_id,
        package_version="0.3.6",
        package_source_sha256=_HEX64,
        adapter_source_sha256=_HEX64,
        parser_model_version=model_version,
        parser_model_artifact_sha256=model_sha,
        timeout_seconds=timeout,
        is_vlm=is_vlm,
    )


def _adapter(
    invoker: FakeSubprocessInvoker,
    *,
    parser_id: str = "aksharamd-reference",
    is_vlm: bool = False,
    timeout: float = 120.0,
    model_version: str | None = None,
    model_sha: str | None = None,
) -> SubprocessParserAdapter:
    return SubprocessParserAdapter(
        config=_cfg(parser_id, is_vlm=is_vlm, timeout=timeout,
                    model_version=model_version, model_sha=model_sha),
        subprocess_invoker=invoker,
        worker_argv_prefix=_worker_prefix(),
        offline_env={
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DOCLING_ARTIFACTS_OFFLINE": "1",
        },
    )


# ---------------------------------------------------------------------------
# Invocation shape.


def test_adapter_builds_expected_argv_and_env() -> None:
    def handler(inv):
        return SubprocessResult(
            stdout=json.dumps({"status": "EXECUTED", "markdown": "ok"}).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    adapter = _adapter(invoker, parser_id="marker", is_vlm=True,
                       timeout=600.0, model_version="v",
                       model_sha=_HEX64)
    outcome = adapter.compile(b"%PDF-1.5", canonical_id="SYN-1")
    assert outcome.status is ParseStatus.EXECUTED
    assert outcome.markdown == "ok"

    (inv,) = invoker.invocations
    assert inv.argv[:len(_worker_prefix())] == _worker_prefix()
    assert "--parser-id" in inv.argv
    assert "marker" in inv.argv
    assert "--canonical-id" in inv.argv
    assert "SYN-1" in inv.argv
    assert inv.timeout_seconds == 600.0
    assert inv.env["HF_HUB_OFFLINE"] == "1"
    assert inv.env["TRANSFORMERS_OFFLINE"] == "1"
    assert inv.env["DOCLING_ARTIFACTS_OFFLINE"] == "1"
    assert inv.stdin_bytes == b"%PDF-1.5"


# ---------------------------------------------------------------------------
# EXECUTED branches.


def test_executed_happy_path() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps({"status": "EXECUTED", "markdown": "# hi\n"}).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker).compile(b"", canonical_id="X")
    assert outcome.status is ParseStatus.EXECUTED
    assert outcome.markdown == "# hi\n"


def test_executed_empty_string_is_valid_output() -> None:
    """Docling documented empty-output-on-success stays EXECUTED."""
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps({"status": "EXECUTED", "markdown": ""}).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.05, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker, parser_id="docling", is_vlm=True,
                       timeout=600.0, model_version="v",
                       model_sha=_HEX64).compile(b"", canonical_id="X")
    assert outcome.status is ParseStatus.EXECUTED
    assert outcome.markdown == ""


def test_executed_meta_is_carried_through() -> None:
    payload = {"status": "EXECUTED", "markdown": "ok",
               "meta": {"pages": 3, "cell_count": 12}}
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps(payload).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker).compile(b"", canonical_id="X")
    assert outcome.meta == {"pages": 3, "cell_count": 12}


# ---------------------------------------------------------------------------
# DEFECT branches.


def test_timeout_maps_to_coded_reason() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=b"", stderr=b"", exit_code=-1,
            wall_clock_seconds=600.0, timed_out=True,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker, parser_id="marker", is_vlm=True,
                       timeout=600.0, model_version="v",
                       model_sha=_HEX64).compile(b"", canonical_id="X")
    assert outcome.status is ParseStatus.DEFECT
    assert outcome.defect_reason == "marker_timeout_600s"


def test_timeout_reference_parser_uses_reference_prefix() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=b"", stderr=b"", exit_code=-1,
            wall_clock_seconds=120.0, timed_out=True,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker, parser_id="aksharamd-reference",
                       timeout=120.0).compile(b"", canonical_id="X")
    assert outcome.defect_reason == "reference_parser_timeout_120s"


def test_nonzero_exit_falls_back_to_generic_exception_reason() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=b"", stderr=b"traceback something\n",
            exit_code=1, wall_clock_seconds=0.5, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker, parser_id="markitdown").compile(
        b"", canonical_id="X",
    )
    assert outcome.status is ParseStatus.DEFECT
    assert outcome.defect_reason == "markitdown_exception:NonZeroExit"


def test_worker_reported_coded_reason_wins_over_generic() -> None:
    stderr = (
        b"traceback...\nAKSHARAMD_SMOKE_DEFECT_REASON: marker_cuda_oom\n"
    )
    def handler(_inv):
        return SubprocessResult(
            stdout=b"", stderr=stderr, exit_code=3,
            wall_clock_seconds=0.5, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker, parser_id="marker", is_vlm=True,
                       timeout=600.0, model_version="v",
                       model_sha=_HEX64).compile(b"", canonical_id="X")
    assert outcome.defect_reason == "marker_cuda_oom"


def test_malformed_json_stdout_maps_to_worker_output_malformed() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=b"not-json-at-all", stderr=b"", exit_code=0,
            wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker, parser_id="docling", is_vlm=True,
                       timeout=600.0, model_version="v",
                       model_sha=_HEX64).compile(b"", canonical_id="X")
    assert outcome.defect_reason == "docling_exception:WorkerOutputMalformed"


def test_non_dict_json_payload_maps_to_worker_output_malformed() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps([1, 2, 3]).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker).compile(b"", canonical_id="X")
    assert outcome.defect_reason == (
        "reference_parser_exception:WorkerOutputMalformed"
    )


def test_worker_defect_status_without_reason_falls_back() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps({"status": "DEFECT"}).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker).compile(b"", canonical_id="X")
    assert outcome.status is ParseStatus.DEFECT
    assert "WorkerReportedDefectWithoutReason" in outcome.defect_reason


def test_unknown_status_from_worker_maps_to_defect() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps({"status": "MAYBE", "markdown": "x"}).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker).compile(b"", canonical_id="X")
    assert outcome.status is ParseStatus.DEFECT
    assert "WorkerUnknownStatus" in outcome.defect_reason


def test_non_string_markdown_maps_to_worker_output_malformed() -> None:
    def handler(_inv):
        return SubprocessResult(
            stdout=json.dumps({"status": "EXECUTED", "markdown": 42}).encode(),
            stderr=b"", exit_code=0, wall_clock_seconds=0.1, timed_out=False,
        )
    invoker = FakeSubprocessInvoker(handler=handler)
    outcome = _adapter(invoker).compile(b"", canonical_id="X")
    assert outcome.status is ParseStatus.DEFECT
    assert "WorkerOutputMalformed" in outcome.defect_reason
