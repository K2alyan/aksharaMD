from __future__ import annotations

from pathlib import Path

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.text import _MAX_CONTENT_CHARS, TextParser


def _parse(text: str, tmp_path: Path, suffix: str = ".txt") -> CompilationContext:
    p = tmp_path / f"test{suffix}"
    p.write_text(text, encoding="utf-8")
    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"))
    return TextParser().execute(ctx)


def test_small_file_all_content(tmp_path):
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    ctx = _parse(text, tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) == 3
    assert any("First" in b.content for b in paras)
    assert any("Third" in b.content for b in paras)


def test_large_file_truncated(tmp_path):
    # Create a file well over the budget
    paragraph = "This is a long paragraph with enough content to fill space. " * 10
    paragraphs = [paragraph] * 200
    text = "\n\n".join(paragraphs)
    assert len(text) > _MAX_CONTENT_CHARS

    ctx = _parse(text, tmp_path)
    full_content = "\n".join(b.content for b in ctx.document.blocks)
    assert "Truncated" in full_content


def test_large_file_has_metadata_block(tmp_path):
    paragraph = "A" * 300
    text = "\n\n".join([paragraph] * 300)
    ctx = _parse(text, tmp_path)
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta) >= 1
    assert "Words" in meta[0].content or "Size" in meta[0].content


def test_chapter_heading_toc(tmp_path):
    # A file with chapter headings should produce a TOC in large mode
    chapters = "\n\n".join(
        f"CHAPTER {i}\n\n" + ("Content paragraph. " * 20)
        for i in range(1, 60)
    )
    ctx = _parse(chapters, tmp_path)
    # Should have a structure/TOC block
    all_content = "\n".join(b.content for b in ctx.document.blocks)
    assert "CHAPTER" in all_content


def test_small_file_no_metadata(tmp_path):
    text = "Just a short document.\n\nWith two paragraphs."
    ctx = _parse(text, tmp_path)
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta) == 0


def test_file_type_preserved(tmp_path):
    ctx = _parse("Hello.", tmp_path, suffix=".txt")
    assert ctx.document.file_type == "txt"


def test_empty_paragraphs_skipped(tmp_path):
    text = "Para one.\n\n\n\nPara two."
    ctx = _parse(text, tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) == 2


@pytest.mark.parametrize(
    ("suffix", "language", "source"),
    [
        (".py", "python", '\ndef outer():\n    if True:\n        return "a  b"\n\n\n'),
        (".yaml", "yaml", "root:\n  children:\n    - name: first\n      value: |\n        a  b\n\n"),
        (".yml", "yaml", "root:\n  enabled: true\n"),
        (".conf", "conf", "key = a  b\n\n"),
        (".sh", "sh", "cat <<'EOF'\n  literal  text\nEOF\n"),
    ],
)
def test_literal_source_preserved_through_compiler_export(tmp_path, suffix, language, source):
    from aksharamd.compiler import Compiler

    path = tmp_path / f"source{suffix}"
    path.write_bytes(source.encode("utf-8"))
    output = tmp_path / "compiled"
    ctx = Compiler(output_dir=str(output)).compile(str(path))
    code = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code) == 1
    assert code[0].content == source
    assert code[0].language == language
    assert ctx.document.file_type == suffix[1:]
    markdown = (output / "document.md").read_text(encoding="utf-8")
    assert f"```{language}\n{source}\n```" in markdown
    if suffix == ".py":
        import ast
        assert ast.dump(ast.parse(code[0].content)) == ast.dump(ast.parse(source))


def test_large_literal_source_is_not_a_prose_preview(tmp_path):
    source = "root:\n" + "  # retain every comment and blank line\n\n" * 2000 + "  tail: 42\n"
    assert len(source) > _MAX_CONTENT_CHARS
    ctx = _parse(source, tmp_path, suffix=".yaml")
    assert len(ctx.document.blocks) == 1
    assert ctx.document.blocks[0].content == source
    assert not ctx.document.metadata.get("truncated", False)
    assert not any(issue.code == "W_OUTPUT_TRUNCATED" for issue in ctx.validation.warnings)


def test_numeric_facts_preserved_through_compiler_export(tmp_path):
    from aksharamd.compiler import Compiler

    path = tmp_path / "amount.txt"
    path.write_text("Amount\n\n1000\n\n2026\n\n00042", encoding="utf-8")
    output = tmp_path / "compiled"
    ctx = Compiler(output_dir=str(output)).compile(str(path))
    assert [b.content for b in ctx.document.blocks] == ["Amount", "1000", "2026", "00042"]
    markdown = (output / "document.md").read_text(encoding="utf-8")
    assert "Amount\n\n1000\n\n2026\n\n00042" in markdown
