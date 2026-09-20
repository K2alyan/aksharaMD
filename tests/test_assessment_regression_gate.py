import hashlib
import json

import pytest
from click.testing import CliRunner

from aksharamd.assessment import Assessor, CandidateArtifact, SourceArtifact
from aksharamd.cli import main


def _artifact(kind, text, *, source_hash=None):
    data = text.encode()
    values = {
        "content_hash": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "media_type": "text/markdown",
        "data": data,
    }
    if source_hash is not None:
        values["original_source_hash"] = source_hash
    return kind(**values)


def _assessment_payload(source_text, candidate_text, *, policy_id="general-ingestion-v2"):
    source = _artifact(SourceArtifact, source_text)
    candidate = _artifact(CandidateArtifact, candidate_text, source_hash=source.content_hash)
    result = Assessor().assess(source=source, candidate=candidate, policy_id=policy_id)
    return result.model_dump(mode="json")


def _write_assessment(path, source_text, candidate_text):
    path.write_text(json.dumps(_assessment_payload(source_text, candidate_text)), encoding="utf-8")


def _write_manifest(tmp_path, *, policy=None):
    payload = {
        "schema_version": "1.0",
        "comparisons": [{
            "id": "invoice-parser",
            "baseline": "baseline.json",
            "candidate": "candidate.json",
        }],
    }
    if policy is not None:
        payload["policy"] = policy
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_gate_passes_equivalent_assessment_artifacts(tmp_path):
    _write_assessment(tmp_path / "baseline.json", "Invoice 42: $18", "Invoice 42: $18")
    _write_assessment(tmp_path / "candidate.json", "Invoice 42: $18", "Invoice 42: $18")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "PASS"
    assert report["summary"] == {"comparisons": 1, "denied": 0, "passed": 1}
    assert len(report["manifest_sha256"]) == 64
    assert "QA prediction" in report["scope"]


@pytest.mark.parametrize("policy_id", [
    "general-ingestion-v1",
    "general-ingestion-v2",
    "source-text-preservation-v1",
])
def test_gate_strict_replay_supports_every_version_one_policy(tmp_path, policy_id):
    payload = _assessment_payload("Invoice 42: $18", "Invoice 42: $18", policy_id=policy_id)
    for name in ("baseline.json", "candidate.json"):
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 0, result.output


def test_gate_denies_new_warning_and_failed_disposition(tmp_path):
    _write_assessment(tmp_path / "baseline.json", "Invoice 42: $18", "Invoice 42: $18")
    _write_assessment(tmp_path / "candidate.json", "Invoice 42: $18", "Invoice pending")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 2
    report = json.loads(result.output)
    assert report["status"] == "DENY"
    codes = {failure["code"] for failure in report["results"][0]["failures"]}
    assert codes == {"REQUIRED_DISPOSITION_NOT_MET", "NEW_WARNING_NOT_ALLOWED"}
    assert "CRITICAL_LITERAL_MISSING" in report["results"][0]["new_warning_codes"]


def test_gate_allow_and_deny_warning_rules_are_policy_defined(tmp_path):
    _write_assessment(tmp_path / "baseline.json", "Invoice 42: $18", "Invoice 42: $18")
    _write_assessment(tmp_path / "candidate.json", "Invoice 42: $18", "Invoice pending")
    warning = "CRITICAL_LITERAL_MISSING"
    manifest = _write_manifest(tmp_path, policy={
        "required_disposition": "HOLD",
        "allow_new_warning_codes": [warning, "SOURCE_TEXT_PRESERVATION_UNPROVEN"],
        "deny_warning_codes": [],
    })
    allowed = CliRunner().invoke(main, ["gate", str(manifest), "--json"])
    assert allowed.exit_code == 0, allowed.output

    payload = json.loads(manifest.read_text())
    payload["policy"]["allow_new_warning_codes"] = []
    payload["policy"]["deny_warning_codes"] = [warning]
    payload["policy"]["deny_new_warnings"] = False
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    denied = CliRunner().invoke(main, ["gate", str(manifest), "--json"])
    assert denied.exit_code == 2
    assert json.loads(denied.output)["results"][0]["failures"] == [{
        "code": "DENIED_WARNING_PRESENT", "warning_codes": [warning]
    }]


def test_gate_rejects_malformed_manifest_with_input_exit_code(tmp_path):
    manifest = tmp_path / "gate.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "comparisons": [],
        "unexpected": True,
    }), encoding="utf-8")

    result = CliRunner().invoke(main, ["gate", str(manifest), "--json"])

    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["status"] == "ERROR"
    assert report["error"]["code"] == "INVALID_INPUT"
    assert "Invalid gate manifest" in report["error"]["message"]


