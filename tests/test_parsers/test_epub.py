from __future__ import annotations

from pathlib import Path

import pytest

ebooklib = pytest.importorskip("ebooklib", reason="ebooklib not installed")

from ebooklib import epub as _epub

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType


def _make_epub(tmp_path: Path, title: str = "Test Book", author: str = "Test Author",
               chapters: list[tuple[str, str]] | None = None) -> Path:
    if chapters is None:
        chapters = [("Chapter One", "<h1>Chapter One</h1><p>Hello world content.</p>")]

    book = _epub.EpubBook()
    book.set_identifier("test-id-001")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    items = []
    for i, (ch_title, ch_content) in enumerate(chapters):
        c = _epub.EpubHtml(title=ch_title, file_name=f"chap_{i:02d}.xhtml", lang="en")
        c.content = ch_content
        book.add_item(c)
        items.append(c)

    book.toc = tuple(_epub.Link(f"chap_{i:02d}.xhtml", t, f"ch{i}") for i, (t, _) in enumerate(chapters))
    book.add_item(_epub.EpubNcx())
    book.add_item(_epub.EpubNav())
    book.spine = ["nav"] + items

    path = tmp_path / "test.epub"
    _epub.write_epub(str(path), book)
    return path


def _compile(path: Path, tmp_path: Path):
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(path))


# ── basic parsing ─────────────────────────────────────────────────────────────

def test_epub_produces_document(tmp_path):
    path = _make_epub(tmp_path)
    ctx = _compile(path, tmp_path)
    assert ctx.document is not None
    assert ctx.document.file_type == "epub"


def test_epub_title_extracted(tmp_path):
    path = _make_epub(tmp_path, title="My Great Novel")
    ctx = _compile(path, tmp_path)
    assert ctx.document.title == "My Great Novel"


def test_epub_author_extracted(tmp_path):
    path = _make_epub(tmp_path, author="Jane Smith")
    ctx = _compile(path, tmp_path)
    assert ctx.document.author == "Jane Smith"


def test_epub_has_metadata_block(tmp_path):
    path = _make_epub(tmp_path)
    ctx = _compile(path, tmp_path)
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta) >= 1


def test_epub_heading_extracted(tmp_path):
    path = _make_epub(tmp_path, chapters=[
        ("Introduction", "<h1>Introduction</h1><p>This is the intro.</p>"),
    ])
    ctx = _compile(path, tmp_path)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert any("Introduction" in b.content for b in headings)


def test_epub_paragraph_extracted(tmp_path):
    path = _make_epub(tmp_path, chapters=[
        ("Ch1", "<h1>Chapter</h1><p>This is paragraph content for the test.</p>"),
    ])
    ctx = _compile(path, tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) >= 1


def test_epub_multiple_chapters(tmp_path):
    path = _make_epub(tmp_path, chapters=[
        ("Ch1", "<h1>First Chapter</h1><p>First content.</p>"),
        ("Ch2", "<h2>Second Chapter</h2><p>Second content.</p>"),
        ("Ch3", "<h2>Third Chapter</h2><p>Third content.</p>"),
    ])
    ctx = _compile(path, tmp_path)
    assert ctx.document is not None
    assert ctx.document.pages >= 3


def test_epub_toc_block_present(tmp_path):
    path = _make_epub(tmp_path, chapters=[
        ("Introduction", "<h1>Introduction</h1><p>Content.</p>"),
        ("Part One", "<h1>Part One</h1><p>More content.</p>"),
    ])
    ctx = _compile(path, tmp_path)
    # TOC is a paragraph block starting with "**Table of Contents**"
    toc = [b for b in ctx.document.blocks
           if b.type == BlockType.PARAGRAPH and "Contents" in b.content]
    assert len(toc) >= 1


def test_epub_pages_equals_section_count(tmp_path):
    path = _make_epub(tmp_path, chapters=[
        ("A", "<p>Content A.</p>"),
        ("B", "<p>Content B.</p>"),
    ])
    ctx = _compile(path, tmp_path)
    # pages = number of ITEM_DOCUMENT sections (includes nav)
    assert ctx.document.pages >= 2


# ── error handling ────────────────────────────────────────────────────────────

def test_epub_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "corrupt.epub"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
    ctx = _compile(path, tmp_path)
    assert ctx is not None


def test_epub_empty_file_does_not_crash(tmp_path):
    path = tmp_path / "empty.epub"
    path.write_bytes(b"")
    ctx = _compile(path, tmp_path)
    assert ctx is not None
