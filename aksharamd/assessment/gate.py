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

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from .models import (
    DEFAULT_ASSESSMENT_POLICY_ID,
    GENERAL_INGESTION_POLICY_ID,
    TASK_PROFILE_NONE,
    AssessmentDisposition,
    EvidenceStatus,
    NextAction,
    TaskProfile,
    Verdict,
    canonical_task_profile_sha256,
)
from .text_preservation import SOURCE_TEXT_PRESERVATION_POLICY_ID

GATE_MANIFEST_SCHEMA_VERSION = "1.0"
GATE_REPORT_SCHEMA_VERSION = "1.0"
GateInvariant = Literal["schema_version", "policy_id", "source_hash", "task_profile_sha256"]
_INVARIANT_FIELDS: tuple[GateInvariant, ...] = (
    "schema_version", "policy_id", "source_hash", "task_profile_sha256",
)
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

    @field_validator("measurement", "denominator", mode="before")
    @classmethod
    def _number_is_a_json_number(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("must be a JSON number or null")
        return value

    @field_validator("denominator")
    @classmethod
    def _denominator_is_nonnegative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("must be nonnegative")
        return value

    @model_validator(mode="after")
    def _details_match_the_versioned_check(self):
        if self.check_id == "critical_literal_coverage":
            if set(self.details) != {"missing"} or not isinstance(self.details["missing"], list):
                raise ValueError("critical_literal_coverage details are malformed")
            if any(not isinstance(item, str) for item in self.details["missing"]):
                raise ValueError("critical_literal_coverage missing values must be strings")
        elif self.check_id in {"markdown_fence_balance", "candidate_text_integrity"}:
            if self.details:
                raise ValueError(f"{self.check_id} details must be empty")
        elif self.check_id == "source_text_preservation":
            if set(self.details) != {"match_method", "semantic_fidelity_established", "scope"}:
                raise ValueError("source_text_preservation details are malformed")
            match = self.details["match_method"]
            if match is not None and not isinstance(match, str):
                raise ValueError("match_method must be a string or null")
            if self.details["semantic_fidelity_established"] is not False:
                raise ValueError("semantic_fidelity_established must be false")
            if not isinstance(self.details["scope"], str):
                raise ValueError("scope must be a string")
        elif self.check_id == "task_profile_literal_requirements":
            expected = {"task_profile_schema_version", "missing_literals", "missing_relationships"}
            if set(self.details) != expected or self.details["task_profile_schema_version"] != "1.0":
                raise ValueError("task_profile_literal_requirements details are malformed")
            if (not isinstance(self.details["missing_literals"], list)
                    or any(not isinstance(item, str) for item in self.details["missing_literals"])):
                raise ValueError("missing_literals must be a list of strings")
            relationships = self.details["missing_relationships"]
            if not isinstance(relationships, list):
                raise ValueError("missing_relationships must be a list")
            for relationship in relationships:
                if not isinstance(relationship, dict) or set(relationship) != {
                    "first_literal", "second_literal", "max_characters_between",
                }:
                    raise ValueError("missing relationship is malformed")
                if (not isinstance(relationship["first_literal"], str)
                        or not isinstance(relationship["second_literal"], str)
                        or type(relationship["max_characters_between"]) is not int
                        or relationship["max_characters_between"] < 0):
                    raise ValueError("missing relationship values have invalid primitive types")
        else:
            raise ValueError(f"unsupported evidence check_id: {self.check_id}")
        return self


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


class GateRequiredLiteralRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_literal: str = Field(min_length=1)
    second_literal: str = Field(min_length=1)
    max_characters_between: StrictInt = Field(default=160, ge=0)
    case_sensitive: StrictBool = False

    @field_validator("first_literal", "second_literal")
    @classmethod
    def _literal_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("literal must contain non-whitespace characters")
        return value


class GateTaskProfile(BaseModel):
    """Non-coercing replay schema for the task contract bound to an assessment."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    purpose: str = Field(min_length=1)
    required_literals: list[str]
    required_relationships: list[GateRequiredLiteralRelationship]

    @field_validator("purpose")
    @classmethod
    def _purpose_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("purpose must contain non-whitespace characters")
        return value

    @field_validator("required_literals")
    @classmethod
    def _required_literals_are_not_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("required_literals must not contain blank values")
        return values

    @model_validator(mode="after")
    def _has_requirement(self):
        if not self.required_literals and not self.required_relationships:
            raise ValueError("task profile requires at least one requirement")
        return self


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

    schema_version: Literal["1.1"]
    policy_id: str
    source_hash: str | None
    candidate_hash: str
    task_profile_sha256: str
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

    @field_validator("task_profile_sha256")
    @classmethod
    def _task_profile_hash_is_valid(cls, value: str) -> str:
        if value == TASK_PROFILE_NONE:
            return value
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

        task_dimension = self.dimensions["task_suitability"]
        no_profile_state = (
            task_dimension.status == EvidenceStatus.NOT_REQUESTED
            and task_dimension.verdict == Verdict.UNDETERMINED
            and not task_dimension.findings
            and not task_dimension.evidence
        )
        if no_profile_state != (self.task_profile_sha256 == TASK_PROFILE_NONE):
            raise ValueError("task-profile identity is inconsistent with task-suitability evidence")

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
    byte_size: StrictInt = Field(ge=0)
    media_type: str = Field(min_length=1)
    storage_reference: str

    _capture_id_is_valid = field_validator("capture_id")(_valid_sha256)


class GateBoundCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_id: str
    content_hash: str
    byte_size: StrictInt = Field(ge=0)
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
    task_profile: GateTaskProfile | None = None

    @model_validator(mode="after")
    def _binding_is_consistent(self):
        if (self.assessment.source_hash != self.source.capture_id
                or self.candidate.original_source_hash != self.source.capture_id):
            raise ValueError("inconsistent source provenance")
        if self.assessment.candidate_hash != self.candidate.content_hash:
            raise ValueError("inconsistent candidate provenance")
        profile = (
            TaskProfile.model_validate(self.task_profile.model_dump(mode="json"))
            if self.task_profile is not None
            else None
        )
        expected_profile_hash = canonical_task_profile_sha256(profile)
        if self.assessment.task_profile_sha256 != expected_profile_hash:
            raise ValueError("envelope task profile does not match assessment task-profile identity")
        return self


class GatePolicy(BaseModel):
    """Release policy applied to every comparison in a manifest."""

    model_config = ConfigDict(extra="forbid")

    required_disposition: AssessmentDisposition = AssessmentDisposition.ACCEPT
    required_invariants: list[GateInvariant] = Field(
        default_factory=lambda: list(_INVARIANT_FIELDS)
    )
    deny_new_warnings: StrictBool = True
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


def _load_assessment(path: Path) -> tuple[GateAssessmentResult, str, str]:
    payload, digest = _read_json(path, kind="assessment artifact")
    # Compiler output wraps the versioned assessment; ``aksharamd assess
    # --json`` emits the assessment directly.  Both are immutable evidence.
    try:
        if "assessment" in payload:
            envelope = GateAssessmentEnvelope.model_validate(payload)
            assessment = envelope.assessment
            task_profile_identity = assessment.task_profile_sha256
        else:
            assessment = GateAssessmentResult.model_validate(payload)
            task_profile_identity = assessment.task_profile_sha256
    except (ValueError, TypeError) as exc:
        raise GateInputError(f"Invalid assessment artifact {path}: {exc}") from exc
    return assessment, digest, task_profile_identity


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
        baseline, baseline_digest, baseline_task_profile = _load_assessment(baseline_path)
        candidate, candidate_digest, candidate_task_profile = _load_assessment(candidate_path)
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
            if field == "task_profile_sha256":
                baseline_value = baseline_task_profile
                candidate_value = candidate_task_profile
            else:
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
                "task_profile_sha256": baseline_task_profile,
                "disposition": baseline.disposition.value,
                "warning_codes": sorted(baseline_warnings),
            },
            "candidate": {
                "path": comparison.candidate,
                "sha256": candidate_digest,
                "task_profile_sha256": candidate_task_profile,
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
