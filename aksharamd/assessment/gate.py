"""Deterministic regression gates over saved assessment artifacts.

The gate compares immutable assessment evidence.  It never parses documents,
repairs output, calls a model, or interprets an assessment as a prediction of
downstream QA quality.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    ASSESSMENT_SCHEMA_VERSION,
    DEFAULT_ASSESSMENT_POLICY_ID,
    GENERAL_INGESTION_POLICY_ID,
    AssessmentDisposition,
    EvidenceStatus,
    NextAction,
    TaskProfile,
    Verdict,
)
from .text_preservation import SOURCE_TEXT_PRESERVATION_POLICY_ID

GATE_MANIFEST_SCHEMA_VERSION = "1.0"
GATE_REPORT_SCHEMA_VERSION = "1.0"
_INVARIANT_FIELDS = ("schema_version", "policy_id", "source_hash")
_DIMENSIONS = frozenset({
    "conversion_fidelity",
    "structural_usability",
    "content_integrity",
    "task_suitability",
})
_SUPPORTED_POLICY_IDS = frozenset({
    GENERAL_INGESTION_POLICY_ID,
    DEFAULT_ASSESSMENT_POLICY_ID,
    SOURCE_TEXT_PRESERVATION_POLICY_ID,
})
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _valid_sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("must be a lowercase SHA-256 digest")
    return value


class GateEvidenceItem(BaseModel):
    """Strict replay schema for one version-1 evidence item."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(min_length=1)
    check_version: str = Field(min_length=1)
    status: EvidenceStatus
    measurement: float | None
    unit: str | None
    denominator: float | None
    details: dict[str, object]

    @field_validator("measurement", "denominator")
    @classmethod
    def _number_is_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be finite")
        return value

    @field_validator("denominator")
    @classmethod
    def _denominator_is_nonnegative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("must be nonnegative")
        return value


class GateFinding(BaseModel):
    """Strict replay schema for one version-1 finding."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    severity: Literal["warning", "major", "critical"]
    message: str = Field(min_length=1)
    evidence_ids: list[str]
    origin: Literal["unknown", "conversion", "representation"]
    region: str | None


class GateDimensionResult(BaseModel):
    """Strict replay schema with local evidence/finding consistency checks."""

    model_config = ConfigDict(extra="forbid")

    status: EvidenceStatus
    verdict: Verdict
    findings: list[GateFinding]
    evidence: list[GateEvidenceItem]

    @model_validator(mode="after")
    def _status_and_verdict_are_consistent(self):
        if self.verdict in {Verdict.PASS, Verdict.CONCERN} and self.status != EvidenceStatus.OBSERVED:
            raise ValueError(f"{self.verdict.value} verdict requires observed evidence")
        if self.verdict == Verdict.FAIL and self.status not in {
            EvidenceStatus.OBSERVED, EvidenceStatus.FAILED,
        }:
            raise ValueError("fail verdict requires observed or failed evidence")
        if self.verdict == Verdict.UNDETERMINED and self.status == EvidenceStatus.OBSERVED:
            raise ValueError("undetermined verdict cannot claim observed evidence")
        evidence_ids = [item.check_id for item in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence check_id values must be unique within a dimension")
        known = set(evidence_ids)
        for finding in self.findings:
            missing = set(finding.evidence_ids) - known
            if missing:
                raise ValueError(f"finding references unknown evidence ids: {sorted(missing)}")
        return self


class GateAssessmentResult(BaseModel):
    """Closed version-1 result schema used only at the release-gate boundary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[ASSESSMENT_SCHEMA_VERSION]
    policy_id: str
    source_hash: str | None
    candidate_hash: str
    execution: Literal["complete"]
    dimensions: dict[str, GateDimensionResult]
    disposition: AssessmentDisposition
    next_action: NextAction

    @field_validator("policy_id")
    @classmethod
    def _policy_is_supported(cls, value: str) -> str:
        if value not in _SUPPORTED_POLICY_IDS:
            raise ValueError(f"unsupported policy_id: {value}")
        return value

    @field_validator("source_hash")
    @classmethod
    def _source_hash_is_valid(cls, value: str | None) -> str | None:
        return _valid_sha256(value) if value is not None else None

    @field_validator("candidate_hash")
    @classmethod
    def _candidate_hash_is_valid(cls, value: str) -> str:
        return _valid_sha256(value)

    @model_validator(mode="after")
    def _result_is_internally_consistent(self):
        if set(self.dimensions) != _DIMENSIONS:
            missing = sorted(_DIMENSIONS - set(self.dimensions))
            unexpected = sorted(set(self.dimensions) - _DIMENSIONS)
            raise ValueError(f"dimension set mismatch; missing={missing}, unexpected={unexpected}")
        for name, dimension in self.dimensions.items():
            if any(finding.dimension != name for finding in dimension.findings):
                raise ValueError(f"finding dimension does not match dimension key: {name}")

        if any(dimension.verdict == Verdict.FAIL for dimension in self.dimensions.values()):
            expected = (AssessmentDisposition.HOLD, NextAction.REVIEW)
        elif any(dimension.status in {EvidenceStatus.UNKNOWN, EvidenceStatus.FAILED}
                 for dimension in self.dimensions.values()):
            expected = (AssessmentDisposition.ABSTAIN, NextAction.REVIEW)
        else:
            expected = (AssessmentDisposition.ACCEPT, NextAction.NONE)
        if (self.disposition, self.next_action) != expected:
            raise ValueError(
                "disposition/next_action inconsistent with dimensions; "
                f"expected {expected[0].value}/{expected[1].value}"
            )
        return self


class GateBoundSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_id: str
    capture_id: str
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    storage_reference: str

    _capture_id_is_valid = field_validator("capture_id")(_valid_sha256)


class GateBoundCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_id: str
    content_hash: str
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    storage_reference: str
    original_source_hash: str
    parser_name: str | None
    parser_version: str | None
    parser_configuration_id: str | None

    _content_hash_is_valid = field_validator("content_hash")(_valid_sha256)
    _original_source_hash_is_valid = field_validator("original_source_hash")(_valid_sha256)


class GateAssessmentEnvelope(BaseModel):
    """Closed schema for compiler-produced ``quality_assessment.json``."""

    model_config = ConfigDict(extra="forbid")

    binding_schema_version: Literal["1.0"]
    assessment: GateAssessmentResult
    source: GateBoundSource
    candidate: GateBoundCandidate
    task_profile: TaskProfile | None = None

    @model_validator(mode="after")
    def _binding_is_consistent(self):
        if (self.assessment.source_hash != self.source.capture_id
                or self.candidate.original_source_hash != self.source.capture_id):
            raise ValueError("inconsistent source provenance")
        if self.assessment.candidate_hash != self.candidate.content_hash:
            raise ValueError("inconsistent candidate provenance")
        return self


class GatePolicy(BaseModel):
    """Release policy applied to every comparison in a manifest."""

    model_config = ConfigDict(extra="forbid")

    required_disposition: AssessmentDisposition = AssessmentDisposition.ACCEPT
    required_invariants: list[Literal["schema_version", "policy_id", "source_hash"]] = Field(
        default_factory=lambda: list(_INVARIANT_FIELDS)
    )
    deny_new_warnings: bool = True
    allow_new_warning_codes: set[str] = Field(default_factory=set)
    deny_warning_codes: set[str] = Field(default_factory=set)

    @field_validator("allow_new_warning_codes", "deny_warning_codes")
    @classmethod
    def _codes_are_not_blank(cls, values: set[str]) -> set[str]:
        if any(not value.strip() for value in values):
            raise ValueError("warning codes must not be blank")
        return values

    @model_validator(mode="after")
    def _rules_do_not_conflict(self):
        overlap = self.allow_new_warning_codes & self.deny_warning_codes
        if overlap:
            raise ValueError(f"warning codes cannot be both allowed and denied: {sorted(overlap)}")
        if len(set(self.required_invariants)) != len(self.required_invariants):
            raise ValueError("required_invariants must not contain duplicates")
        return self


class GateComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    baseline: str = Field(min_length=1)
    candidate: str = Field(min_length=1)

    @field_validator("id", "baseline", "candidate")
    @classmethod
    def _value_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace characters")
        return value


class GateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = GATE_MANIFEST_SCHEMA_VERSION
    policy: GatePolicy = Field(default_factory=GatePolicy)
    comparisons: list[GateComparison] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        if value != GATE_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"Unsupported gate manifest schema version: {value}")
        return value

    @model_validator(mode="after")
    def _comparison_ids_are_unique(self):
        ids = [comparison.id for comparison in self.comparisons]
        if len(set(ids)) != len(ids):
            raise ValueError("comparison ids must be unique")
        return self


