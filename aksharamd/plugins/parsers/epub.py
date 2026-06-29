from __future__ import annotations
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SKIP_TAGS = {"script", "style", "nav", "aside"}


def _parse_html_content(html_bytes: bytes) -> list[tuple[BlockType, str, int | None]]:
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup.find_all(_SKIP_TAGS):
        tag.decompose()

    results: list[tuple[BlockType, str, int | None]] = []
    body = soup.find("body") or soup

    for el in body.descendants:
        if not isinstance(el, Tag):
            continue
        name = (el.name or "").lower()

        if name in _HEADING_TAGS:
            text = el.get_text(strip=True)
            if text:
                results.append((BlockType.HEADING, text, _HEADING_TAGS[name]))

        elif name == "p":
            text = el.get_text(separator=" ", strip=True)
            if text and len(text) > 10:
                results.append((BlockType.PARAGRAPH, text, None))

        elif name in ("pre", "code"):
            text = el.get_text()
            if text.strip():
                results.append((BlockType.CODE_BLOCK, text.strip(), None))

        elif name in ("li",):
            text = el.get_text(strip=True)
            if text:
                results.append((BlockType.LIST, f"- {text}", None))

    return results


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

        # Metadata
        titles = book.get_metadata("DC", "title")
        if titles:
            title = titles[0][0]
        authors = book.get_metadata("DC", "creator")
        if authors:
            author = authors[0][0]

        blocks: list[Block] = []
        idx = 0
        chapter_count = 0

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content()
            if not content:
                continue

            parsed = _parse_html_content(content)
            if not parsed:
                continue

            chapter_count += 1
            for btype, text, level in parsed:
                blocks.append(Block(
                    type=btype,
                    content=text,
                    level=level,
                    page=chapter_count,
                    index=idx,
                ))
                idx += 1

        ctx.document = Document(
            source=str(path),
            file_type="epub",
            title=title,
            author=author,
            pages=chapter_count,
            blocks=blocks,
            metadata={"chapters": chapter_count},
        ).compute_id()
        return ctx


register_parser("epub", EpubParser)
