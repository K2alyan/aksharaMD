from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins.optimizers.token import TokenOptimizer


def _run(blocks: list[Block], pages: int = 1) -> CompilationContext:
    doc = Document(source="test.md", file_type="md", blocks=blocks, pages=pages)
    ctx = CompilationContext(source="test.md", output_dir="out")
    ctx.document = doc
    return TokenOptimizer().execute(ctx)


def _para(content: str, page: int | None = None) -> Block:
    return Block(type=BlockType.PARAGRAPH, content=content, page=page, index=0)


def _heading(content: str, level: int = 1, page: int | None = None) -> Block:
    return Block(type=BlockType.HEADING, content=content, level=level, page=page, index=0)


def _image(page: int | None = None) -> Block:
    return Block(type=BlockType.IMAGE, content="", page=page, index=0)


def test_duplicate_paragraph_removed():
    blocks = [_para("Same text here."), _para("Same text here.")]
    ctx = _run(blocks)
    assert len(ctx.document.blocks) == 1
    assert ctx.duplicate_blocks_removed == 1


def test_unique_blocks_kept():
    blocks = [_para("First."), _para("Second."), _para("Third.")]
    ctx = _run(blocks)
    assert len(ctx.document.blocks) == 3


def test_images_never_deduplicated():
    # Two IMAGE blocks with empty content — same checksum — must both survive
    blocks = [_image(page=1), _image(page=2)]
    ctx = _run(blocks)
    assert len(ctx.document.blocks) == 2
    assert ctx.duplicate_blocks_removed == 0


def test_repeated_header_removed():
    # Same text appears on every page in the header zone — should be removed
    # We need enough pages and repetitions to trigger the threshold (>= 40% of pages)
    header = "Company Confidential"
    blocks = []
    for page in range(1, 11):  # 10 pages
        blocks.append(Block(
            type=BlockType.PARAGRAPH, content=header,
            page=page, index=len(blocks),
        ))
        blocks.append(_para(f"Page {page} body content.", page=page))
    ctx = _run(blocks, pages=10)
    remaining = [b for b in ctx.document.blocks if b.content == header]
    assert len(remaining) == 0
    assert ctx.headers_removed > 0 or ctx.footers_removed > 0 or ctx.duplicate_blocks_removed > 0


def test_short_fragments_merged():
    # Two short paragraphs on the same page should be merged
    blocks = [
        Block(type=BlockType.PARAGRAPH, content="Short text.", page=1, index=0),
        Block(type=BlockType.PARAGRAPH, content="Also short.", page=1, index=1),
    ]
    ctx = _run(blocks)
    # Merged into one
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) == 1
    assert "Short text" in paras[0].content
    assert "Also short" in paras[0].content


def test_long_paragraphs_not_merged():
    long_text = "This is a very long paragraph that exceeds the merge threshold. " * 5
    blocks = [
        Block(type=BlockType.PARAGRAPH, content=long_text, page=1, index=0),
        Block(type=BlockType.PARAGRAPH, content=long_text + " extra", page=1, index=1),
    ]
    ctx = _run(blocks)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) == 2


def test_fragmented_headings_merged():
    # Same-level headings on same page that are each short get merged
    blocks = [
        Block(type=BlockType.HEADING, content="Annual", level=1, page=1, index=0),
        Block(type=BlockType.HEADING, content="Report", level=1, page=1, index=1),
        Block(type=BlockType.HEADING, content="2024", level=1, page=1, index=2),
    ]
    ctx = _run(blocks)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 1
    assert "Annual Report 2024" == headings[0].content


def test_original_tokens_recorded():
    blocks = [_para("Hello world this is some content for token counting.")]
    ctx = _run(blocks)
    assert ctx.original_tokens > 0


def test_blocks_reindexed():
    blocks = [_para("A"), _para("B"), _para("C")]
    ctx = _run(blocks)
    indices = [b.index for b in ctx.document.blocks]
    assert indices == list(range(len(ctx.document.blocks)))
