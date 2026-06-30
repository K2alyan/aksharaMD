from __future__ import annotations

from pathlib import Path

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SKIP_TAGS = {"script", "style", "nav", "aside"}

# Budget for large books — keeps output under ~15k tokens for full novels
_MAX_CONTENT_CHARS = 60_000


def _parse_html_content(html_bytes: bytes) -> list[tuple[BlockType, str, int | None, str | None]]:
    """
    Returns list of (block_type, content, level, extra) where extra is the
    image src href for IMAGE blocks and None for all other block types.
    """
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup.find_all(_SKIP_TAGS):
        tag.decompose()

    results: list[tuple[BlockType, str, int | None, str | None]] = []
    body = soup.find("body") or soup

    for el in body.descendants:
        if not isinstance(el, Tag):
            continue
        name = (el.name or "").lower()

        if name in _HEADING_TAGS:
            text = el.get_text(strip=True)
            if text:
                results.append((BlockType.HEADING, text, _HEADING_TAGS[name], None))

        elif name == "p":
            text = el.get_text(separator=" ", strip=True)
            if text and len(text) > 10:
                results.append((BlockType.PARAGRAPH, text, None, None))

        elif name in ("pre", "code"):
            text = el.get_text()
            if text.strip():
                results.append((BlockType.CODE_BLOCK, text.strip(), None, None))

        elif name in ("li",):
            text = el.get_text(strip=True)
            if text:
                results.append((BlockType.LIST, f"- {text}", None, None))

        elif name == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src or alt:
                results.append((BlockType.IMAGE, alt or src, None, src))

    return results


def _get_epub_image(book, src_href: str) -> bytes | None:
    """Look up image bytes from an EPUB book by href."""
    if not src_href:
        return None
    # Try direct lookup first
    item = book.get_item_with_href(src_href)
    if item:
        return item.get_content()
    # Try matching by basename (handles relative paths like "../images/fig.png")
    basename = Path(src_href).name
    for it in book.get_items():
        if Path(it.get_name()).name == basename:
            return it.get_content()
    return None


class EpubParser(ParserPlugin):
    name = "epub_parser"
    supported_types = ["epub"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import ebooklib
        from ebooklib import epub

        path = Path(ctx.source)
        try:
            book = epub.read_epub(str(path), options={"ignore_ncx": True})
        except Exception as e:
            ctx.error("EPUB_PARSE_ERROR", str(e))
            return ctx

        title = None
        author = None
        titles = book.get_metadata("DC", "title")
        if titles:
            title = titles[0][0]
        authors = book.get_metadata("DC", "creator")
        if authors:
            author = authors[0][0]

        all_items = [
            item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
            if item.get_content()
        ]
        total_sections = len(all_items)

        # Metadata block
        meta_parts = [f"File: {path.name}"]
        if title:
            meta_parts.append(f"Title: {title}")
        if author:
            meta_parts.append(f"Author: {author}")
        meta_parts.append(f"Sections: {total_sections}")

        blocks: list[Block] = [
            Block(type=BlockType.METADATA, content=" | ".join(meta_parts), index=0)
        ]
        doc_assets: list[Asset] = []
        idx = 1

        # First pass: collect top-level headings for a TOC (always — even truncated books)
        toc_lines: list[str] = []
        for item in all_items:
            parsed = _parse_html_content(item.get_content())
            for btype, text, level, _extra in parsed:
                if btype == BlockType.HEADING and level and level <= 2:
                    indent = "  " * (level - 1)
                    toc_lines.append(f"{indent}- {text}")
                    if len(toc_lines) >= 60:
                        break
            if len(toc_lines) >= 60:
                break

        if toc_lines:
            blocks.append(Block(
                type=BlockType.PARAGRAPH,
                content="**Table of Contents**\n" + "\n".join(toc_lines),
                index=idx,
            ))
            idx += 1

        # Second pass: content up to char budget
        total_chars = 0
        chapter_count = 0
        truncated = False

        for item in all_items:
            if truncated:
                break
            parsed = _parse_html_content(item.get_content())
            if not parsed:
                continue

            chapter_count += 1
            for btype, text, level, extra in parsed:
                if btype == BlockType.IMAGE:
                    src_href = extra or ""
                    img_bytes = _get_epub_image(book, src_href)
                    asset_id = f"img_{idx}"
                    doc_assets.append(Asset(
                        id=asset_id, type="image",
                        image_bytes=img_bytes, alt_text=text,
                        metadata={"src": src_href},
                    ))
                    blocks.append(Block(
                        type=BlockType.IMAGE, content=text,
                        page=chapter_count, index=idx,
                        metadata={"asset_id": asset_id},
                    ))
                    idx += 1
                    continue

                if total_chars + len(text) > _MAX_CONTENT_CHARS:
                    truncated = True
                    break
                blocks.append(Block(
                    type=btype,
                    content=text,
                    level=level,
                    page=chapter_count,
                    index=idx,
                ))
                idx += 1
                total_chars += len(text)

        if truncated:
            blocks.append(Block(
                type=BlockType.METADATA,
                content=(
                    f"[Truncated: {total_chars:,} of ~{sum(len(i.get_content()) for i in all_items):,} chars shown"
                    f" ({chapter_count} of {total_sections} sections)."
                    f" Full book has {total_sections} sections.]"
                ),
                index=idx,
            ))

        ctx.document = Document(
            source=str(path),
            file_type="epub",
            title=title,
            author=author,
            pages=total_sections,
            blocks=blocks,
            assets=doc_assets,
            metadata={"sections": total_sections, "truncated": truncated},
        ).compute_id()
        return ctx


register_parser("epub", EpubParser)
