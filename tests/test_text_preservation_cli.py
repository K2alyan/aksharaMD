"""CLI must expose explicit conservative policy and its nonacceptance exit code."""
import json

from click.testing import CliRunner

from aksharamd.cli import main


def test_conservative_policy_reports_omitted_negation(tmp_path):
    source = tmp_path / "source.txt"
    candidate = tmp_path / "candidate.md"
    source.write_text("Do not send this form to the agency.")
    candidate.write_text("Do send this form to the agency.")
    result = CliRunner().invoke(main, ["assess", str(candidate), "--source", str(source),
                                      "--policy", "source-text-preservation-v1", "--json"])
    assert result.exit_code == 2, result.output
    report = json.loads(result.output)
    assert report["policy_id"] == "source-text-preservation-v1"
    assert report["disposition"] == "ABSTAIN"


def test_cli_rejects_unrecognized_policy(tmp_path):
    candidate = tmp_path / "candidate.md"
    candidate.write_text("Content")
    result = CliRunner().invoke(main, ["assess", str(candidate), "--policy", "unknown"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output
