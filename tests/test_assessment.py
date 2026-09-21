import hashlib
import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from aksharamd.assessment import (
    AssessmentDisposition,
    AssessmentResult,
    Assessor,
    CandidateArtifact,
    SourceArtifact,
)
from aksharamd.cli import main


def _artifact(cls, text: str):
    data = text.encode()
    return cls(content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data),
               media_type="text/markdown", data=data)


def test_missing_critical_number_holds_candidate():
    result = Assessor().assess(
        source=_artifact(SourceArtifact, "Balance: $1200"),
        candidate=_artifact(CandidateArtifact, "Balance: pending"),
    )
    assert result.disposition == AssessmentDisposition.HOLD
    assert result.dimensions["conversion_fidelity"].findings[0].code == "CRITICAL_LITERAL_MISSING"


def test_output_only_assessment_abstains():
    result = Assessor().assess(candidate=_artifact(CandidateArtifact, "A usable candidate."))
    assert result.disposition == AssessmentDisposition.ABSTAIN


def test_source_identity_mismatch_holds_candidate():
    source = _artifact(SourceArtifact, "Value: 12")
    candidate = _artifact(CandidateArtifact, "Value: 12")
    candidate.original_source_hash = "0" * 64
    result = Assessor().assess(source=source, candidate=candidate)
    assert result.disposition == AssessmentDisposition.HOLD
    assert result.dimensions["conversion_fidelity"].findings[0].code == "SOURCE_IDENTITY_MISMATCH"


def test_clean_text_candidate_is_accepted():
    result = Assessor().assess(
        source=_artifact(SourceArtifact, "Due 2026-09-07: 12 kg"),
        candidate=_artifact(CandidateArtifact, "Due 2026-09-07: 12 kg"),
    )
    assert result.disposition == AssessmentDisposition.ACCEPT
    assert result.schema_version == "1.1"
    assert result.task_profile_sha256 == "none"


def test_legacy_assessment_schema_requires_regeneration():
    result = Assessor().assess(
        source=_artifact(SourceArtifact, "Invoice 42: $18"),
        candidate=_artifact(CandidateArtifact, "Invoice 42: $18"),
    )
    legacy = result.model_dump(mode="json")
    legacy["schema_version"] = "1.0"
    legacy.pop("task_profile_sha256")

    with pytest.raises(ValidationError, match="lacks task-profile provenance"):
        AssessmentResult.model_validate(legacy)


def test_assess_cli_emits_machine_readable_report(tmp_path):
    source = tmp_path / "source.md"
    candidate = tmp_path / "candidate.md"
    source.write_text("Invoice 42: $18", encoding="utf-8")
    candidate.write_text("Invoice 42: $18", encoding="utf-8")
    result = CliRunner().invoke(main, ["assess", str(candidate), "--source", str(source), "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["schema_version"] == "1.1"
    assert report["disposition"] == "ACCEPT"
