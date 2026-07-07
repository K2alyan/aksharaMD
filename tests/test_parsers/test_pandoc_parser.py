"""Tests for PandocParser — all unit tests mock the Pandoc binary so CI doesn't need it."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.models.validation import Severity
from aksharamd.plugins.parsers.pandoc_parser import _FORMATS, PandocParser, _detect_pandoc
from aksharamd.plugins.registry import get_parser

# ── AST helpers ───────────────────────────────────────────────────────────────

def _ast(blocks: list) -> str:
    """Wrap a list of block nodes in a minimal valid Pandoc v3 JSON AST."""
    return json.dumps({
        "pandoc-api-version": [3, 1, 9],
        "meta": {},
        "blocks": blocks,
    })


def _make_ctx(tmp_path: Path, ext: str = "adoc", filename: str = "test") -> CompilationContext:
    """Create a CompilationContext pointing at a real (empty) file on disk."""
    p = tmp_path / f"{filename}.{ext}"
    p.write_text("placeholder", encoding="utf-8")
    return CompilationContext(source=str(p), output_dir=str(tmp_path / "out"))


def _mock_pandoc_run(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    """Return a mock subprocess.CompletedProcess."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def _successful_detect():
    """Patch tuple for _detect_pandoc returning (True, '3.2.1')."""
    return patch(
        "aksharamd.plugins.parsers.pandoc_parser._detect_pandoc",
        return_value=(True, "3.2.1"),
    )


# ── Test 1: pandoc unavailable ────────────────────────────────────────────────

def test_pandoc_unavailable_emits_error(tmp_path):
    ctx = _make_ctx(tmp_path)
    with patch("aksharamd.plugins.parsers.pandoc_parser._detect_pandoc", return_value=(False, "")):
        PandocParser().execute(ctx)

    errors = [i for i in ctx.validation.issues if i.severity == Severity.ERROR]
    assert any(i.code == "PANDOC_UNAVAILABLE" for i in errors)
    assert ctx.document is None


# ── Test 2: pandoc non-zero exit ──────────────────────────────────────────────

def test_pandoc_nonzero_exit_emits_error(tmp_path):
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run("", returncode=1, stderr="parse error")
    ):
        PandocParser().execute(ctx)

    errors = [i for i in ctx.validation.issues if i.severity == Severity.ERROR]
    assert any(i.code == "PANDOC_FAILED" for i in errors)
    assert ctx.document is None


# ── Test 3: invalid JSON ──────────────────────────────────────────────────────

def test_pandoc_invalid_json_emits_error(tmp_path):
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run("not json at all", returncode=0)
    ):
        PandocParser().execute(ctx)

    errors = [i for i in ctx.validation.issues if i.severity == Severity.ERROR]
    assert any(i.code == "PANDOC_INVALID_JSON" for i in errors)
    assert ctx.document is None


# ── Test 4: Header conversion ─────────────────────────────────────────────────

