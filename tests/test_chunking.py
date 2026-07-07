"""Tests for configurable chunk size and overlap (issue #22)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from aksharamd.cli import compile as compile_cmd
from aksharamd.compiler import Compiler


@pytest.fixture
def long_md(tmp_path: Path) -> Path:
    """Markdown with enough content to produce multiple chunks at small chunk_size."""
    lines = ["# Section\n"]
    for i in range(30):
        lines.append(f"Paragraph {i}: " + ("word " * 20) + "\n")
    doc = tmp_path / "long.md"
    doc.write_text("\n".join(lines), encoding="utf-8")
    return doc


@pytest.fixture
def short_md(tmp_path: Path) -> Path:
    doc = tmp_path / "short.md"
    doc.write_text(textwrap.dedent("""
        # Title

        A short document with minimal content.
    """), encoding="utf-8")
    return doc


# ── Compiler Python API ────────────────────────────────────────────────────────

def test_default_chunk_size_unchanged(short_md: Path, tmp_path: Path):
    """Default chunk_size=512 and chunk_overlap=0 produce valid output."""
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(short_md))[1]
    assert ctx.manifest is not None
    assert ctx.manifest.chunk_size == 512
    assert ctx.manifest.chunk_overlap == 0
    assert ctx.manifest.chunks >= 1


def test_custom_chunk_size_produces_more_chunks(long_md: Path, tmp_path: Path):
    """A smaller chunk_size produces more chunks than the default."""
    ctx_default = Compiler(output_dir=str(tmp_path / "default")).compile_to_string(str(long_md))[1]
    ctx_small = Compiler(
        output_dir=str(tmp_path / "small"), chunk_size=64
    ).compile_to_string(str(long_md))[1]

    assert ctx_small.manifest.chunks > ctx_default.manifest.chunks
    assert ctx_small.manifest.chunk_size == 64


def test_custom_chunk_overlap_recorded_in_manifest(long_md: Path, tmp_path: Path):
    """chunk_overlap is recorded in the manifest."""
    ctx = Compiler(
        output_dir=str(tmp_path / "out"), chunk_size=128, chunk_overlap=32
    ).compile_to_string(str(long_md))[1]

    assert ctx.manifest.chunk_overlap == 32
    assert ctx.manifest.chunk_size == 128


def test_overlap_content_appears_in_consecutive_chunks(long_md: Path, tmp_path: Path):
    """When overlap > 0, the tail of chunk N appears at the start of chunk N+1.

    chunk_overlap=30 is chosen to exceed the per-block token count (~23 tokens),
    ensuring at least one block is carried into the next chunk as overlap.
    """
    ctx = Compiler(
        output_dir=str(tmp_path / "out"), chunk_size=100, chunk_overlap=30
    ).compile_to_string(str(long_md))[1]

    chunks = ctx.chunks
    if len(chunks) < 2:
        pytest.skip("document did not produce enough chunks at this size")

    # At least one consecutive pair should share block IDs (the overlap blocks).
    found_overlap = False
    for i in range(len(chunks) - 1):
        shared = set(chunks[i].block_ids) & set(chunks[i + 1].block_ids)
        if shared:
            found_overlap = True
            break
    assert found_overlap, "expected at least one pair of chunks to share overlap block IDs"


def test_invalid_overlap_ge_chunk_size_raises():
    """chunk_overlap >= chunk_size must raise ValueError."""
    with pytest.raises(ValueError, match="chunk_overlap"):
        Compiler(chunk_size=128, chunk_overlap=128)

    with pytest.raises(ValueError, match="chunk_overlap"):
        Compiler(chunk_size=128, chunk_overlap=200)


def test_invalid_chunk_size_zero_raises():
    with pytest.raises(ValueError, match="chunk_size must be positive"):
        Compiler(chunk_size=0)


def test_invalid_chunk_size_negative_raises():
    with pytest.raises(ValueError, match="chunk_size must be positive"):
        Compiler(chunk_size=-1)


def test_invalid_overlap_negative_raises():
    with pytest.raises(ValueError, match="chunk_overlap must be >= 0"):
        Compiler(chunk_size=512, chunk_overlap=-1)


# ── CLI ────────────────────────────────────────────────────────────────────────

def test_cli_default_behavior_unchanged(short_md: Path, tmp_path: Path):
    """CLI with no chunk flags produces the same output as before."""
    runner = CliRunner()
    result = runner.invoke(compile_cmd, [str(short_md), "-o", str(tmp_path / "out"), "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.output)
    assert data["chunk_size"] == 512
    assert data["chunk_overlap"] == 0


def test_cli_chunk_size_option(long_md: Path, tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        compile_cmd,
        [str(long_md), "-o", str(tmp_path / "out"), "--chunk-size", "64", "--json"],
    )
    assert result.exit_code == 0
    import json
    data = json.loads(result.output)
    assert data["chunk_size"] == 64


def test_cli_chunk_overlap_option(long_md: Path, tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        compile_cmd,
        [str(long_md), "-o", str(tmp_path / "out"), "--chunk-size", "128", "--chunk-overlap", "32", "--json"],
    )
    assert result.exit_code == 0
    import json
    data = json.loads(result.output)
    assert data["chunk_overlap"] == 32


def test_cli_invalid_overlap_exits_nonzero(short_md: Path, tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        compile_cmd,
        [str(short_md), "-o", str(tmp_path / "out"), "--chunk-size", "128", "--chunk-overlap", "128"],
    )
    assert result.exit_code != 0
