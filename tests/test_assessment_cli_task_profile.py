"""CLI coverage for declared task-profile assessment input."""

from __future__ import annotations

import json

from click.testing import CliRunner

from aksharamd.cli import main


def _write_artifacts(tmp_path):
    source = tmp_path / "source.md"
    candidate = tmp_path / "candidate.md"
    source.write_text("Invoice 42: $18", encoding="utf-8")
    candidate.write_text("Invoice 42: $18", encoding="utf-8")
    return source, candidate


def test_assess_cli_loads_versioned_task_profile_and_emits_suitability(tmp_path):
    source, candidate = _write_artifacts(tmp_path)
    profile = tmp_path / "task-profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "purpose": "verify invoice payment amount",
                "required_literals": ["$18"],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        ["assess", str(candidate), "--source", str(source), "--task-profile", str(profile), "--json"],
    )

    assert result.exit_code == 0, result.output
    suitability = json.loads(result.output)["dimensions"]["task_suitability"]
    assert suitability["status"] == "observed"
    assert suitability["verdict"] == "pass"
    assert suitability["evidence"][0]["details"]["task_profile_schema_version"] == "1.0"


def test_assess_cli_rejects_invalid_task_profile_before_assessment(tmp_path):
    source, candidate = _write_artifacts(tmp_path)
    profile = tmp_path / "task-profile.json"
    profile.write_text('{"purpose": "no requirements"}', encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["assess", str(candidate), "--source", str(source), "--task-profile", str(profile), "--json"],
    )

    assert result.exit_code != 0
    assert "Invalid task profile" in result.output
