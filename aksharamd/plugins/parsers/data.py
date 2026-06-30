from __future__ import annotations
import json as _json
try:
    import defusedxml.ElementTree as ET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET  # type: ignore[assignment]
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_MAX_JSON_CHARS = 40_000   # cap raw JSON shown as code block
_MAX_FEED_ITEMS = 50


# ── JSON / JSONL ──────────────────────────────────────────────────────────────

def _summarise_json(obj, depth: int = 0, max_depth: int = 3) -> list[str]:
    """Return human-readable lines describing a JSON structure."""
    lines = []
    if depth > max_depth:
        return ["..."]
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:20]:
            if isinstance(v, (dict, list)):
                lines.append(f"{'  '*depth}**{k}** ({type(v).__name__}, {len(v)} items)")
                lines.extend(_summarise_json(v, depth + 1, max_depth))
            else:
                val = str(v)[:120]
                lines.append(f"{'  '*depth}**{k}**: {val}")
    elif isinstance(obj, list):
        lines.append(f"{'  '*depth}[{len(obj)} items]")
        for item in obj[:3]:
            lines.extend(_summarise_json(item, depth + 1, max_depth))
        if len(obj) > 3:
            lines.append(f"{'  '*depth}... {len(obj)-3} more")
    else:
        lines.append(f"{'  '*depth}{str(obj)[:120]}")
    return lines


class JsonParser(ParserPlugin):
    name = "json_parser"
    supported_types = ["json"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import chardet

        path = Path(ctx.source)
        raw = path.read_bytes()
        enc = chardet.detect(raw).get("encoding") or "utf-8"
        text = raw.decode(enc, errors="replace")

        blocks: list[Block] = [
            Block(type=BlockType.METADATA,
                  content=f"File: {path.name} | Size: {len(raw):,} bytes",
                  index=0),
        ]

        try:
            obj = _json.loads(text)
            summary_lines = _summarise_json(obj)
            blocks.append(Block(
                type=BlockType.PARAGRAPH,
                content="\n".join(summary_lines[:200]),
                index=1,
            ))
            # Also include raw (truncated) for small files
            if len(text) <= _MAX_JSON_CHARS:
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=text[:_MAX_JSON_CHARS],
                    language="json",
                    index=2,
                ))
            else:
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=text[:_MAX_JSON_CHARS] + "\n... (truncated)",
                    language="json",
                    index=2,
                ))
        except _json.JSONDecodeError:
            blocks.append(Block(type=BlockType.CODE_BLOCK, content=text[:_MAX_JSON_CHARS], language="json", index=1))

        ctx.document = Document(
            source=str(path), file_type="json", title=path.stem,
            pages=1, blocks=blocks,
        ).compute_id()
        return ctx


class JsonlParser(ParserPlugin):
    name = "jsonl_parser"
    supported_types = ["jsonl", "ndjson"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import chardet

        path = Path(ctx.source)
        raw = path.read_bytes()
        enc = chardet.detect(raw).get("encoding") or "utf-8"
        lines = raw.decode(enc, errors="replace").splitlines()

        parsed_lines = []
        plain_lines: list[str] = []
        json_errors = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed_lines.append(_json.loads(line))
            except _json.JSONDecodeError:
                json_errors += 1
                plain_lines.append(line)

        # If all/most lines failed JSON parsing, treat as plain-text-per-line
        all_plain = len(parsed_lines) == 0 and plain_lines
        if all_plain:
            parsed_lines = plain_lines

        blocks: list[Block] = [
            Block(type=BlockType.METADATA,
                  content=f"File: {path.name} | Records: {len(parsed_lines)}",
                  index=0),
        ]

        if parsed_lines:
            first = parsed_lines[0]
            if all_plain or isinstance(first, str):
                # Plain-text records — emit as paragraphs (up to 50)
                for i, record in enumerate(parsed_lines[:50]):
                    text = record.strip() if isinstance(record, str) else str(record)
                    if text:
                        blocks.append(Block(type=BlockType.PARAGRAPH, content=text, index=i + 1))
            else:
                # Structured JSON — show schema + sample code block
                if isinstance(first, dict):
                    schema = "Keys: " + ", ".join(f"`{k}`" for k in list(first.keys())[:30])
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=schema, index=1))
                sample = _json.dumps(parsed_lines[:10], indent=2)
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=sample[:_MAX_JSON_CHARS],
                    language="json",
                    index=2,
                ))

        ctx.document = Document(
            source=str(path), file_type="jsonl", title=path.stem,
            pages=1, blocks=blocks,
            metadata={"records": len(parsed_lines)},
        ).compute_id()
        return ctx


# ── XML ───────────────────────────────────────────────────────────────────────

_XML_HEADING_TAGS = {
    "title", "heading", "head", "header", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "chapter", "part", "article-title", "source", "label",
    "name", "subtitle",
}
_XML_SKIP_TAGS = {
    "script", "style", "comment", "processing-instruction",
    "xref", "ref", "citation", "bibr", "ext-link",
}
_XML_MAX_BLOCKS = 250
_XML_MAX_RAW_CHARS = 8_000


def _local(tag: str) -> str:
    return tag.split("}")[-1].lower() if "}" in tag else tag.lower()


