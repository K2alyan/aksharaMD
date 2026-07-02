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


# ── validate command ──────────────────────────────────────────────────────────

def test_validate_valid_file_exits_zero(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text("A valid document with some content.", encoding="utf-8")

    result = runner.invoke(main, ["validate", str(src)])
    assert result.exit_code == 0


def test_validate_nonexistent_file_exits_nonzero(runner):
    result = runner.invoke(main, ["validate", "/nonexistent/path.txt"])
    assert result.exit_code != 0
