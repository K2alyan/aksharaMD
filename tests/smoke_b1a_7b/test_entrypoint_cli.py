"""CLI admission-check tests.

Exercises the three-barrier admission — env sentinel, explicit
``--execute-frozen-smoke`` flag, and ``--run-dir <empty>``. No real
smoke ever runs from these tests; ``main()`` is guarded by
``pragma: no cover`` because the real-backend body requires a real
authorization the test suite deliberately does not provide.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.entrypoint import (
    AUTHORIZATION_ENV_VAR,
    AUTHORIZATION_TOKEN,
    EXECUTE_FLAG,
    InvalidCliArgumentsError,
    RunDirectoryError,
    UnauthorizedInvocationError,
    check_execute_mode_enabled,
    emit_operational,
    parse_cli,
)

# ---------------------------------------------------------------------------
# Authorization sentinel.


def test_missing_env_sentinel_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(AUTHORIZATION_ENV_VAR, raising=False)
    with pytest.raises(UnauthorizedInvocationError, match="AKSHARAMD_SMOKE_AUTHORIZED"):
        check_execute_mode_enabled([EXECUTE_FLAG, "--run-dir", str(tmp_path)])


def test_wrong_env_sentinel_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, "not-the-token")
    with pytest.raises(UnauthorizedInvocationError, match="separate authorization"):
        check_execute_mode_enabled([EXECUTE_FLAG, "--run-dir", str(tmp_path)])


# ---------------------------------------------------------------------------
# Explicit flag.


def test_missing_execute_flag_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    with pytest.raises(UnauthorizedInvocationError, match="--execute-frozen-smoke"):
        check_execute_mode_enabled(["--run-dir", str(tmp_path)])


def test_unknown_flag_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    with pytest.raises(InvalidCliArgumentsError):
        check_execute_mode_enabled([
            EXECUTE_FLAG,
            "--run-dir", str(tmp_path),
            "--force-execute-anyway",  # not a real flag
        ])


def test_document_ids_cannot_be_supplied_on_cli(monkeypatch, tmp_path: Path) -> None:
    """Regression test: any attempt to override the frozen document
    IDs from the CLI is refused by argparse's unknown-flag handling.
    """
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    for attempt in (
        ["--canonical-id", "PMC1234.1"],
        ["--doc-id", "something"],
        ["--corpus", "pmc_oa", "--canonical-id", "X"],
        ["--override-smoke-documents", "X,Y,Z"],
    ):
        with pytest.raises(InvalidCliArgumentsError):
            check_execute_mode_enabled([
                EXECUTE_FLAG, "--run-dir", str(tmp_path), *attempt,
            ])


# ---------------------------------------------------------------------------
# Run-dir semantics.


def test_run_dir_missing_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    missing = tmp_path / "does_not_exist"
    with pytest.raises(RunDirectoryError, match="does not exist"):
        check_execute_mode_enabled([EXECUTE_FLAG, "--run-dir", str(missing)])


def test_run_dir_not_a_directory_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    fake_file = tmp_path / "not_a_dir"
    fake_file.write_text("x", encoding="utf-8")
    with pytest.raises(RunDirectoryError, match="not a directory"):
        check_execute_mode_enabled([EXECUTE_FLAG, "--run-dir", str(fake_file)])


def test_run_dir_non_empty_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    (tmp_path / "prior_output.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RunDirectoryError, match="not empty"):
        check_execute_mode_enabled([EXECUTE_FLAG, "--run-dir", str(tmp_path)])


def test_run_dir_required_when_execute_flag_present(monkeypatch) -> None:
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    with pytest.raises(RunDirectoryError, match="required"):
        check_execute_mode_enabled([EXECUTE_FLAG])


# ---------------------------------------------------------------------------
# Happy admission — but nothing executes.


def test_all_three_barriers_pass_returns_run_dir(monkeypatch, tmp_path: Path) -> None:
    """When env sentinel + flag + empty run-dir are all satisfied, the
    admission check returns the run-dir path. It does NOT trigger any
    real backend; the caller (main()) does that behind a further
    ``# pragma: no cover`` guard.
    """
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, AUTHORIZATION_TOKEN)
    empty_dir = tmp_path / "run_XYZ"
    empty_dir.mkdir()
    result = check_execute_mode_enabled([EXECUTE_FLAG, "--run-dir", str(empty_dir)])
    assert result == empty_dir


# ---------------------------------------------------------------------------
# parse_cli — pure parsing helper, no admission checks.


def test_parse_cli_bare_produces_no_execute() -> None:
    args = parse_cli([])
    assert args.execute_frozen_smoke is False
    assert args.run_dir is None


def test_parse_cli_execute_flag_only() -> None:
    args = parse_cli([EXECUTE_FLAG])
    assert args.execute_frozen_smoke is True
    assert args.run_dir is None


def test_parse_cli_help_raises_invalid_cli() -> None:
    with pytest.raises(InvalidCliArgumentsError):
        parse_cli(["--help"])


# ---------------------------------------------------------------------------
# Operational-only console output.


def test_emit_operational_allows_operational_fields(capsys) -> None:
    emit_operational(
        phase="pair_1_of_12",
        pair_number=1,
        pair_id="0" * 16,
        parser_id="marker",
        exit_status="EXECUTED",
        wall_clock_seconds=123.4,
    )
    out = capsys.readouterr().out
    assert "phase=" in out
    assert "pair_id=" in out
    assert "exit_status=" in out


def test_emit_operational_refuses_markdown_field() -> None:
    with pytest.raises(ValueError, match="raw_output"):
        emit_operational(raw_output="# secret markdown", pair_number=1)


def test_emit_operational_refuses_score_field() -> None:
    with pytest.raises(ValueError, match="aksharamd_readiness_score"):
        emit_operational(aksharamd_readiness_score=0.87)


def test_emit_operational_refuses_warning_codes_field() -> None:
    with pytest.raises(ValueError, match="warning_codes"):
        emit_operational(warning_codes=["W_DROPPED_CONTENT"])


def test_emit_operational_refuses_q123_answers() -> None:
    with pytest.raises(ValueError, match="q1_answer"):
        emit_operational(q1_answer="yes")


def test_emit_operational_refuses_cross_parser_comparison() -> None:
    with pytest.raises(ValueError, match="parser_quality_comparison"):
        emit_operational(parser_quality_comparison={"marker": 0.9})
