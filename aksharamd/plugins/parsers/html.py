from __future__ import annotations

import base64 as _b64
import re
from pathlib import Path

import chardet
from bs4 import BeautifulSoup, NavigableString, Tag

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.block import Block, BlockType, ExtractionConfidence
from ...models.document import Document
from ...models.table import ExtractionMethod, TableCell, TableData
from ..base import ParserPlugin
from ..registry import register_parser

_MAX_LOCAL_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB cap for local images


def _extract_image_bytes(src: str, source_path: Path | None) -> bytes | None:
    if not src:
        return None
    if src.startswith("data:"):
        try:
            header, encoded = src.split(",", 1)
            mime = header.split(";")[0].replace("data:", "").lower()
            if not mime.startswith("image/") or mime in ("image/svg+xml",):
                return None
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


_GH_ADMONITION_RE = re.compile(r"^\[!(NOTE|WARNING|TIP|IMPORTANT|CAUTION|DANGER)\]", re.IGNORECASE)
_ADMONITION_CLASSES = frozenset({"note", "warning", "tip", "important", "caution", "danger", "error", "admonition"})

_SKIP_TAGS = {
    "nav", "header", "aside", "script", "style",
    "noscript", "iframe", "form", "menu", "menuitem",
}

# CSS class patterns for navigation/boilerplate elements not captured by tag names.
# Covers Wikipedia navboxes, hatnotes, MediaWiki edit-section links, and common
# sidebar/noprint patterns used across many CMS platforms.
_SKIP_CLASSES = re.compile(
    r"\b(navbox|navbox-inner|navbox-subgroup|hatnote|"
    r"sidebar|mw-editsection|noprint|navigation-not-searchable)\b"
)
# footer is NOT skipped — it often contains meaningful metadata (dates, org names,
# copyright). Navigation noise inside footers is handled by the cleaner stage.
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_CONTAINER_TAGS = {
    "div", "section", "article", "main", "body", "figure",
    "details", "summary", "span", "label",
}
_STRUCTURAL_TAGS = (
    set(_HEADING_TAGS) | {"p", "pre", "table", "ul", "ol", "blockquote", "img", "hr", "dl"}
)


def _read_file(path: Path) -> str:
    raw = path.read_bytes()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def _html_table_to_tabledata(table: Tag, page: int | None = None) -> TableData | None:
    """Parse an HTML <table> into TableData with rowspan/colspan normalization."""
    # Collect rows in document order: thead rows, then tbody rows, then tfoot rows
    all_trs: list[Tag] = []
    thead_row_count = 0

    thead = table.find('thead')
    tbody = table.find('tbody')
    tfoot = table.find('tfoot')

    if thead and isinstance(thead, Tag):
        thead_trs = [tr for tr in thead.find_all('tr') if isinstance(tr, Tag)]
        thead_row_count = len(thead_trs)
        all_trs.extend(thead_trs)
    if tbody and isinstance(tbody, Tag):
        all_trs.extend(tr for tr in tbody.find_all('tr') if isinstance(tr, Tag))
    if tfoot and isinstance(tfoot, Tag):
        all_trs.extend(tr for tr in tfoot.find_all('tr') if isinstance(tr, Tag))

    # Fallback: direct tr children if no sections
    if not all_trs:
        all_trs = [tr for tr in table.find_all('tr') if isinstance(tr, Tag)]

    if not all_trs:
        return None

    # Grid normalization: track occupied positions for rowspan/colspan
    grid_occupied: dict[tuple[int, int], bool] = {}
    table_cells: list[TableCell] = []
    max_col = 0
    row_count = len(all_trs)

    for r_idx, tr in enumerate(all_trs):
        c_idx = 0
        for cell_tag in tr.find_all(['th', 'td'], recursive=False):
            if not isinstance(cell_tag, Tag):
                continue
            # Advance past occupied positions
            while grid_occupied.get((r_idx, c_idx)):
                c_idx += 1

            try:
                colspan = max(1, int(cell_tag.get('colspan', 1)))
            except (ValueError, TypeError):
                colspan = 1
            try:
                rowspan = max(1, int(cell_tag.get('rowspan', 1)))
            except (ValueError, TypeError):
                rowspan = 1
            # Cap spans to prevent runaway
            colspan = min(colspan, 50)
            rowspan = min(rowspan, 50)

            cell_text = cell_tag.get_text(separator=' ', strip=True)
            is_th = cell_tag.name == 'th'

            # Mark covered positions
            for r in range(r_idx, r_idx + rowspan):
                for c in range(c_idx, c_idx + colspan):
                    if (r, c) != (r_idx, c_idx):
                        grid_occupied[(r, c)] = True

            max_col = max(max_col, c_idx + colspan)
            table_cells.append(TableCell(
                text=cell_text,
                row=r_idx,
                column=c_idx,
                row_span=rowspan,
                column_span=colspan,
                is_header=is_th,
            ))
            c_idx += colspan

    if not table_cells:
        return None

    col_count = max_col

    # Determine header_rows and header_detection
    header_rows: list[int] = []
    header_detection: str = "unknown"

    if thead_row_count > 0:
        header_rows = list(range(thead_row_count))
        header_detection = "native"
    else:
        # Detect rows where ALL cells are <th>
        row_cells: dict[int, list[TableCell]] = {}
        for cell in table_cells:
            row_cells.setdefault(cell.row, []).append(cell)
        for r, rcells in sorted(row_cells.items()):
            if rcells and all(c.is_header for c in rcells):
                header_rows.append(r)
        if header_rows:
            header_detection = "native"

    if not header_rows:
        header_detection = "unknown"

    return TableData(
        row_count=row_count,
        column_count=col_count,
        cells=table_cells,
        header_rows=header_rows,
        header_detection=header_detection,  # type: ignore[arg-type]
        span_detection="native",
        extraction_method=ExtractionMethod.HTML_NATIVE,
        page=page,
    )


