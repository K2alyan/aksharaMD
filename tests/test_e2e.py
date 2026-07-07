from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from aksharamd.cli import main


@pytest.fixture
def runner():
    return CliRunner()


# ── formats command ───────────────────────────────────────────────────────────

def test_formats_exits_zero(runner):
    result = runner.invoke(main, ["formats"])
    assert result.exit_code == 0


# ── compile: error paths ──────────────────────────────────────────────────────

def test_compile_nonexistent_file_exits_nonzero(runner):
    result = runner.invoke(main, ["compile", "/nonexistent/no_such_file.txt"])
    assert result.exit_code != 0


# ── compile: text file ────────────────────────────────────────────────────────

def test_compile_text_produces_manifest(runner, tmp_path):
    src = tmp_path / "sample.txt"
    src.write_text(
        "Hello world.\n\nThis is a test document with multiple paragraphs.\n\nThird paragraph here.",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    result = runner.invoke(main, ["compile", str(src), "-o", out_dir])
    assert result.exit_code == 0, result.output

    manifest_path = Path(out_dir) / "sample" / "manifest.json"
    assert manifest_path.exists(), f"Expected manifest at {manifest_path}"
    data = json.loads(manifest_path.read_text())
    assert data["pages"] >= 1
    assert data["file_type"] == "txt"


# ── compile: markdown file ────────────────────────────────────────────────────

def test_compile_markdown_produces_document(runner, tmp_path):
    src = tmp_path / "readme.md"
    src.write_text(
        "# Title\n\nSome content.\n\n## Section\n\nMore text here.\n",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    result = runner.invoke(main, ["compile", str(src), "-o", out_dir])
    assert result.exit_code == 0, result.output

    out = Path(out_dir) / "readme"
    assert (out / "manifest.json").exists()
    doc_md = out / "document.md"
    assert doc_md.exists()
    assert "Title" in doc_md.read_text()


# ── compile: HTML file ────────────────────────────────────────────────────────

def test_compile_html_produces_manifest(runner, tmp_path):
    src = tmp_path / "page.html"
    src.write_text(
        "<html><body><h1>Hello</h1><p>World.</p></body></html>",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    result = runner.invoke(main, ["compile", str(src), "-o", out_dir])
    assert result.exit_code == 0, result.output

    manifest_path = Path(out_dir) / "page" / "manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert data["file_type"] == "html"


# ── compile: --json flag ─────────────────────────────────────────────────────

def test_compile_json_output_is_parseable(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Hello world.\n\nThis is a test document with multiple paragraphs.\n\nThird paragraph here.",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    result = runner.invoke(main, ["compile", str(src), "-o", out_dir, "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["success"] is True
    assert data["source"].endswith("doc.txt") or "doc" in data["source"]
    assert isinstance(data["readiness_score"], int)
    assert data["quality_band"] in ("HIGH", "OK", "RISKY", "POOR")
    assert isinstance(data["warning_codes"], list)
    assert isinstance(data["errors"], list)
    assert isinstance(data["chunks"], int)
    assert isinstance(data["pages"], int)
    assert isinstance(data["optimized_tokens"], int)
    assert isinstance(data["elapsed_seconds"], float)


def test_compile_json_output_has_no_rich_markup(runner, tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# Title\n\nSome content here.\n", encoding="utf-8")
    out_dir = str(tmp_path / "out")

    result = runner.invoke(main, ["compile", str(src), "-o", out_dir, "--json"])

    # The raw output must be valid JSON with no Rich ANSI or markup
    stripped = result.output.strip()
    data = json.loads(stripped)  # would raise if not valid JSON
    assert "bold" not in stripped
    assert "\x1b[" not in stripped  # no ANSI escape codes
    assert data is not None


# ── compile: --min-readiness-score ────────────────────────────────────────────

def test_compile_min_readiness_score_passes_when_above_threshold(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Hello world.\n\nThis is a test document with multiple paragraphs.\n\nThird paragraph here.",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    # threshold 0 — any document should pass
    result = runner.invoke(main, ["compile", str(src), "-o", out_dir, "--min-readiness-score", "0"])
    assert result.exit_code == 0, result.output


def test_compile_min_readiness_score_fails_when_below_threshold(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Hello world.\n\nThis is a test document with multiple paragraphs.\n\nThird paragraph here.",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    # threshold 101 — no document can score above 100
    result = runner.invoke(main, ["compile", str(src), "-o", out_dir, "--min-readiness-score", "101"])
    assert result.exit_code != 0
    # Output files are still written even when threshold is not met
    manifest_path = Path(out_dir) / "doc" / "manifest.json"
    assert manifest_path.exists(), "Output files should be written even when readiness gate fails"


def test_compile_json_with_min_readiness_score_passes(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Hello world.\n\nThis is a test document with multiple paragraphs.\n\nThird paragraph here.",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    result = runner.invoke(
        main, ["compile", str(src), "-o", out_dir, "--json", "--min-readiness-score", "0"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["success"] is True


def test_compile_json_with_min_readiness_score_fails(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Hello world.\n\nThis is a test document with multiple paragraphs.\n\nThird paragraph here.",
        encoding="utf-8",
    )
    out_dir = str(tmp_path / "out")

    # threshold 101 — no document can score above 100
    result = runner.invoke(
        main, ["compile", str(src), "-o", out_dir, "--json", "--min-readiness-score", "101"]
    )
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["success"] is False
    assert isinstance(data["readiness_score"], int)


# ── validate command ──────────────────────────────────────────────────────────

def test_validate_valid_file_exits_zero(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text("A valid document with some content.", encoding="utf-8")

    result = runner.invoke(main, ["validate", str(src)])
    assert result.exit_code == 0


def test_validate_nonexistent_file_exits_nonzero(runner):
    result = runner.invoke(main, ["validate", "/nonexistent/path.txt"])
    assert result.exit_code != 0