def test_header_conversion(tmp_path):
    ast_json = _ast([
        {"t": "Header", "c": [2, ["sec-id", [], []], [{"t": "Str", "c": "Introduction"}]]},
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 1
    assert headings[0].level == 2
    assert "Introduction" in headings[0].content


# ── Test 5: Para conversion ───────────────────────────────────────────────────

def test_para_conversion(tmp_path):
    ast_json = _ast([
        {
            "t": "Para",
            "c": [
                {"t": "Str", "c": "Hello"},
                {"t": "Space"},
                {"t": "Str", "c": "world."},
            ],
        },
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) == 1
    assert paras[0].content == "Hello world."


# ── Test 6: CodeBlock conversion ─────────────────────────────────────────────

def test_code_block_conversion(tmp_path):
    ast_json = _ast([
        {"t": "CodeBlock", "c": [["", ["python", "numberLines"], []], "x = 1\nprint(x)\n"]},
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code_blocks) == 1
    assert code_blocks[0].language == "python"
    assert "x = 1" in code_blocks[0].content


# ── Test 7: BulletList conversion ────────────────────────────────────────────

def test_bullet_list_conversion(tmp_path):
    ast_json = _ast([
        {
            "t": "BulletList",
            "c": [
                [{"t": "Para", "c": [{"t": "Str", "c": "Alpha"}]}],
                [{"t": "Para", "c": [{"t": "Str", "c": "Beta"}]}],
            ],
        },
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    assert lists[0].content.startswith("- ")
    assert "Alpha" in lists[0].content
    assert "Beta" in lists[0].content


# ── Test 8: OrderedList conversion ───────────────────────────────────────────

def test_ordered_list_conversion(tmp_path):
    ast_json = _ast([
        {
            "t": "OrderedList",
            "c": [
                [1, {"t": "Decimal"}, {"t": "Period"}],
                [
                    [{"t": "Para", "c": [{"t": "Str", "c": "First"}]}],
                    [{"t": "Para", "c": [{"t": "Str", "c": "Second"}]}],
                ],
            ],
        },
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    assert lists[0].content.startswith("1.")
    assert "First" in lists[0].content


# ── Test 9: Block order preserved ────────────────────────────────────────────

def test_block_order_preserved(tmp_path):
    ast_json = _ast([
        {"t": "Header", "c": [1, ["h", [], []], [{"t": "Str", "c": "Title"}]]},
        {"t": "Para", "c": [{"t": "Str", "c": "Body text here."}]},
        {"t": "CodeBlock", "c": [["", ["bash"], []], "ls -la"]},
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    types = [b.type for b in ctx.document.blocks]
    assert types == [BlockType.HEADING, BlockType.PARAGRAPH, BlockType.CODE_BLOCK]


# ── Test 10: Unsupported node types recorded ──────────────────────────────────

def test_unsupported_node_types_recorded(tmp_path):
    # "Null" is not in the handled set — should land in unsupported
    ast_json = _ast([
        {"t": "Para", "c": [{"t": "Str", "c": "Normal paragraph."}]},
        {"t": "Null"},
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    unsupported = ctx.document.metadata.get("unsupported_node_types", [])
    assert len(unsupported) > 0
    assert "Null" in unsupported


# ── Tests 11-14: NOT registered for common formats ────────────────────────────

def test_not_registered_for_pdf():
    parser = get_parser("pdf")
    assert not isinstance(parser, PandocParser)


def test_not_registered_for_docx():
    parser = get_parser("docx")
    assert not isinstance(parser, PandocParser)


def test_not_registered_for_html():
    parser = get_parser("html")
    assert not isinstance(parser, PandocParser)


def test_not_registered_for_md():
    parser = get_parser("md")
    assert not isinstance(parser, PandocParser)


# ── Test 15: All target extensions registered ─────────────────────────────────

def test_all_target_extensions_registered():
    for ext in _FORMATS:
        parser = get_parser(ext)
        assert parser is not None, f"No parser registered for extension: {ext}"
        assert isinstance(parser, PandocParser), (
            f"Expected PandocParser for .{ext}, got {type(parser).__name__}"
        )


# ── Test 16: Metadata fields present ─────────────────────────────────────────

def test_metadata_fields_present(tmp_path):
    ast_json = _ast([
        {"t": "Para", "c": [{"t": "Str", "c": "Content."}]},
    ])
    ctx = _make_ctx(tmp_path)
    with _successful_detect(), patch(
        "subprocess.run", return_value=_mock_pandoc_run(ast_json)
    ):
        PandocParser().execute(ctx)

    assert ctx.document is not None
    meta = ctx.document.metadata
    assert "parser_backend" in meta
    assert meta["parser_backend"] == "pandoc"
    assert "pandoc_version" in meta
    assert "pandoc_source_format" in meta
    assert "unsupported_node_types" in meta


# ── Test 17: Integration test (skipped when Pandoc not installed) ─────────────

@pytest.mark.skipif(not _detect_pandoc()[0], reason="pandoc not installed")
def test_integration_asciidoc(tmp_path):
    adoc_content = """\
= My Document
:author: Test Author

== Introduction

This is a paragraph under the introduction section.

== Features

* First feature
* Second feature

[source,python]
----
def hello():
    print("hello world")
----
"""
    p = tmp_path / "sample.adoc"
    p.write_text(adoc_content, encoding="utf-8")

    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"))
    PandocParser().execute(ctx)

    assert ctx.document is not None
    assert len(ctx.document.blocks) > 0
    assert ctx.document.metadata.get("parser_backend") == "pandoc"
    assert ctx.document.metadata.get("pandoc_source_format") == "asciidoc"

    # Should have at least one heading and one paragraph
    block_types = {b.type for b in ctx.document.blocks}
    assert BlockType.HEADING in block_types or BlockType.PARAGRAPH in block_types


# ── Safe-mode: subprocess must never be invoked ───────────────────────────────

def test_safe_mode_blocks_pandoc_before_subprocess(tmp_path):
    """In safe mode, PandocParser must emit SAFE_MODE_BLOCKED and never call subprocess.run."""
    from unittest.mock import patch

    p = tmp_path / "doc.adoc"
    p.write_text("= Title\n\nBody.", encoding="utf-8")
    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"), safe_mode=True)

    with patch("subprocess.run") as mock_run:
        PandocParser().execute(ctx)
        mock_run.assert_not_called(), "subprocess.run must not be called in safe mode"

    error_codes = [i.code for i in ctx.validation.issues]
    assert "SAFE_MODE_BLOCKED" in error_codes
    assert ctx.document is None


def test_safe_mode_blocks_all_pandoc_extensions(tmp_path):
    """SAFE_MODE_BLOCKED must fire for every Pandoc-registered extension."""
    for ext in _FORMATS:
        p = tmp_path / f"doc.{ext}"
        p.write_text("placeholder", encoding="utf-8")
        ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"), safe_mode=True)
        PandocParser().execute(ctx)
        codes = [i.code for i in ctx.validation.issues]
        assert "SAFE_MODE_BLOCKED" in codes, f"Expected SAFE_MODE_BLOCKED for .{ext}, got {codes}"


def test_safe_mode_does_not_call_detect_pandoc(tmp_path):
    """_detect_pandoc (which itself runs 'pandoc --version') must not be called in safe mode."""
    from unittest.mock import patch

    p = tmp_path / "doc.org"
    p.write_text("* Heading\nBody text.", encoding="utf-8")
    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"), safe_mode=True)

    with patch("aksharamd.plugins.parsers.pandoc_parser._detect_pandoc") as mock_detect:
        PandocParser().execute(ctx)
        mock_detect.assert_not_called(), "_detect_pandoc must not be called in safe mode"
