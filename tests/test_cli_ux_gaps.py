"""Tests for the base CLI/UX gaps addressed by Issue #42.

Covers:
- `validate --json` emits a single JSON object on stdout, no prose;
- required fields are present in the payload;
- warning + deduction structures serialize as native types;
- exit code is 0 on validation-passed, 1 on validation-failed;
- omitting `--json` still produces the pre-existing human-readable output;
- `show-manifest` accepts a direct manifest.json path;
- `show-manifest` accepts a per-source output directory;
- `show-manifest` auto-resolves a parent with a single manifest-bearing child;
- `show-manifest` rejects a parent with multiple candidates and lists them;
- `show-manifest` returns a useful error when no manifest is found.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _cli_argv() -> list[str]:
    """Locate the aksharamd CLI to exercise.

    Priority: AKSHARAMD_E2E_BINARY env var → `aksharamd` on PATH → skip.
    Mirrors the discovery logic in tests/test_e2e_installed_wheel.py so
    dev-installs and CI wheel-smoke jobs behave the same.
    """
    binary = os.environ.get("AKSHARAMD_E2E_BINARY")
    if binary:
        return [binary]
    on_path = shutil.which("aksharamd")
    if on_path:
        return [on_path]
    pytest.skip(
        "aksharamd CLI not installed on PATH; set AKSHARAMD_E2E_BINARY to run"
    )


def _cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_cli_argv(), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


# ── validate --json ───────────────────────────────────────────────────────────


def test_validate_json_returns_parseable_json(tmp_path: Path) -> None:
    src = tmp_path / "small.md"
    src.write_text("# heading\n\nbody\n", encoding="utf-8")

    r = _cli("validate", str(src), "--json")
    assert r.returncode == 0
    # stdout must be a single JSON object with a trailing newline.
    payload = json.loads(r.stdout)
    assert isinstance(payload, dict)


def test_validate_json_stdout_contains_only_json(tmp_path: Path) -> None:
    """Prose must not mix into stdout in JSON mode."""
    src = tmp_path / "small.md"
    src.write_text("# heading\n\nbody\n", encoding="utf-8")

    r = _cli("validate", str(src), "--json")
    assert r.returncode == 0
    # Everything on stdout should parse as one JSON blob.
    json.loads(r.stdout)
    # And there must be no Rich markup or Panel borders bleeding through.
    for token in ("[green]", "[red]", "Panel", "──"):
        assert token not in r.stdout, (
            f"stdout in --json mode leaked prose token {token!r}: {r.stdout!r}"
        )


def test_validate_json_required_fields_present(tmp_path: Path) -> None:
    src = tmp_path / "small.md"
    src.write_text("# heading\n\nbody\n", encoding="utf-8")

    r = _cli("validate", str(src), "--json")
    payload = json.loads(r.stdout)
    for field in (
        "success",
        "source",
        "readiness_score",
        "quality_band",
        "scoring_policy_version",
        "deductions",
        "informational",
        "warning_codes",
        "errors",
    ):
        assert field in payload, f"missing field: {field}"


def test_validate_json_warnings_and_deductions_are_lists(tmp_path: Path) -> None:
    """The warning code and deduction lists must serialize as JSON arrays,
    not as Python repr or stringified structures."""
    # Empty markdown → EMPTY_DOCUMENT warning surfaces.
    src = tmp_path / "empty.md"
    src.write_text("", encoding="utf-8")

    r = _cli("validate", str(src), "--json")
    payload = json.loads(r.stdout)
    assert isinstance(payload["warning_codes"], list)
    assert isinstance(payload["deductions"], list)
    assert isinstance(payload["informational"], list)
    assert "EMPTY_DOCUMENT" in payload["warning_codes"]
    # Deduction records should be dicts, not strings.
    for d in payload["deductions"]:
        assert isinstance(d, dict), f"deduction not serialized as dict: {d!r}"


def test_validate_json_failure_returns_nonzero(tmp_path: Path) -> None:
    """Unsupported extension → validation failure → exit 1 with parseable
    payload on stdout."""
    src = tmp_path / "garbage.xyz"
    src.write_text("nothing", encoding="utf-8")

    r = _cli("validate", str(src), "--json")
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["success"] is False
    assert payload["errors"]


def test_validate_human_mode_still_works_without_json(tmp_path: Path) -> None:
    """The pre-existing text output must be preserved when --json is absent."""
    src = tmp_path / "small.md"
    src.write_text("# heading\n\nbody\n", encoding="utf-8")

    r = _cli("validate", str(src))
    assert r.returncode == 0
    # Human mode still prints the classic text; it must NOT be JSON.
    assert "Validation passed" in r.stdout
    try:
        json.loads(r.stdout)
        raise AssertionError("Human-mode output should not parse as JSON")
    except json.JSONDecodeError:
        pass  # correct


# ── show-manifest ─────────────────────────────────────────────────────────────


def _compile_one(src: Path, out_dir: Path) -> Path:
    """Compile `src` to `out_dir` and return the per-source subdir."""
    r = _cli("compile", str(src), "-o", str(out_dir), "--quiet")
    assert r.returncode == 0, f"compile failed:\n{r.stderr}"
    stem = src.stem
    subdir = out_dir / stem
    assert (subdir / "manifest.json").exists()
    return subdir


def test_show_manifest_accepts_direct_manifest_json_path(tmp_path: Path) -> None:
    src = tmp_path / "doc.md"
    src.write_text("# alpha\n", encoding="utf-8")
    sub = _compile_one(src, tmp_path / "out")

    r = _cli("show-manifest", str(sub / "manifest.json"))
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "source_id" in r.stdout


def test_show_manifest_accepts_per_source_output_directory(tmp_path: Path) -> None:
    src = tmp_path / "doc.md"
    src.write_text("# alpha\n", encoding="utf-8")
    sub = _compile_one(src, tmp_path / "out")

    r = _cli("show-manifest", str(sub))
    assert r.returncode == 0
    assert "source_id" in r.stdout


def test_show_manifest_resolves_parent_with_single_child(tmp_path: Path) -> None:
    """The pre-fix pain point: `-o out/`, then `show-manifest out/` should
    now auto-resolve to `out/<stem>/manifest.json`."""
    src = tmp_path / "doc.md"
    src.write_text("# alpha\n", encoding="utf-8")
    out = tmp_path / "out"
    _compile_one(src, out)

    r = _cli("show-manifest", str(out))
    assert r.returncode == 0, (
        "parent auto-resolution failed:\n"
        f"stdout: {r.stdout[:200]!r}\nstderr: {r.stderr[:200]!r}"
    )
    assert "source_id" in r.stdout


def test_show_manifest_rejects_parent_with_multiple_candidates(tmp_path: Path) -> None:
    src1 = tmp_path / "one.md"
    src1.write_text("# one\n", encoding="utf-8")
    src2 = tmp_path / "two.md"
    src2.write_text("# two\n", encoding="utf-8")
    out = tmp_path / "out"
    _compile_one(src1, out)
    _compile_one(src2, out)

    r = _cli("show-manifest", str(out))
    assert r.returncode == 1
    # stdout (Rich console.print writes there) should list the candidates.
    combined = r.stdout + r.stderr
    assert "multiple" in combined.lower()
    assert "one" in combined and "two" in combined


def test_show_manifest_reports_missing_manifest(tmp_path: Path) -> None:
    empty = tmp_path / "empty_dir"
    empty.mkdir()

    r = _cli("show-manifest", str(empty))
    assert r.returncode == 1
    combined = r.stdout + r.stderr
    assert "no manifest" in combined.lower() or "not found" in combined.lower()


def test_show_manifest_rejects_non_manifest_file(tmp_path: Path) -> None:
    junk = tmp_path / "junk.txt"
    junk.write_text("hi", encoding="utf-8")

    r = _cli("show-manifest", str(junk))
    assert r.returncode == 1
    combined = r.stdout + r.stderr
    assert "not a manifest" in combined.lower()
