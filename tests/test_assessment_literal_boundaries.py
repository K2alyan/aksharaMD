"""Regression coverage for complete critical-literal preservation."""
import hashlib

import pytest

from aksharamd.assessment import (
    AssessmentDisposition,
    Assessor,
    CandidateArtifact,
    SourceArtifact,
)


def _artifact(cls, text):
    data = text.encode()
    return cls(
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        media_type="text/markdown",
        data=data,
    )


@pytest.mark.parametrize("source,candidate", [
    ("Balance: 12", "Balance: 312"),
    ("Balance: 12", "Balance: 120"),
    ("Balance: 12", "Balance: 12.5"),
    ("Balance: 12", "Account AB12"),
    ("Invoice AB12", "Invoice AB123"),
    ("Invoice AB12", "Invoice CAB12"),
    ("Due: 2026-09-07", "Due: 2026-07-09"),
    ("Due: 2026-09-07", "Year 2026, month 09, day 07"),
    ("Weight: 12 kg", "Weight: 312 kg"),
    ("Weight: 12 kg", "Weight: 12 km"),
    ("Weight: 12 kg", "Weight: 12"),
])
def test_changed_or_fragmented_critical_literal_holds(source, candidate):
    result = Assessor().assess(
        source=_artifact(SourceArtifact, source),
        candidate=_artifact(CandidateArtifact, candidate),
    )
    assert result.disposition == AssessmentDisposition.HOLD
    assert result.dimensions["conversion_fidelity"].findings[0].code == "CRITICAL_LITERAL_MISSING"


@pytest.mark.parametrize("literal", ["12", "12.5", "AB12", "2026-09-07", "12 kg", "12 USD", "12%"])
def test_complete_critical_literal_survives_markdown_formatting(literal):
    result = Assessor().assess(
        source=_artifact(SourceArtifact, f"Value: {literal}"),
        candidate=_artifact(CandidateArtifact, f"# Value\n\n**{literal}**"),
    )
    # Literal checks pass, but changed wording/field layout is not preservation proof.
    assert result.disposition == AssessmentDisposition.ABSTAIN
    evidence = result.dimensions["conversion_fidelity"].evidence[0]
    assert evidence.measurement == evidence.denominator == 1
