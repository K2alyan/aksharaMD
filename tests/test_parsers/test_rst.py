from __future__ import annotations

from pathlib import Path

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.rst import RSTParser


def _parse(text: str, tmp_path: Path) -> CompilationContext:
    p = tmp_path / "test.rst"
    p.write_text(text, encoding="utf-8")
    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"))
    return RSTParser().execute(ctx)


def test_section_heading(tmp_path):
    rst = "My Title\n========\n\nSome paragraph.\n"
    ctx = _parse(rst, tmp_path)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert any("My Title" in h.content for h in headings)


def test_subsection_heading(tmp_path):
    # Docutils needs actual content under each section to emit <h2> rather than subtitle
    rst = (
        "Title\n=====\n\nIntro content.\n\n"
        "Section One\n-----------\n\nContent here.\n\n"
        "Section Two\n-----------\n\nMore content.\n"
    )
    ctx = _parse(rst, tmp_path)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) >= 2
    assert any("Title" in h.content for h in headings)
    assert any("Section" in h.content for h in headings)


def test_paragraph(tmp_path):
    rst = "Title\n=====\n\nThis is a paragraph with some content.\n"
    ctx = _parse(rst, tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert any("paragraph" in b.content for b in paras)


def test_bullet_list(tmp_path):
    rst = "Title\n=====\n\n- Item one\n- Item two\n- Item three\n"
    ctx = _parse(rst, tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    assert "Item one" in lists[0].content


def test_nested_list(tmp_path):
    rst = "Title\n=====\n\n- Top\n\n  - Nested\n\n- Other\n"
    ctx = _parse(rst, tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) >= 1


def test_code_block(tmp_path):
    rst = "Title\n=====\n\n.. code-block:: python\n\n   x = 1\n   print(x)\n"
    ctx = _parse(rst, tmp_path)
    code = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code) == 1
    assert "x = 1" in code[0].content


def test_grid_table(tmp_path):
    rst = ("Title\n=====\n\n"
           "+-------+-------+\n"
           "| A     | B     |\n"
           "+=======+=======+\n"
           "| 1     | 2     |\n"
           "+-------+-------+\n")
    ctx = _parse(rst, tmp_path)
    tables = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 1
    assert "A" in tables[0].content


def test_empty_rst(tmp_path):
    ctx = _parse("", tmp_path)
    assert ctx.document is not None
