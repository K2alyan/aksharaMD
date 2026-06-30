from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins.cleaners.default import DefaultCleaner


def _run(blocks: list[Block]) -> list[Block]:
    doc = Document(source="test.md", file_type="md", blocks=blocks)
    ctx = CompilationContext(source="test.md", output_dir="out")
    ctx.document = doc
    result = DefaultCleaner().execute(ctx)
    return result.document.blocks


def _block(content: str, btype: BlockType = BlockType.PARAGRAPH) -> Block:
    return Block(type=btype, content=content, index=0)


def test_page_number_dropped():
    blocks = [_block("42"), _block("Hello world paragraph.")]
    out = _run(blocks)
    assert len(out) == 1
    assert out[0].content == "Hello world paragraph."


def test_page_number_with_word_dropped():
    blocks = [_block("Page 5 of 20"), _block("Real content here.")]
    out = _run(blocks)
    assert len(out) == 1


def test_empty_paragraph_dropped():
    blocks = [_block(""), _block("   "), _block("Real content.")]
    out = _run(blocks)
    assert len(out) == 1
    assert out[0].content == "Real content."


def test_image_kept_without_content():
    blocks = [_block("", BlockType.IMAGE)]
    out = _run(blocks)
    assert len(out) == 1
    assert out[0].type == BlockType.IMAGE


def test_whitespace_normalized():
    blocks = [_block("Hello   world  with    spaces.")]
    out = _run(blocks)
    assert out[0].content == "Hello world with spaces."


def test_excess_blank_lines_collapsed():
    blocks = [_block("Line one.\n\n\n\nLine two.")]
    out = _run(blocks)
    assert "\n\n\n" not in out[0].content


def test_list_indentation_preserved():
    content = "- Top level\n  - Nested item\n    - Deep item"
    blocks = [_block(content, BlockType.LIST)]
    out = _run(blocks)
    assert out[0].content == content  # must be unchanged


def test_code_block_indentation_preserved():
    content = "def foo():\n    return 1\n    return 2"
    blocks = [_block(content, BlockType.CODE_BLOCK)]
    out = _run(blocks)
    assert "    return 1" in out[0].content


def test_zero_width_chars_removed():
    blocks = [_block("Hello​world")]  # zero-width space
    out = _run(blocks)
    assert out[0].content == "Helloworld"


def test_multiple_blocks_all_processed():
    blocks = [
        _block("First paragraph here."),
        _block(""),  # dropped
        _block("42"),  # dropped (page number)
        _block("Second paragraph here."),
    ]
    out = _run(blocks)
    assert len(out) == 2
