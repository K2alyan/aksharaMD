"""Real parser adapters using subprocess isolation.

All four parsers (aksharamd-reference, marker, docling, markitdown)
are launched in fresh child processes per (document, parser)
invocation. The subprocess boundary is uniform: none of the four
runs in-process. This matches the parser-execution contract §5.3
one-invocation-per-process requirement for marker/docling and
extends it uniformly to reference/markitdown so there is a single
execution path.

Each adapter builds an ``argv`` invoking the worker CLI
(``python -m benchmarks.eval_v1.smoke_b1a_7b.workers.main``), pipes
the PDF bytes to the worker via stdin, and parses the worker's JSON
result from stdout. Timeouts cause a process-tree kill and are
recorded as ``<parser>_timeout_<n>s`` DEFECT reasons. Non-zero exit
maps to ``<parser>_exception:<Class>``. Unparseable output maps to
``<parser>_exception:WorkerOutputMalformed``.

Tests inject a ``FakeSubprocessInvoker`` that returns canned bytes;
no real parser is imported and no real subprocess spawned.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .adapter_protocol import ParseOutcome, ParseStatus
from .subprocess_runner import (
    SubprocessInvocation,
    SubprocessInvoker,
    SubprocessResult,
)

# --------------------------------------------------------------------------
# Per-parser configuration.


@dataclass(frozen=True)
class RealAdapterConfig:
    parser_id: str
    package_version: str
    package_source_sha256: str
    adapter_source_sha256: str
    parser_model_version: str | None
    parser_model_artifact_sha256: str | None
    timeout_seconds: float
    is_vlm: bool


# --------------------------------------------------------------------------
# Adapter.


class SubprocessParserAdapter:
    """A ``ParserAdapterProtocol`` implementation that runs the parser
    in a subprocess. One instance per parser; parameterized by
    ``RealAdapterConfig``.
    """

    def __init__(
        self,
        *,
        config: RealAdapterConfig,
        subprocess_invoker: SubprocessInvoker,
        worker_argv_prefix: list[str],
        offline_env: dict[str, str],
    ) -> None:
        self._cfg = config
        self._invoker = subprocess_invoker
        self._worker_argv_prefix = list(worker_argv_prefix)
        self._offline_env = dict(offline_env)

    @property
    def parser_id(self) -> str:
        return self._cfg.parser_id

    def package_version(self) -> str:
        return self._cfg.package_version

    def package_source_sha256(self) -> str:
        return self._cfg.package_source_sha256

    def adapter_source_sha256(self) -> str:
        return self._cfg.adapter_source_sha256

    def parser_model_version(self) -> str | None:
        return self._cfg.parser_model_version

    def parser_model_artifact_sha256(self) -> str | None:
        return self._cfg.parser_model_artifact_sha256

    def compile(self, pdf_bytes: bytes, canonical_id: str) -> ParseOutcome:
        invocation = SubprocessInvocation(
            argv=[
                *self._worker_argv_prefix,
                "--parser-id", self._cfg.parser_id,
                "--canonical-id", canonical_id,
            ],
            timeout_seconds=self._cfg.timeout_seconds,
            env=self._offline_env,
            stdin_bytes=pdf_bytes,
            context={"parser_id": self._cfg.parser_id,
                     "canonical_id": canonical_id},
        )
        result: SubprocessResult = self._invoker.invoke(invocation)

        # Timeout -> DEFECT with the coded reason for this parser.
        if result.timed_out:
            return ParseOutcome(
                status=ParseStatus.DEFECT,
                markdown=None,
                stdout="",
                stderr=result.stderr.decode("utf-8", errors="replace"),
                defect_reason=self._timeout_reason(),
            )

        # Non-zero exit -> DEFECT. Attempt to read the worker's coded
        # reason from stderr's last line if present, otherwise fall
        # back to the generic exception code.
        if result.exit_code != 0:
            coded = self._parse_coded_reason_from_stderr(result.stderr)
            return ParseOutcome(
                status=ParseStatus.DEFECT,
                markdown=None,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
                defect_reason=coded or self._generic_exception_reason(
                    "NonZeroExit"
                ),
            )

        # Zero exit: parse the JSON payload from stdout.
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ParseOutcome(
                status=ParseStatus.DEFECT,
                markdown=None,
                stdout="",
                stderr=result.stderr.decode("utf-8", errors="replace"),
                defect_reason=self._generic_exception_reason(
                    "WorkerOutputMalformed"
                ),
            )

        if not isinstance(payload, dict):
            return ParseOutcome(
                status=ParseStatus.DEFECT,
                markdown=None,
                stdout="",
                stderr=result.stderr.decode("utf-8", errors="replace"),
                defect_reason=self._generic_exception_reason(
                    "WorkerOutputMalformed"
                ),
            )

        # Worker may itself report a DEFECT (e.g., cuda_oom detected
        # during the parser call). Trust the coded reason if present.
        status_str = str(payload.get("status", ""))
        if status_str == "DEFECT":
            defect_reason = str(payload.get("defect_reason", ""))
            if not defect_reason:
                defect_reason = self._generic_exception_reason(
                    "WorkerReportedDefectWithoutReason"
                )
            return ParseOutcome(
                status=ParseStatus.DEFECT,
                markdown=None,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
                defect_reason=defect_reason,
            )

        if status_str == "EXECUTED":
            markdown = payload.get("markdown", "")
            if not isinstance(markdown, str):
                return ParseOutcome(
                    status=ParseStatus.DEFECT,
                    markdown=None,
                    stdout="",
                    stderr=result.stderr.decode("utf-8", errors="replace"),
                    defect_reason=self._generic_exception_reason(
                        "WorkerOutputMalformed"
                    ),
                )
            meta = payload.get("meta") or {}
            return ParseOutcome(
                status=ParseStatus.EXECUTED,
                markdown=markdown,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
                defect_reason=None,
                meta=meta if isinstance(meta, dict) else {},
            )

        # Unknown status.
        return ParseOutcome(
            status=ParseStatus.DEFECT,
            markdown=None,
            stdout="",
            stderr=result.stderr.decode("utf-8", errors="replace"),
            defect_reason=self._generic_exception_reason("WorkerUnknownStatus"),
        )

    # ------------------------------------------------------------------
    # Coded-reason helpers per parser.

    def _timeout_reason(self) -> str:
        # Map parser_id -> exact coded reason string used in the
        # parser-execution contract §6 vocabulary.
        n = int(self._cfg.timeout_seconds)
        pid = self._cfg.parser_id
        if pid == "aksharamd-reference":
            return f"reference_parser_timeout_{n}s"
        return f"{pid}_timeout_{n}s"

    def _generic_exception_reason(self, class_name: str) -> str:
        pid = self._cfg.parser_id
        if pid == "aksharamd-reference":
            return f"reference_parser_exception:{class_name}"
        return f"{pid}_exception:{class_name}"

    def _parse_coded_reason_from_stderr(self, stderr: bytes) -> str | None:
        """Workers may emit a coded reason as the last stderr line
        prefixed with ``AKSHARAMD_SMOKE_DEFECT_REASON: ``. Recognizing
        it lets the adapter propagate the specific reason (e.g.,
        marker_cuda_oom) instead of falling back to a generic
        exception code."""
        prefix = "AKSHARAMD_SMOKE_DEFECT_REASON:"
        try:
            text = stderr.decode("utf-8", errors="replace")
        except Exception:
            return None
        for line in reversed(text.splitlines()):
            if line.startswith(prefix):
                return line[len(prefix):].strip() or None
        return None
