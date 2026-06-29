from __future__ import annotations
import textwrap
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler
from aksharamd.context import CompilationContext


@pytest.fixture
def tmp_md(tmp_path: Path) -> Path:
    doc = tmp_path / "sample.md"
    doc.write_text(textwrap.dedent("""
        # Introduction

        This is a sample document for testing AksharaMD.

        ## Section One

        Here is some content in section one. It contains multiple sentences.
        This paragraph has enough content to be meaningful.

        ## Section Two

        Another section with content. Tables and code blocks follow.

        ```python
        def hello():
            return "world"
        ```

        | Column A | Column B |
        | --- | --- |
        | Value 1 | Value 2 |
        | Value 3 | Value 4 |
    """), encoding="utf-8")
    return doc


@pytest.fixture
def tmp_txt(tmp_path: Path) -> Path:
    doc = tmp_path / "sample.txt"
    doc.write_text(
        "First paragraph with some content that is long enough to avoid merging by the optimizer.\n\n"
        "Second paragraph with more content that is also long enough to avoid merging by the optimizer.\n\n"
        "Third paragraph with sufficient length to stand alone as its own block in the output.",
        encoding="utf-8",
    )
    return doc


def test_compile_markdown(tmp_md: Path, tmp_path: Path):
    out = tmp_path / "out"
    ctx = Compiler(output_dir=str(out)).compile(str(tmp_md))

    assert ctx.document is not None
    assert ctx.document.file_type == "md"
    assert len(ctx.document.blocks) > 0
    assert len(ctx.chunks) > 0
    assert ctx.manifest is not None
    assert ctx.manifest.readiness_score > 0
    assert (out / "document.md").exists()
    assert (out / "document.json").exists()
    assert (out / "manifest.json").exists()
    assert (out / "validation.json").exists()


def test_compile_text(tmp_txt: Path, tmp_path: Path):
    out = tmp_path / "out"
    ctx = Compiler(output_dir=str(out)).compile(str(tmp_txt))

    assert ctx.document is not None
    assert ctx.document.file_type == "txt"
    assert len(ctx.document.blocks) == 3


def test_determinism(tmp_md: Path, tmp_path: Path):
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    ctx1 = Compiler(output_dir=str(out1)).compile(str(tmp_md))
    ctx2 = Compiler(output_dir=str(out2)).compile(str(tmp_md))

    # Block IDs must be stable across runs
    ids1 = [b.id for b in ctx1.document.blocks]
    ids2 = [b.id for b in ctx2.document.blocks]
    assert ids1 == ids2

    # Chunk IDs must be stable
    chunk_ids1 = [c.id for c in ctx1.chunks]
    chunk_ids2 = [c.id for c in ctx2.chunks]
    assert chunk_ids1 == chunk_ids2


def test_token_counts_non_negative(tmp_md: Path, tmp_path: Path):
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(tmp_md))
    assert ctx.manifest.original_tokens >= 0
    assert ctx.manifest.optimized_tokens >= 0
    assert 0.0 <= ctx.manifest.token_reduction_percent <= 100.0


def test_no_parser_for_unknown_type(tmp_path: Path):
    f = tmp_path / "file.xyz"
    f.write_text("content")
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    assert ctx.document is None
    assert len(ctx.validation.errors) > 0
