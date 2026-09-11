"""Regression tests for the opt-in source-grounded compile gate."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aksharamd.cli import main


def test_compile_assessment_gate_accepts_and_emits_disposition(tmp_path):
    source = tmp_path / "invoice.md"
    source.write_text("# Invoice 42\n\nBalance: $18", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "compile", str(source), "-o", str(tmp_path / "out"), "--json",
            "--require-assessment-accept",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["success"] is True
    gate = payload["assessment_gate"]
    assert gate["required_disposition"] == "ACCEPT"
    assert gate["disposition"] == "ACCEPT"
    assert gate["next_action"] == "NONE"
    assert gate["activated"] is True
    assert gate["error"] is None
    assert gate["activation_error"] is None
    assert gate["final_output_dir"] == str(tmp_path / "out" / "invoice")
    assert gate["assessment_path"] == str(tmp_path / "out" / "invoice" / "quality_assessment.json")
    assert (tmp_path / "out" / "invoice" / "document.md").is_file()


def test_compile_assessment_gate_returns_two_for_hold(tmp_path):
    source = tmp_path / "preview.txt"
    source.write_text(("ordinary text " * 1000 + "\n\n") * 10, encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "compile", str(source), "-o", str(tmp_path / "out"), "--json",
            "--require-assessment-accept",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["assessment_gate"]["disposition"] == "HOLD"
    assert payload["assessment_gate"]["next_action"] == "REVIEW"
    assert payload["assessment_gate"]["activated"] is False
    assert not (tmp_path / "out" / "preview").exists()
    assert (Path(payload["assessment_gate"]["staging_output_dir"]) / "document.md").is_file()


def test_compile_without_assessment_gate_keeps_legacy_json_shape(tmp_path):
    source = tmp_path / "plain.md"
    source.write_text("# Plain\n\nValue 7", encoding="utf-8")

    result = CliRunner().invoke(main, ["compile", str(source), "-o", str(tmp_path / "out"), "--json"])

    assert result.exit_code == 0, result.output
    assert "assessment_gate" not in json.loads(result.output)


def test_compile_assessment_gate_never_replaces_existing_final_output(tmp_path):
    source = tmp_path / "invoice.md"
    source.write_text("# Invoice 42\n\nBalance: $18", encoding="utf-8")
    final_output = tmp_path / "out" / "invoice"
    final_output.mkdir(parents=True)
    sentinel = final_output / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "compile", str(source), "-o", str(tmp_path / "out"), "--json",
            "--require-assessment-accept",
        ],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["assessment_gate"]["disposition"] == "ACCEPT"
    assert payload["assessment_gate"]["activated"] is False
    assert "refusing to replace existing final output" in payload["assessment_gate"]["activation_error"]
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_compile_assessment_gate_uses_task_profile_before_promotion(tmp_path):
    source = tmp_path / "invoice.md"
    source.write_text("# Invoice 42\n\nBalance: $18", encoding="utf-8")
    profile = tmp_path / "task-profile.json"
    profile.write_text(
        json.dumps({"purpose": "route account", "required_literals": ["Account 77"]}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "compile", str(source), "-o", str(tmp_path / "out"), "--json",
            "--require-assessment-accept", "--task-profile", str(profile),
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["assessment_gate"]["disposition"] == "HOLD"
    assert payload["assessment_gate"]["activated"] is False
    assessment = json.loads(
        (Path(payload["assessment_gate"]["staging_output_dir"]) / "quality_assessment.json").read_text(
            encoding="utf-8"
        )
    )
    assert assessment["task_profile"]["purpose"] == "route account"
    assert assessment["assessment"]["dimensions"]["task_suitability"]["verdict"] == "fail"


def test_compile_task_profile_requires_opt_in_assessment_gate(tmp_path):
    source = tmp_path / "invoice.md"
    source.write_text("# Invoice 42", encoding="utf-8")
    profile = tmp_path / "task-profile.json"
    profile.write_text(
        json.dumps({"purpose": "locate invoice", "required_literals": ["Invoice 42"]}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["compile", str(source), "--task-profile", str(profile)])

    assert result.exit_code == 2
    assert "--task-profile requires --require-assessment-accept" in result.output
