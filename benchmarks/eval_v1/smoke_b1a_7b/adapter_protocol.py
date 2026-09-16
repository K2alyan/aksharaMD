"""Parser-adapter injection protocol.

The smoke harness never imports a real parser (marker, docling,
markitdown, aksharamd) directly. It receives adapters that conform
to ``ParserAdapterProtocol``. Real adapters bind to the actual
packages; synthetic adapters (used in tests) return canned outcomes.

An adapter's ``compile()`` returns a ``ParseOutcome``. The outcome
tells the harness whether the invocation produced markdown, produced
empty output, or defected — and if defected, with which coded reason.
This intentionally separates the *outcome* of a parser call from the
*mechanics* of writing an execution record; the writer lives in
``execution_record`` and consumes ``ParseOutcome``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class ParseStatus(StrEnum):
    EXECUTED = "EXECUTED"
    DEFECT = "DEFECT"


@dataclass(frozen=True)
class ParseOutcome:
    """One parser adapter's outcome on one (document) invocation.

    Invariants:
    - EXECUTED must set ``markdown`` (may be empty string, per Docling
      empty-output-on-success).
    - EXECUTED must set ``defect_reason = None``.
    - DEFECT must set ``defect_reason`` to a coded string.
    - DEFECT must set ``markdown = None``.

    ``stdout`` / ``stderr`` may be empty. ``meta`` is a free-form
    JSON-serializable dict of parser-specific metadata (layout blocks,
    per-page markers) that the adapter chooses to surface.
    """

    status: ParseStatus
    markdown: str | None
    stdout: str
    stderr: str
    defect_reason: str | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is ParseStatus.EXECUTED:
            if self.markdown is None:
                raise ValueError("EXECUTED requires markdown (may be empty string)")
            if self.defect_reason is not None:
                raise ValueError("EXECUTED must not carry a defect_reason")
        elif self.status is ParseStatus.DEFECT:
            if self.markdown is not None:
                raise ValueError("DEFECT must not carry markdown")
            if not self.defect_reason:
                raise ValueError("DEFECT requires a non-empty coded defect_reason")


class ParserAdapterProtocol(Protocol):
    """Every adapter (real or synthetic) exposes this interface.

    ``parser_id`` returns the coded parser slug: ``aksharamd-reference``,
    ``marker``, ``docling``, ``markitdown``. This slug is what
    ``sha256(parser_id)[:16]`` is computed from and is never displayed
    on the reviewer surface.
    """

    parser_id: str

    def package_version(self) -> str:
        """Pinned version string, e.g. ``0.3.6``."""

    def package_source_sha256(self) -> str:
        """SHA-256 of the parser package artifact (wheel or model),
        recorded per invocation. Adapters that cannot compute this
        (e.g., because the pinned wheel hash freeze is pending B1a-7c)
        may return a placeholder deterministic string agreed with the
        harness; the harness verifies match against the pinned value."""

    def adapter_source_sha256(self) -> str:
        """SHA-256 of the adapter module's own bytes, so a change to
        the harness wrapper is auditable independently of the parser
        package version."""

    def parser_model_version(self) -> str | None:
        """Model version string for VLM parsers; None for CPU-only /
        no-model parsers."""

    def parser_model_artifact_sha256(self) -> str | None:
        """Model artifact hash; None for CPU-only / no-model parsers."""

    def compile(self, pdf_bytes: bytes, canonical_id: str) -> ParseOutcome:
        """Invoke the parser on a single PDF. Must not raise: any
        exception is the adapter's responsibility to map to a coded
        DEFECT ParseOutcome. Callers rely on that discipline."""