class GateInputError(ValueError):
    """A manifest or referenced assessment artifact is invalid."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, *, kind: str) -> tuple[dict, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GateInputError(f"Could not read {kind} {path}: {exc}") from exc
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateInputError(f"Invalid JSON in {kind} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateInputError(f"{kind.capitalize()} {path} must contain a JSON object")
    return payload, _sha256(data)


def load_gate_manifest(path: Path) -> tuple[GateManifest, str]:
    """Load and strictly validate a gate manifest."""
    payload, digest = _read_json(path, kind="manifest")
    try:
        return GateManifest.model_validate(payload), digest
    except ValueError as exc:
        raise GateInputError(f"Invalid gate manifest {path}: {exc}") from exc


def _load_assessment(path: Path) -> tuple[GateAssessmentResult, str]:
    payload, digest = _read_json(path, kind="assessment artifact")
    # Compiler output wraps the versioned assessment; ``aksharamd assess
    # --json`` emits the assessment directly.  Both are immutable evidence.
    try:
        if "assessment" in payload:
            assessment = GateAssessmentEnvelope.model_validate(payload).assessment
        else:
            assessment = GateAssessmentResult.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise GateInputError(f"Invalid assessment artifact {path}: {exc}") from exc
    return assessment, digest


def _warning_codes(assessment: GateAssessmentResult) -> set[str]:
    return {
        finding.code
        for dimension in assessment.dimensions.values()
        for finding in dimension.findings
    }


def evaluate_gate(manifest_path: Path) -> dict:
    """Evaluate all comparisons and return a stable, machine-readable report."""
    manifest_path = manifest_path.resolve()
    manifest, manifest_digest = load_gate_manifest(manifest_path)
    root = manifest_path.parent
    results: list[dict] = []

    for comparison in manifest.comparisons:
        baseline_path = (root / comparison.baseline).resolve()
        candidate_path = (root / comparison.candidate).resolve()
        baseline, baseline_digest = _load_assessment(baseline_path)
        candidate, candidate_digest = _load_assessment(candidate_path)
        baseline_warnings = _warning_codes(baseline)
        candidate_warnings = _warning_codes(candidate)
        new_warnings = candidate_warnings - baseline_warnings
        failures: list[dict[str, object]] = []

        if candidate.disposition != manifest.policy.required_disposition:
            failures.append({
                "code": "REQUIRED_DISPOSITION_NOT_MET",
                "expected": manifest.policy.required_disposition.value,
                "actual": candidate.disposition.value,
            })

        for field in manifest.policy.required_invariants:
            baseline_value = getattr(baseline, field)
            candidate_value = getattr(candidate, field)
            if baseline_value != candidate_value or candidate_value is None:
                failures.append({
                    "code": "PROVENANCE_INVARIANT_MISMATCH",
                    "invariant": field,
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                })

        denied = candidate_warnings & manifest.policy.deny_warning_codes
        if denied:
            failures.append({"code": "DENIED_WARNING_PRESENT", "warning_codes": sorted(denied)})

        blocked_new = set()
        if manifest.policy.deny_new_warnings:
            blocked_new = new_warnings - manifest.policy.allow_new_warning_codes
        if blocked_new:
            failures.append({"code": "NEW_WARNING_NOT_ALLOWED", "warning_codes": sorted(blocked_new)})

        results.append({
            "id": comparison.id,
            "status": "PASS" if not failures else "DENY",
            "baseline": {
                "path": comparison.baseline,
                "sha256": baseline_digest,
                "disposition": baseline.disposition.value,
                "warning_codes": sorted(baseline_warnings),
            },
            "candidate": {
                "path": comparison.candidate,
                "sha256": candidate_digest,
                "disposition": candidate.disposition.value,
                "warning_codes": sorted(candidate_warnings),
            },
            "new_warning_codes": sorted(new_warnings),
            "failures": failures,
        })

    denied_count = sum(result["status"] == "DENY" for result in results)
    return {
        "schema_version": GATE_REPORT_SCHEMA_VERSION,
        "gate": "parser-regression",
        "status": "PASS" if denied_count == 0 else "DENY",
        "manifest_sha256": manifest_digest,
        "summary": {
            "comparisons": len(results),
            "passed": len(results) - denied_count,
            "denied": denied_count,
        },
        "results": results,
        "scope": "Deterministic assessment regression evidence; not calibrated QA prediction.",
    }
