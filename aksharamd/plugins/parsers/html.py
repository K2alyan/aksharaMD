from __future__ import annotations

import base64 as _b64
from pathlib import Path

import chardet
from bs4 import BeautifulSoup, NavigableString, Tag

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_MAX_LOCAL_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB cap for local images


def _extract_image_bytes(src: str, source_path: Path | None) -> bytes | None:
    if not src:
        return None
    if src.startswith("data:"):
        try:
            _, encoded = src.split(",", 1)
            # Add padding in case it's missing
            encoded += "=" * (-len(encoded) % 4)
            return _b64.b64decode(encoded)
        except Exception:
            return None
    if src.startswith(("http://", "https://")):
        return None  # don't fetch remote URLs — keeps MCP server lightweight
    if source_path is not None:
        try:
            safe_root = source_path.parent.resolve()
            img_path = (safe_root / src).resolve()
            # Guard against path traversal via symlinks or ../.. sequences
            if not img_path.is_relative_to(safe_root):
                return None
            if img_path.exists() and img_path.is_file():
                data = img_path.read_bytes()
                return data if len(data) <= _MAX_LOCAL_IMAGE_BYTES else None
        except Exception:
            pass
    return None


_SKIP_TAGS = {
    "nav", "header", "aside", "script", "style",
    "noscript", "iframe", "form", "menu", "menuitem",
}
# footer is NOT skipped — it often contains meaningful metadata (dates, org names,
# copyright). Navigation noise inside footers is handled by the cleaner stage.
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_CONTAINER_TAGS = {
    "div", "section", "article", "main", "body", "figure",
    "details", "summary", "dl", "dt", "dd", "span", "label",
}
_STRUCTURAL_TAGS = (
    set(_HEADING_TAGS) | {"p", "pre", "table", "ul", "ol", "blockquote", "img", "hr"}
)


def _read_file(path: Path) -> str:
    raw = path.read_bytes()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def _table_to_markdown(table: Tag) -> str:
    rows = []
    for row in table.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all(["th", "td"])]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    sep_count = rows[0].count("|") - 1
    sep = "| " + " | ".join(["---"] * sep_count) + " |"
    return "\n".join([rows[0], sep] + rows[1:])


def _list_to_lines(element: Tag, ordered: bool, depth: int = 0) -> list[str]:
    lines = []
    indent = "  " * depth
    for i, li in enumerate(element.find_all("li", recursive=False)):
        # Collect direct text only (skip nested ul/ol content to avoid duplication)
        direct_parts: list[str] = []
        for child in li.children:
            if isinstance(child, NavigableString):
                t = child.strip()
                if t:
                    direct_parts.append(t)
            elif isinstance(child, Tag) and child.name not in ("ul", "ol"):
                t = child.get_text(separator=" ", strip=True)
                if t:
                    direct_parts.append(t)
        direct_text = " ".join(direct_parts).strip()
        if direct_text:
            prefix = f"{i + 1}." if ordered else "-"
            lines.append(f"{indent}{prefix} {direct_text}")
        # Recurse into nested lists
        for nested in li.find_all(["ul", "ol"], recursive=False):
            lines.extend(_list_to_lines(nested, ordered=(nested.name == "ol"), depth=depth + 1))
    return lines


_MAX_DEPTH = 100  # guard against pathologically nested HTML causing RecursionError

# Tags that are purely inline — never emit as standalone paragraphs
_INLINE_TAGS = {
    "a", "em", "strong", "b", "i", "u", "s", "cite", "abbr",
    "time", "mark", "sup", "sub", "small", "kbd", "var",
}

# All tags that carry block-level structure (used for leaf-container detection)
_BLOCK_TAGS = _STRUCTURAL_TAGS | _CONTAINER_TAGS


def _has_block_children(el: Tag) -> bool:
    return any(isinstance(c, Tag) and c.name in _BLOCK_TAGS for c in el.children)


