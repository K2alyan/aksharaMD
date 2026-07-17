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


# ---------------------------------------------------------------------------
# Numbered heading preservation — regression tests (ec-ho-001 / AXA URD fix)
# ---------------------------------------------------------------------------

def test_merged_heading_gets_unique_id():
    """Merged headings must receive id=checksum, not id='', to avoid payload collisions."""
    blocks = [
        Block(type=BlockType.HEADING, content="1.5", level=2, page=1, index=0),
        Block(type=BlockType.HEADING, content="Ratings", level=2, page=1, index=1),
    ]
    ctx = _run(blocks)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 1
    assert headings[0].id != "", "merged heading must not have empty id"
    assert headings[0].id == headings[0].checksum, "merged heading id must equal its checksum"


def test_multiple_merged_headings_have_distinct_ids():
    """Multiple merged heading groups on different pages must all get distinct, non-empty ids."""
    blocks = [
        Block(type=BlockType.HEADING, content="1.5", level=2, page=1, index=0),
        Block(type=BlockType.HEADING, content="Ratings", level=2, page=1, index=1),
        _para("Some body text.", page=1),
        Block(type=BlockType.HEADING, content="1.5.1", level=3, page=1, index=3),
        Block(type=BlockType.HEADING, content="Insurer financial strength", level=3, page=1, index=4),
    ]
    ctx = _run(blocks)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 2
    ids = [h.id for h in headings]
    assert "" not in ids, "no merged heading may have empty id"
    assert len(set(ids)) == 2, "merged headings must have distinct ids"


def test_numbered_subsection_heading_not_deduplicated():
    """A subsection heading (e.g. 1.5.1) that shares a page with other headings must survive."""
    blocks = [
        Block(type=BlockType.HEADING, content="1.5 Ratings", level=2, page=1, index=0),
        _para("Intro paragraph.", page=1),
        Block(type=BlockType.HEADING, content="1.5.1 Insurer financial strength and counterparty credit ratings", level=3, page=1, index=2),
        _para("Body content.", page=1),
    ]
    ctx = _run(blocks)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 2
    contents = [h.content for h in headings]
    assert any("1.5.1" in c for c in contents), "1.5.1 subsection heading must survive optimization"


def test_numbered_heading_not_removed_as_furniture():
    """Numbered section headings repeated across pages must never be removed as page furniture."""
    # Simulate a heading repeated at the top of 10 pages (would normally be detected as a header)
    blocks = []
    for page in range(1, 11):
        blocks.append(Block(
            type=BlockType.HEADING, content="1.5 Ratings", level=2,
            page=page, index=len(blocks),
        ))
        blocks.append(_para(f"Page {page} body.", page=page))
    ctx = _run(blocks, pages=10)
    numbered_headings = [
        b for b in ctx.document.blocks
        if b.type == BlockType.HEADING and b.content == "1.5 Ratings"
    ]
    assert len(numbered_headings) > 0, "repeated numbered section headings must not be removed as furniture"


def test_plain_repeated_header_still_removed():
    """Non-numbered repeated page headers should still be removed (existing behavior)."""
    blocks = []
    for page in range(1, 11):
        blocks.append(Block(
            type=BlockType.PARAGRAPH, content="Company Confidential",
            page=page, index=len(blocks),
        ))
        blocks.append(_para(f"Page {page} body.", page=page))
    ctx = _run(blocks, pages=10)
    remaining = [b for b in ctx.document.blocks if b.content == "Company Confidential"]
    assert len(remaining) == 0, "plain repeated page headers must still be removed"


def test_multiple_numbering_schemes_preserved():
    """Headings with various numbering schemes (decimal, roman, letter+digit) survive optimization."""
    blocks = [
        Block(type=BlockType.HEADING, content="1.5.1 Decimal sub-section", level=3, page=1, index=0),
        Block(type=BlockType.HEADING, content="Section 1.5.1 Named sub-section", level=3, page=2, index=1),
        Block(type=BlockType.HEADING, content="A.2 Appendix section", level=2, page=3, index=2),
        Block(type=BlockType.HEADING, content="IV. Roman numeral section", level=2, page=4, index=3),
    ]
    # Give each page one heading — four pages, four headings, none repeated enough for removal
    ctx = _run(blocks, pages=4)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 4, "all numbering scheme variants must survive"


def test_heading_with_following_content_preserved():
    """A numbered heading immediately before a paragraph must both survive."""
    blocks = [
        Block(type=BlockType.HEADING, content="1.5.1 Ratings subsection", level=3, page=1, index=0),
        _para("The following table shows ratings for AXA.", page=1),
    ]
    ctx = _run(blocks)
    assert len(ctx.document.blocks) == 2
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) == 1
    assert "1.5.1" in headings[0].content
