"""Deterministic regression gates over saved assessment artifacts.

The gate compares immutable assessment evidence.  It never parses documents,
repairs output, calls a model, or interprets an assessment as a prediction of
downstream QA quality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AssessmentDisposition, AssessmentResult

GATE_MANIFEST_SCHEMA_VERSION = "1.0"
GATE_REPORT_SCHEMA_VERSION = "1.0"
_INVARIANT_FIELDS = ("schema_version", "policy_id", "source_hash")


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


def _binding_value(payload: dict, section: str, field: str, path: Path):
    value = payload.get(section)
    if not isinstance(value, dict) or field not in value:
        raise GateInputError(f"Assessment artifact {path} is missing {section}.{field}")
    return value[field]


def _load_assessment(path: Path) -> tuple[AssessmentResult, str]:
    payload, digest = _read_json(path, kind="assessment artifact")
    # Compiler output wraps the versioned assessment; ``aksharamd assess
    # --json`` emits the assessment directly.  Both are immutable evidence.
    if "assessment" in payload:
        if payload.get("binding_schema_version") != "1.0":
            raise GateInputError(
                f"Assessment artifact {path} has an unsupported binding_schema_version"
            )
        assessment_payload = payload["assessment"]
    else:
        assessment_payload = payload
    try:
        assessment = AssessmentResult.model_validate(assessment_payload)
    except (ValueError, TypeError) as exc:
        raise GateInputError(f"Invalid assessment artifact {path}: {exc}") from exc

    if assessment.execution != "complete":
        raise GateInputError(f"Assessment artifact {path} is not a complete execution")
    if assessment.schema_version != "1.0":
        raise GateInputError(f"Assessment artifact {path} has an unsupported schema_version")
    if "assessment" in payload:
        source_hash = _binding_value(payload, "source", "capture_id", path)
        candidate_hash = _binding_value(payload, "candidate", "content_hash", path)
        original_source_hash = _binding_value(payload, "candidate", "original_source_hash", path)
        if assessment.source_hash != source_hash or original_source_hash != source_hash:
            raise GateInputError(f"Assessment artifact {path} has inconsistent source provenance")
        if assessment.candidate_hash != candidate_hash:
            raise GateInputError(f"Assessment artifact {path} has inconsistent candidate provenance")
    return assessment, digest


def _warning_codes(assessment: AssessmentResult) -> set[str]:
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
