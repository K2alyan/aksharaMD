"""Versioned contracts for the quality-gate assessment boundary."""
from __future__ import annotations

import hashlib
import mimetypes
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

ASSESSMENT_SCHEMA_VERSION = "1.0"
GENERAL_INGESTION_POLICY_ID = "general-ingestion-v1"  # Historical replay policy; do not retarget.
DEFAULT_ASSESSMENT_POLICY_ID = "general-ingestion-v2"
TASK_PROFILE_SCHEMA_VERSION = "1.0"


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


class Verdict(StrEnum):
    PASS = "pass"
    CONCERN = "concern"
    FAIL = "fail"
    UNDETERMINED = "undetermined"


class AssessmentDisposition(StrEnum):
    ACCEPT = "ACCEPT"
    HOLD = "HOLD"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


class NextAction(StrEnum):
    NONE = "NONE"
    REVIEW = "REVIEW"
    RETRY_REGION = "RETRY_REGION"
    RETRY_DOCUMENT = "RETRY_DOCUMENT"
    USE_SOURCE = "USE_SOURCE"
    REQUEST_BETTER_SOURCE = "REQUEST_BETTER_SOURCE"


class Artifact(BaseModel):
    """Immutable input whose identity is its supplied bytes."""

    content_hash: str
    byte_size: int = Field(ge=0)
    media_type: str = "application/octet-stream"
    logical_id: str = ""
    storage_reference: str | None = None
    data: bytes = Field(repr=False, exclude=True)

    @model_validator(mode="after")
    def _validate_identity(self):
        if len(self.content_hash) != 64 or any(c not in "0123456789abcdef" for c in self.content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        if self.byte_size != len(self.data):
            raise ValueError("byte_size does not match artifact data")
        if hashlib.sha256(self.data).hexdigest() != self.content_hash:
            raise ValueError("content_hash does not match artifact data")
        return self

    @classmethod
    def from_path(cls, path: str | Path, *, logical_id: str | None = None):
        resolved = Path(path)
        data = resolved.read_bytes()
        media_type = mimetypes.guess_type(resolved.name)[0]
        if media_type is None and resolved.suffix.lower() in {".md", ".markdown", ".txt", ".rst"}:
            media_type = "text/plain"
        return cls(
            content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data),
            media_type=media_type or "application/octet-stream",
            logical_id=logical_id or resolved.name, storage_reference=str(resolved), data=data,
        )


class SourceArtifact(Artifact):
    pass


class CandidateArtifact(Artifact):
    parser_name: str | None = None
    parser_version: str | None = None
    parser_configuration_id: str | None = None
    declared_truncated: bool = False
    original_source_hash: str | None = None


class RequiredLiteralRelationship(BaseModel):
    """A caller-declared, directional relationship between two literal spans.

    This contract checks only literal order and bounded textual proximity.  It
    deliberately does not infer a semantic relation from natural language.
    """

    first_literal: str = Field(min_length=1)
    second_literal: str = Field(min_length=1)
    max_characters_between: int = Field(default=160, ge=0)
    case_sensitive: bool = False

    @field_validator("first_literal", "second_literal")
    @classmethod
    def _literal_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("literal must contain non-whitespace characters")
        return value


class TaskProfile(BaseModel):
    """Versioned, caller-declared requirements for one bounded use case.

    The initial deterministic profile supports exact required facts and pairs
    whose literal ordering/proximity must survive delivery.  It is not a
    general relevance or usefulness score.
    """

    schema_version: str = TASK_PROFILE_SCHEMA_VERSION
    purpose: str = Field(min_length=1)
    required_literals: list[str] = Field(default_factory=list)
    required_relationships: list[RequiredLiteralRelationship] = Field(default_factory=list)

    @field_validator("purpose")
    @classmethod
    def _purpose_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("purpose must contain non-whitespace characters")
        return value

    @field_validator("required_literals")
    @classmethod
    def _required_literals_are_not_whitespace(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("required_literals must not contain blank values")
        return values

    @model_validator(mode="after")
    def _has_deterministic_requirement(self):
        if not self.required_literals and not self.required_relationships:
            raise ValueError("TaskProfile requires at least one literal or literal relationship")
        return self


class EvidenceItem(BaseModel):
    check_id: str
    check_version: str = "1"
    status: EvidenceStatus
    measurement: float | None = None
    unit: str | None = None
    denominator: float | None = None
    details: dict[str, object] = Field(default_factory=dict)


class Finding(BaseModel):
    code: str
    dimension: str
    severity: str
    message: str
    evidence_ids: list[str] = Field(default_factory=list)
    origin: str = "unknown"
    region: str | None = None


class DimensionResult(BaseModel):
    status: EvidenceStatus
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class AssessmentResult(BaseModel):
    schema_version: str = ASSESSMENT_SCHEMA_VERSION
    policy_id: str = GENERAL_INGESTION_POLICY_ID
    source_hash: str | None = None
    candidate_hash: str
    execution: str = "complete"
    dimensions: dict[str, DimensionResult]
    disposition: AssessmentDisposition
    next_action: NextAction