def test_gate_rejects_internally_inconsistent_binding_artifact(tmp_path):
    _write_assessment(tmp_path / "baseline.json", "Invoice 42: $18", "Invoice 42: $18")
    direct = json.loads((tmp_path / "baseline.json").read_text())
    wrapped = {
        "binding_schema_version": "1.0",
        "assessment": direct,
        "source": {
            "logical_id": "invoice-source",
            "capture_id": direct["source_hash"],
            "byte_size": 15,
            "media_type": "text/markdown",
            "storage_reference": "invoice.md",
        },
        "candidate": {
            "logical_id": "invoice-candidate",
            "content_hash": direct["candidate_hash"],
            "byte_size": 15,
            "media_type": "text/markdown",
            "storage_reference": "document.md",
            "original_source_hash": "0" * 64,
            "parser_name": None,
            "parser_version": None,
            "parser_configuration_id": None,
        },
    }
    (tmp_path / "candidate.json").write_text(json.dumps(wrapped), encoding="utf-8")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 1
    assert "inconsistent source provenance" in json.loads(result.output)["error"]["message"]


def test_gate_accepts_strict_compiler_binding_artifacts(tmp_path):
    text = "Invoice 42: $18"
    direct = _assessment_payload(text, text)
    wrapped = {
        "binding_schema_version": "1.0",
        "assessment": direct,
        "source": {
            "logical_id": "invoice-source",
            "capture_id": direct["source_hash"],
            "byte_size": len(text.encode()),
            "media_type": "text/markdown",
            "storage_reference": "invoice.md",
        },
        "candidate": {
            "logical_id": "invoice-candidate",
            "content_hash": direct["candidate_hash"],
            "byte_size": len(text.encode()),
            "media_type": "text/markdown",
            "storage_reference": "document.md",
            "original_source_hash": direct["source_hash"],
            "parser_name": "parser",
            "parser_version": "1",
            "parser_configuration_id": None,
        },
    }
    for name in ("baseline.json", "candidate.json"):
        (tmp_path / name).write_text(json.dumps(wrapped), encoding="utf-8")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("corruption", [
    "empty_dimensions",
    "invalid_hash",
    "unexpected_nested_field",
    "unsupported_policy",
    "inconsistent_disposition",
])
def test_gate_rejects_malformed_assessment_results(tmp_path, corruption):
    source = "Invoice 42: $18"
    _write_assessment(tmp_path / "baseline.json", source, source)
    payload = _assessment_payload(source, source)
    if corruption == "empty_dimensions":
        payload["dimensions"] = {}
    elif corruption == "invalid_hash":
        payload["candidate_hash"] = "A" * 64
    elif corruption == "unexpected_nested_field":
        payload["dimensions"]["conversion_fidelity"]["evidence"][0]["unexpected"] = True
    elif corruption == "unsupported_policy":
        payload["policy_id"] = "general-ingestion-future"
    else:
        payload["disposition"] = "HOLD"
        payload["next_action"] = "REVIEW"
    (tmp_path / "candidate.json").write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["status"] == "ERROR"
    assert report["error"]["code"] == "INVALID_INPUT"
    assert "Invalid assessment artifact" in report["error"]["message"]


def test_gate_missing_manifest_is_machine_readable_input_error(tmp_path):
    result = CliRunner().invoke(main, ["gate", str(tmp_path / "missing.json"), "--json"])

    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["status"] == "ERROR"
    assert report["error"]["code"] == "INVALID_INPUT"


def test_gate_denies_source_provenance_mismatch(tmp_path):
    _write_assessment(tmp_path / "baseline.json", "Invoice 42: $18", "Invoice 42: $18")
    _write_assessment(tmp_path / "candidate.json", "Other invoice 42: $18", "Other invoice 42: $18")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path)), "--json"])

    assert result.exit_code == 2
    report = json.loads(result.output)
    mismatches = [failure for failure in report["results"][0]["failures"]
                  if failure["code"] == "PROVENANCE_INVARIANT_MISMATCH"]
    assert mismatches == [{
        "code": "PROVENANCE_INVARIANT_MISMATCH",
        "invariant": "source_hash",
        "baseline": mismatches[0]["baseline"],
        "candidate": mismatches[0]["candidate"],
    }]
    assert mismatches[0]["baseline"] != mismatches[0]["candidate"]


def test_gate_human_report_is_concise(tmp_path):
    _write_assessment(tmp_path / "baseline.json", "Invoice 42: $18", "Invoice 42: $18")
    _write_assessment(tmp_path / "candidate.json", "Invoice 42: $18", "Invoice 42: $18")

    result = CliRunner().invoke(main, ["gate", str(_write_manifest(tmp_path))])

    assert result.exit_code == 0
    assert "Gate PASS: 1/1 passed, 0 denied" in result.output
    assert "PASS  invoice-parser" in result.output
