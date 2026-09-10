"""Focused contract tests for deterministic task-suitability assessment."""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from aksharamd.assessment import (
    AssessmentDisposition,
    Assessor,
    CandidateArtifact,
    RequiredLiteralRelationship,
    SourceArtifact,
    TaskProfile,
    Verdict,
)


def _artifact(cls, text: str):
    data = text.encode()
    return cls(
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        media_type="text/markdown",
        data=data,
    )


def test_task_profile_required_literals_are_visible_and_pass_when_present():
    profile = TaskProfile(purpose="approve invoice", required_literals=["Invoice 42", "$18"])

    result = Assessor().assess(
        source=_artifact(SourceArtifact, "Invoice 42 has balance $18"),
        candidate=_artifact(CandidateArtifact, "# Invoice 42\n\nBalance: $18"),
        task_profile=profile,
    )

    suitability = result.dimensions["task_suitability"]
    assert suitability.verdict == Verdict.PASS
    assert suitability.evidence[0].check_id == "task_profile_literal_requirements"
    # Literal checks pass, but changed wording/field layout is not preservation proof.
    assert result.disposition == AssessmentDisposition.ABSTAIN


def test_missing_task_required_literal_holds_even_when_conversion_checks_pass():
    profile = TaskProfile(purpose="route account", required_literals=["Account 77"])
    artifact = _artifact(CandidateArtifact, "Invoice is approved")

    result = Assessor().assess(
        source=_artifact(SourceArtifact, "Invoice is approved"),
        candidate=artifact,
        task_profile=profile,
    )

    suitability = result.dimensions["task_suitability"]
    assert suitability.verdict == Verdict.FAIL
    assert suitability.findings[0].code == "TASK_REQUIRED_LITERAL_MISSING"
    assert result.disposition == AssessmentDisposition.HOLD


def test_task_profile_checks_declared_relationship_as_bounded_literal_order():
    profile = TaskProfile(
        purpose="confirm approval binding",
        required_relationships=[
            RequiredLiteralRelationship(
                first_literal="Contract CN-7",
                second_literal="approved",
                max_characters_between=30,
            )
        ],
    )

    accepted = Assessor().assess(
        source=_artifact(SourceArtifact, "Contract CN-7 approved"),
        candidate=_artifact(CandidateArtifact, "Contract CN-7 was approved."),
        task_profile=profile,
    )
    held = Assessor().assess(
        source=_artifact(SourceArtifact, "Contract CN-7 approved"),
        candidate=_artifact(CandidateArtifact, "approved\n\nContract CN-7"),
        task_profile=profile,
    )

    assert accepted.dimensions["task_suitability"].verdict == Verdict.PASS
    assert held.dimensions["task_suitability"].findings[0].code == "TASK_REQUIRED_RELATIONSHIP_MISSING"
    assert held.disposition == AssessmentDisposition.HOLD


def test_task_profile_requires_a_bounded_deterministic_requirement():
    with pytest.raises(ValidationError, match="at least one literal"):
        TaskProfile(purpose="unbounded usefulness")
