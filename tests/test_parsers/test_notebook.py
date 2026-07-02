from __future__ import annotations

import json
from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.notebook import _md_heading_level


def _compile_ipynb(tmp_path: Path, nb: dict) -> CompilationContext:
    f = tmp_path / "notebook.ipynb"
    f.write_text(json.dumps(nb), encoding="utf-8")
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(f))


def _minimal_nb(**kwargs) -> dict:
    base = {
        "nbformat": 4,
        "nbformat_minor": 4,
        "metadata": {
            "kernelspec": {"name": "python3", "language": "python"},
            "language_info": {"name": "python"},
        },
        "cells": [],
    }
    base.update(kwargs)
    return base


# ── _md_heading_level ─────────────────────────────────────────────────────────

def test_heading_level_h1():
    assert _md_heading_level("# Title") == 1


def test_heading_level_h3():
    assert _md_heading_level("### Sub") == 3


def test_heading_level_not_heading():
    assert _md_heading_level("Normal paragraph text") is None


def test_heading_level_hash_without_space():
    assert _md_heading_level("#NoSpace") is None


# ── markdown cells ────────────────────────────────────────────────────────────

def test_notebook_markdown_heading_becomes_block(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "markdown",
        "source": ["# My Notebook\n\nIntroduction paragraph."],
        "metadata": {},
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    assert ctx.document is not None
    types = [b.type for b in ctx.document.blocks]
    assert BlockType.HEADING in types
    assert ctx.document.title == "My Notebook"


def test_notebook_markdown_paragraph(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "markdown",
        "source": ["Just a regular paragraph with some content."],
        "metadata": {},
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    assert ctx.document is not None
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) >= 1


def test_notebook_multiple_heading_levels(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "markdown",
        "source": ["# H1\n\n## H2\n\n### H3\n\nSome text here for the paragraph."],
        "metadata": {},
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    levels = {b.level for b in headings}
    assert 1 in levels
    assert 2 in levels
    assert 3 in levels


# ── code cells ────────────────────────────────────────────────────────────────

def test_notebook_code_cell_becomes_code_block(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "code",
        "source": ["import os\nprint(os.getcwd())"],
        "metadata": {},
        "outputs": [],
        "execution_count": None,
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code_blocks) >= 1
    assert "print" in code_blocks[0].content


def test_notebook_code_cell_with_text_output(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "code",
        "source": ["print('hello')"],
        "metadata": {},
        "outputs": [{
            "output_type": "stream",
            "name": "stdout",
            "text": ["hello\n"],
        }],
        "execution_count": 1,
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    quotes = [b for b in ctx.document.blocks if b.type == BlockType.BLOCKQUOTE]
    assert len(quotes) >= 1
    assert "hello" in quotes[0].content


def test_notebook_code_cell_stderr_output(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "code",
        "source": ["import sys\nprint('err', file=sys.stderr)"],
        "metadata": {},
        "outputs": [{
            "output_type": "stream",
            "name": "stderr",
            "text": ["UserWarning: something\n"],
        }],
        "execution_count": 1,
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    quotes = [b for b in ctx.document.blocks if b.type == BlockType.BLOCKQUOTE]
    assert any("[stderr]" in b.content for b in quotes)


def test_notebook_empty_code_cell_skipped(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "code",
        "source": ["   \n  "],
        "metadata": {},
        "outputs": [],
        "execution_count": None,
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code_blocks) == 0


# ── raw cells ─────────────────────────────────────────────────────────────────

def test_notebook_raw_cell_becomes_paragraph(tmp_path):
    nb = _minimal_nb(cells=[{
        "cell_type": "raw",
        "source": ["Raw content here."],
        "metadata": {},
    }])
    ctx = _compile_ipynb(tmp_path, nb)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert any("Raw content" in b.content for b in paras)


# ── metadata ──────────────────────────────────────────────────────────────────

def test_notebook_metadata_block_present(tmp_path):
    nb = _minimal_nb()
    ctx = _compile_ipynb(tmp_path, nb)
    meta_blocks = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta_blocks) >= 1
    assert "Notebook" in meta_blocks[0].content


def test_notebook_empty_cells_list(tmp_path):
    ctx = _compile_ipynb(tmp_path, _minimal_nb())
    assert ctx.document is not None
    assert ctx.document.file_type == "ipynb"


# ── error handling ────────────────────────────────────────────────────────────

def test_notebook_corrupt_json(tmp_path):
    f = tmp_path / "bad.ipynb"
    f.write_bytes(b"{not valid json")
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    assert ctx.document is None or ctx.validation.errors