def _walk(
    element: Tag,
    blocks: list[Block],
    assets: list[Asset],
    idx: list[int],
    depth: int = 0,
    source_path: Path | None = None,
) -> None:
    """
    Recursive traversal that processes direct children one at a time.
    When a structural element is handled, we do NOT descend into its children —
    preventing the double-emit bug from body.descendants.
    Container tags (div, section, article, …) are transparent: we recurse through them.
    """
    if depth > _MAX_DEPTH:
        return
    for child in element.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue

        tag = (child.name or "").lower()

        if tag in _SKIP_TAGS:
            continue

        # ── Headings ───────────────────────────────────────────────────────────
        if tag in _HEADING_TAGS:
            text = child.get_text(strip=True)
            if text:
                blocks.append(Block(
                    type=BlockType.HEADING,
                    content=text,
                    level=_HEADING_TAGS[tag],
                    index=idx[0],
                ))
                idx[0] += 1

        # ── Paragraphs ─────────────────────────────────────────────────────────
        elif tag == "p":
            text = child.get_text(separator=" ", strip=True)
            if text:
                blocks.append(Block(
                    type=BlockType.PARAGRAPH,
                    content=text,
                    index=idx[0],
                ))
                idx[0] += 1

        # ── Code / pre ─────────────────────────────────────────────────────────
        elif tag == "pre":
            code = child.find("code")
            source = code or child
            text = source.get_text()
            lang = None
            if code:
                classes = code.get("class") or []
                for cls in classes:
                    if cls.startswith("language-"):
                        lang = cls.replace("language-", "")
                        break
            if text.strip():
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=text,
                    language=lang,
                    index=idx[0],
                ))
                idx[0] += 1

        elif tag == "code" and (child.parent and child.parent.name != "pre"):
            text = child.get_text()
            if text.strip():
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=text,
                    index=idx[0],
                ))
                idx[0] += 1

        # ── Tables ─────────────────────────────────────────────────────────────
        elif tag == "table":
            md = _table_to_markdown(child)
            if md:
                blocks.append(Block(
                    type=BlockType.TABLE,
                    content=md,
                    index=idx[0],
                ))
                idx[0] += 1

        # ── Lists ──────────────────────────────────────────────────────────────
        elif tag in ("ul", "ol"):
            lines = _list_to_lines(child, ordered=(tag == "ol"))
            if lines:
                blocks.append(Block(
                    type=BlockType.LIST,
                    content="\n".join(lines),
                    index=idx[0],
                ))
                idx[0] += 1

        # ── Blockquotes ────────────────────────────────────────────────────────
        elif tag == "blockquote":
            text = child.get_text(separator=" ", strip=True)
            if text:
                blocks.append(Block(
                    type=BlockType.BLOCKQUOTE,
                    content=text,
                    index=idx[0],
                ))
                idx[0] += 1

        # ── Images ─────────────────────────────────────────────────────────────
        elif tag == "img":
            src = child.get("src", "")
            alt = child.get("alt", "")
            if src or alt:
                asset_id = f"img_{idx[0]}"
                img_bytes = _extract_image_bytes(src, source_path)
                assets.append(Asset(
                    id=asset_id,
                    type="image",
                    alt_text=alt,
                    image_bytes=img_bytes,
                    metadata={"src": src},
                ))
                blocks.append(Block(
                    type=BlockType.IMAGE,
                    content=alt or src,
                    index=idx[0],
                    metadata={"asset_id": asset_id, "src": src},
                ))
                idx[0] += 1

        # ── Horizontal rule → page break ───────────────────────────────────────
        elif tag == "hr":
            blocks.append(Block(type=BlockType.PAGE_BREAK, content="", index=idx[0]))
            idx[0] += 1

        # ── Container — transparent, recurse ───────────────────────────────────
        elif tag in _CONTAINER_TAGS or tag not in _STRUCTURAL_TAGS:
            if tag in _INLINE_TAGS:
                continue
            if _has_block_children(child):
                _walk(child, blocks, assets, idx, depth + 1, source_path)
            else:
                # Leaf container: only text + inline tags — emit full text directly
                text = child.get_text(separator=" ", strip=True)
                if text and len(text) > 15:
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content=text,
                        index=idx[0],
                    ))
                    idx[0] += 1


class HTMLParser(ParserPlugin):
    name = "html_parser"
    supported_types = ["html", "htm"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        html = _read_file(path)
        soup = BeautifulSoup(html, "html.parser")

        title: str | None = None
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # Remove boilerplate before traversal
        for tag in soup.find_all(_SKIP_TAGS):
            tag.decompose()

        blocks: list[Block] = []
        assets: list[Asset] = []
        idx = [0]

        body = soup.find("body") or soup
        _walk(body, blocks, assets, idx, source_path=path)

        doc = Document(
            source=str(path),
            file_type="html",
            title=title,
            pages=1,
            blocks=blocks,
            assets=assets,
        )
        doc.compute_id()
        ctx.document = doc
        return ctx


register_parser("html", HTMLParser)
register_parser("htm", HTMLParser)
