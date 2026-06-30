from __future__ import annotations

from pathlib import Path

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.markdown import MarkdownParser


def _parse(text: str, tmp_path: Path) -> CompilationContext:
    p = tmp_path / "test.md"
    p.write_text(text, encoding="utf-8")
    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"))
    return MarkdownParser().execute(ctx)


def test_heading_levels(tmp_path):
    ctx = _parse("# H1\n\n## H2\n\n### H3\n", tmp_path)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert headings[0].level == 1
    assert headings[1].level == 2
    assert headings[2].level == 3


def test_title_from_h1(tmp_path):
    ctx = _parse("# My Document\n\nSome text.\n", tmp_path)
    assert ctx.document.title == "My Document"


def test_paragraph(tmp_path):
    ctx = _parse("Hello world, this is a paragraph.\n", tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert any("Hello world" in b.content for b in paras)


def test_links_stripped_from_paragraph(tmp_path):
    ctx = _parse("See [the docs](https://example.com) for details.\n", tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) == 1
    assert "the docs" in paras[0].content
    assert "https://" not in paras[0].content


def test_flat_bullet_list(tmp_path):
    ctx = _parse("- Alpha\n- Beta\n- Gamma\n", tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    assert "- Alpha" in lists[0].content
    assert "- Gamma" in lists[0].content


def test_nested_bullet_list(tmp_path):
    md = "- Top 1\n- Top 2\n  - Nested A\n  - Nested B\n    - Deep\n- Top 3\n"
    ctx = _parse(md, tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    content = lists[0].content
    assert "- Top 1" in content
    assert "  - Nested A" in content
    assert "    - Deep" in content


def test_ordered_list(tmp_path):
    ctx = _parse("1. First\n2. Second\n3. Third\n", tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    assert "1. First" in lists[0].content
    assert "3. Third" in lists[0].content


def test_fenced_code_block_with_language(tmp_path):
    ctx = _parse("```python\ndef hello():\n    pass\n```\n", tmp_path)
    code = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code) == 1
    assert code[0].language == "python"
    assert "def hello" in code[0].content


def test_table(tmp_path):
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    ctx = _parse(md, tmp_path)
    tables = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 1
    assert "A" in tables[0].content and "1" in tables[0].content


def test_blockquote(tmp_path):
    ctx = _parse("> This is quoted.\n", tmp_path)
    bqs = [b for b in ctx.document.blocks if b.type == BlockType.BLOCKQUOTE]
    assert len(bqs) == 1 and "quoted" in bqs[0].content


def test_list_items_not_double_emitted(tmp_path):
    """List items must not appear as both list and paragraph blocks."""
    ctx = _parse("- Item one\n- Item two\n", tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(lists) == 1
    # Items must NOT appear as extra paragraph blocks
    para_text = " ".join(b.content for b in paras)
    assert "Item one" not in para_text