def _collect_text(el: ET.Element) -> str:
    """Recursively collect all text from an element and its descendants."""
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_collect_text(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(p.strip() for p in parts if p.strip())


def _xml_to_blocks(root: ET.Element) -> list[Block]:
    blocks: list[Block] = []
    idx = 0
    title: str | None = None

    def _walk(el: ET.Element, depth: int) -> None:
        nonlocal idx, title
        if idx >= _XML_MAX_BLOCKS:
            return

        local = _local(el.tag)
        if local in _XML_SKIP_TAGS:
            return

        # Heading elements → emit as heading, don't recurse further
        if local in _XML_HEADING_TAGS:
            text = _collect_text(el).strip()
            if text and len(text) < 300:
                level = max(1, min(6, depth + 1))
                if not title and level <= 2:
                    title = text
                blocks.append(Block(type=BlockType.HEADING, content=text, level=level, index=idx))
                idx += 1
                return

        # Leaf element with meaningful text → paragraph
        child_count = len(list(el))
        direct_text = (el.text or "").strip()

        if child_count == 0 and len(direct_text) > 10:
            blocks.append(Block(type=BlockType.PARAGRAPH, content=direct_text[:2000], index=idx))
            idx += 1
            return

        # Container element with mostly-text children → collect as paragraph
        if child_count > 0 and child_count <= 5:
            full_text = _collect_text(el).strip()
            if len(full_text) > 20 and len(full_text) < 1000 and full_text.count(" ") > 3:
                blocks.append(Block(type=BlockType.PARAGRAPH, content=full_text, index=idx))
                idx += 1
                return

        # Container element → recurse into children
        for child in el:
            _walk(child, depth + 1)

    _walk(root, 0)
    return blocks, title


class XmlParser(ParserPlugin):
    name = "xml_parser"
    supported_types = ["xml"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import chardet

        path = Path(ctx.source)
        raw = path.read_bytes()
        enc = chardet.detect(raw).get("encoding") or "utf-8"
        text = raw.decode(enc, errors="replace")

        blocks: list[Block] = [
            Block(type=BlockType.METADATA,
                  content=f"File: {path.name} | Size: {len(raw):,} bytes",
                  index=0),
        ]
        idx = 1

        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            ctx.error("XML_PARSE_ERROR", str(e))
            return ctx

        content_blocks, title = _xml_to_blocks(root)
        for b in content_blocks:
            blocks.append(b.model_copy(update={"index": idx}))
            idx += 1

        # If very few blocks extracted, fall back to raw snippet
        if len(content_blocks) < 2:
            blocks.append(Block(
                type=BlockType.CODE_BLOCK,
                content=text[:_XML_MAX_RAW_CHARS],
                language="xml",
                index=idx,
            ))

        ctx.document = Document(
            source=str(path), file_type="xml",
            title=title or path.stem,
            pages=1, blocks=blocks,
        ).compute_id()
        return ctx


# ── RSS / ATOM ────────────────────────────────────────────────────────────────

class FeedParser(ParserPlugin):
    name = "feed_parser"
    supported_types = ["rss", "atom"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import feedparser

        path = Path(ctx.source)
        raw = path.read_bytes()

        feed = feedparser.parse(raw)
        ext = path.suffix.lstrip(".").lower()

        feed_title = feed.feed.get("title", path.stem)
        feed_desc  = feed.feed.get("description") or feed.feed.get("subtitle", "")
        feed_link  = feed.feed.get("link", "")

        blocks: list[Block] = [
            Block(type=BlockType.HEADING, content=feed_title, level=1, index=0),
        ]
        if feed_desc:
            blocks.append(Block(type=BlockType.PARAGRAPH, content=feed_desc, index=1))
        if feed_link:
            blocks.append(Block(type=BlockType.METADATA, content=f"Source: {feed_link}", index=2))

        idx = len(blocks)
        for entry in feed.entries[:_MAX_FEED_ITEMS]:
            entry_title   = entry.get("title", "").strip()
            entry_summary = (entry.get("summary") or entry.get("content", [{}])[0].get("value", "")).strip()
            entry_link    = entry.get("link", "")
            entry_date    = entry.get("published") or entry.get("updated", "")

            if entry_title:
                blocks.append(Block(type=BlockType.HEADING, content=entry_title, level=2, index=idx))
                idx += 1
            if entry_date or entry_link:
                meta = " | ".join(filter(None, [entry_date, entry_link]))
                blocks.append(Block(type=BlockType.METADATA, content=meta, index=idx))
                idx += 1
            if entry_summary:
                from bs4 import BeautifulSoup
                clean = BeautifulSoup(entry_summary, "html.parser").get_text(separator=" ", strip=True)
                if clean:
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=clean[:2000], index=idx))
                    idx += 1

        ctx.document = Document(
            source=str(path), file_type=ext,
            title=feed_title,
            pages=1, blocks=blocks,
            metadata={"entries": len(feed.entries)},
        ).compute_id()
        return ctx


register_parser("json",  JsonParser)
register_parser("jsonl", JsonlParser)
register_parser("ndjson", JsonlParser)
register_parser("xml",   XmlParser)
register_parser("rss",   FeedParser)
register_parser("atom",  FeedParser)