def _list_to_lines(element: Tag, ordered: bool, depth: int = 0) -> list[str]:
    lines = []
    indent = "  " * depth
    for i, li in enumerate(element.find_all("li", recursive=False)):
        if not isinstance(li, Tag):
            continue
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
            if not isinstance(nested, Tag):
                continue
            lines.extend(_list_to_lines(nested, ordered=(nested.name == "ol"), depth=depth + 1))
    return lines


_MAX_DEPTH = 100  # guard against pathologically nested HTML causing RecursionError


def _dl_to_key_value_group(dl_tag: Tag, page: "int | None") -> "object | None":
    """Parse a <dl> element into a KeyValueGroup. Returns None if insufficient entries."""
    from ...models.key_value import KeyValueEntry, KeyValueGroup, KeyValueGroupType

    entries = []
    current_key: str | None = None

    for child in dl_tag.children:
        if not isinstance(child, Tag):
            continue
        tag = (child.name or "").lower()
        if tag == "dt":
            current_key = child.get_text(separator=" ", strip=True)
        elif tag == "dd" and current_key is not None:
            value = child.get_text(separator=" ", strip=True)
            if value and len(value) <= 200:
                entries.append(KeyValueEntry(
                    key=current_key,
                    value=value,
                    page=page,
                    confidence="extracted",
                ))
            current_key = None

    if len(entries) < 1:
        return None

    return KeyValueGroup(
        entries=entries,
        extraction_method="html.definition_list",
        confidence="extracted",
        page=page,
    )

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
            if code and isinstance(code, Tag):
                raw_classes: list | str = code.get("class") or []
                classes = raw_classes if isinstance(raw_classes, list) else [str(raw_classes)]
                for cls in classes:
                    if isinstance(cls, str) and cls.startswith("language-"):
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
            table_data = _html_table_to_tabledata(child)
            if table_data is not None:
                blocks.append(Block.from_table(table_data, index=idx[0]))
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

        # ── Blockquotes / Admonitions ──────────────────────────────────────────
        elif tag == "blockquote":
            text = child.get_text(separator=" ", strip=True)
            if text:
                # Detect admonition by CSS class (MkDocs, Sphinx, Python-Markdown)
                css: list[str] = []
                _cls = child.get("class")
                if isinstance(_cls, str):
                    css = _cls.split()
                elif _cls:
                    css = list(_cls)
                admonition_type: str | None = None
                for cls in css:
                    if cls.lower() in _ADMONITION_CLASSES and cls.lower() != "admonition":
                        admonition_type = cls.lower()
                        break
                # Detect admonition by GitHub/Obsidian [!TYPE] first-paragraph pattern
                if admonition_type is None:
                    first_p = child.find("p")
                    first_text = (first_p.get_text(strip=True) if first_p else "").strip()
                    m = _GH_ADMONITION_RE.match(first_text)
                    if m:
                        admonition_type = m.group(1).lower()
                if admonition_type is not None:
                    blocks.append(Block(
                        type=BlockType.ADMONITION,
                        content=text,
                        index=idx[0],
                        metadata={"admonition_type": admonition_type},
                    ))
                else:
                    blocks.append(Block(
                        type=BlockType.BLOCKQUOTE,
                        content=text,
                        index=idx[0],
                    ))
                idx[0] += 1

        # ── Images ─────────────────────────────────────────────────────────────
        elif tag == "img":
            _src_raw = child.get("src", "")
            _alt_raw = child.get("alt", "")
            src: str = _src_raw if isinstance(_src_raw, str) else ""
            alt: str | None = _alt_raw if isinstance(_alt_raw, str) else None
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

        # ── Definition lists → KeyValueGroup ───────────────────────────────────
        elif tag == "dl":
            group = _dl_to_key_value_group(child, page=None)
            if group is not None and len(group.entries) >= 1:
                kv_block = Block.from_key_value_group(
                    group,
                    page=None,
                    index=idx[0],
                    confidence=ExtractionConfidence.EXTRACTED,
                )
                blocks.append(kv_block)
                idx[0] += 1
            else:
                # Fall back to walking children for <dl> with insufficient entries
                _walk(child, blocks, assets, idx, depth + 1, source_path)

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

        # Remove navigation/boilerplate elements identified by CSS class rather than tag name
        for tag in soup.find_all(True, class_=_SKIP_CLASSES):
            tag.decompose()

        blocks: list[Block] = []
        assets: list[Asset] = []
        idx = [0]

        _body = soup.find("body") or soup
        body: Tag = _body if isinstance(_body, Tag) else soup
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
