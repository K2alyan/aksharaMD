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


# ── Admonition blocks ────────────────────────────────────────────────────────

def test_github_admonition_note(tmp_path):
    """GitHub/Obsidian [!NOTE] blockquote produces an ADMONITION block."""
    md = "> [!NOTE]\n> This is important.\n"
    ctx = _parse(md, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "note"
    assert "important" in admonitions[0].content


def test_github_admonition_warning_multiline(tmp_path):
    """GitHub [!WARNING] with multiple body lines keeps all body text."""
    md = "> [!WARNING]\n> First line.\n> Second line.\n"
    ctx = _parse(md, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "warning"
    assert "First line" in admonitions[0].content


def test_mkdocs_admonition(tmp_path):
    """MkDocs !!! note syntax produces an ADMONITION block."""
    md = "!!! warning Some danger ahead\n"
    ctx = _parse(md, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "warning"


def test_plain_blockquote_not_converted(tmp_path):
    """A regular blockquote without [!TYPE] stays as BLOCKQUOTE, not ADMONITION."""
    md = "> Just a regular quote.\n"
    ctx = _parse(md, tmp_path)
    bqs = [b for b in ctx.document.blocks if b.type == BlockType.BLOCKQUOTE]
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(bqs) == 1
    assert len(admonitions) == 0
    assert "regular quote" in bqs[0].content


def test_admonition_case_insensitive(tmp_path):
    """[!TIP] should match regardless of case."""
    md = "> [!TIP]\n> Pro tip.\n"
    ctx = _parse(md, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "tip"


def test_quote_tokens_are_not_reemitted_as_body(tmp_path):
    ctx = _parse("> The claim is approved.\n\nThe claim is approved.\n", tmp_path)
    assert [(b.type, b.content) for b in ctx.document.blocks] == [
        (BlockType.BLOCKQUOTE, "The claim is approved."),
        (BlockType.PARAGRAPH, "The claim is approved."),
    ]


def test_nested_quote_tokens_and_following_body_keep_their_boundaries(tmp_path):
    source = "> Outer\n>\n> > Inner\n> >\n> > Deep detail\n>\n> Outer again\n\nOutside"
    ctx = _parse(source, tmp_path)
    assert len(ctx.document.blocks) == 2
    quote, body = ctx.document.blocks
    assert quote.type == BlockType.BLOCKQUOTE
    assert quote.content == "Outer\n\n> Inner\n>\n> Deep detail\n\nOuter again"
    assert body.type == BlockType.PARAGRAPH and body.content == "Outside"


def test_compiled_nested_quote_preserves_markdown_structure_and_literal_repetitions(tmp_path):
    from markdown_it import MarkdownIt

    from aksharamd.compiler import Compiler

    source = (
        "> Outer\n>\n> > Inner\n> >\n> > ```python\n> > if ready:\n> >     ship()\n> > ```\n"
        ">\n> - repeated\n> - repeated\n\nRepeated body.\n\nRepeated body."
    )
    path = tmp_path / "nested.md"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "compiled"
    ctx = Compiler(output_dir=str(output)).compile(str(path))
    rendered = (output / "document.md").read_text(encoding="utf-8")
    assert len(ctx.document.blocks) == 3
    assert rendered.count("Repeated body.") == 2
    parser = MarkdownIt()

    def semantic_tokens(text):
        return [(t.type, t.tag, t.nesting, t.content, t.info) for t in parser.parse(text)]

    assert semantic_tokens(rendered) == semantic_tokens(source)


def test_admonition_consumes_its_inner_tokens_once(tmp_path):
    ctx = _parse("> [!NOTE]\n> First line.\n> Second line.\n\nBody.", tmp_path)
    assert [b.type for b in ctx.document.blocks] == [BlockType.ADMONITION, BlockType.PARAGRAPH]
    assert ctx.document.blocks[0].content == "First line.\nSecond line."
    assert ctx.document.blocks[1].content == "Body."


def test_quote_source_map_does_not_treat_unicode_separators_as_lines(tmp_path):
    text = "First\u2028second\u2028third\u2028last"
    ctx = _parse("> " + text + "\n\nOutside", tmp_path)
    assert [b.content for b in ctx.document.blocks] == [text, "Outside"]


def test_compiled_admonition_preserves_nested_code_and_blank_lines(tmp_path):
    from markdown_it import MarkdownIt

    from aksharamd.compiler import Compiler

    code = "if ready:\n    ship()\n\n    notify()\n"
    source = "> [!NOTE]\n> ```python\n> if ready:\n>     ship()\n>\n>     notify()\n> ```"
    path = tmp_path / "admonition.md"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "compiled"
    ctx = Compiler(output_dir=str(output)).compile(str(path))
    assert len(ctx.document.blocks) == 1
    block = ctx.document.blocks[0]
    assert block.type == BlockType.ADMONITION
    assert block.content == "```python\n" + code + "```"
    rendered = (output / "document.md").read_text(encoding="utf-8")
    fences = [t for t in MarkdownIt().parse(rendered) if t.type == "fence"]
    assert len(fences) == 1
    assert fences[0].content == code
    assert fences[0].info == "python"
    assert rendered.startswith("> **NOTE**:\n>\n> ```python")


def test_compiled_quote_keeps_unicode_separator_as_literal_content(tmp_path):
    from aksharamd.compiler import Compiler

    source = "> First\u2028second\u2028third\u2028last"
    path = tmp_path / "unicode.md"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "compiled"
    Compiler(output_dir=str(output)).compile(str(path))
    assert (output / "document.md").read_text(encoding="utf-8") == source


def test_admonition_marker_inside_quoted_code_stays_literal(tmp_path):
    ctx = _parse(">     [!NOTE]\n>     literal", tmp_path)
    assert len(ctx.document.blocks) == 1
    assert ctx.document.blocks[0].type == BlockType.BLOCKQUOTE
    assert ctx.document.blocks[0].content == "    [!NOTE]\n    literal"
