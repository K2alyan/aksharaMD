from __future__ import annotations

import functools
import json
import shutil
import subprocess
from pathlib import Path

from ...context import CompilationContext
from ...models.block import Block, BlockType, ExtractionConfidence
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser


@functools.lru_cache(maxsize=1)
def _detect_pandoc() -> tuple[bool, str]:
    """Returns (available: bool, version: str). Cached after first call."""
    path = shutil.which("pandoc")
    if not path:
        return False, ""
    try:
        r = subprocess.run(
            ["pandoc", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            first_line = r.stdout.splitlines()[0]  # "pandoc 3.2.1"
            version = first_line.split()[-1]
            return True, version
    except Exception:
        pass
    return False, ""


# Pandoc --from argument keyed by file extension (without leading dot)
_FORMAT_MAP: dict[str, str] = {
    "adoc":      "asciidoc",
    "asciidoc":  "asciidoc",
    "org":       "org",
    "textile":   "textile",
    "wiki":      "mediawiki",
    "mediawiki": "mediawiki",
    "opml":      "opml",
    "docbook":   "docbook",
    "dbk":       "docbook",
    "man":       "man",
    "roff":      "man",
}


def _inlines_to_text(inlines: list) -> str:
    """Recursively convert Pandoc inline elements to plain/Markdown text."""
    parts: list[str] = []
    for node in inlines:
        t = node.get("t", "")
        c = node.get("c", "")

        if t == "Str":
            parts.append(c)
        elif t == "Space":
            parts.append(" ")
        elif t in ("SoftBreak", "LineBreak"):
            parts.append(" ")
        elif t == "Code":
            # c = [attrs, code_str]
            code_str = c[1] if isinstance(c, list) and len(c) >= 2 else str(c)
            parts.append(f"`{code_str}`")
        elif t == "Strong":
            inner = _inlines_to_text(c if isinstance(c, list) else [])
            parts.append(f"**{inner}**")
        elif t == "Emph":
            inner = _inlines_to_text(c if isinstance(c, list) else [])
            parts.append(f"*{inner}*")
        elif t == "Link":
            # c = [attrs, [inlines], [url, title]]
            if isinstance(c, list) and len(c) >= 3:
                text = _inlines_to_text(c[1] if isinstance(c[1], list) else [])
                target = c[2]
                url = target[0] if isinstance(target, list) and target else ""
                parts.append(f"[{text}]({url})")
            else:
                parts.append("")
        elif t == "Image":
            # c = [attrs, [inlines], [url, title]]
            if isinstance(c, list) and len(c) >= 3:
                alt = _inlines_to_text(c[1] if isinstance(c[1], list) else [])
                target = c[2]
                src = target[0] if isinstance(target, list) and target else ""
                parts.append(f"![{alt}]({src})")
            else:
                parts.append("")
        elif t == "RawInline":
            # c = [format, raw_text]
            raw_text = c[1] if isinstance(c, list) and len(c) >= 2 else ""
            parts.append(raw_text)
        elif t == "Math":
            # c = [MathType, math_str]
            math_str = c[1] if isinstance(c, list) and len(c) >= 2 else str(c)
            parts.append(f"${math_str}$")
        elif t == "Quoted":
            # c = [QuoteType, [inlines]]
            inner_inlines = c[1] if isinstance(c, list) and len(c) >= 2 else []
            parts.append(_inlines_to_text(inner_inlines))
        elif t == "Span":
            # c = [attrs, [inlines]]
            inner_inlines = c[1] if isinstance(c, list) and len(c) >= 2 else []
            parts.append(_inlines_to_text(inner_inlines))
        else:
            # Unknown inline — recurse into c if it's a list
            if isinstance(c, list):
                # It may be a flat list of inline nodes or nested
                # Try to handle both: list of inline dicts, or list of lists
                try:
                    parts.append(_inlines_to_text(c))
                except Exception:
                    pass

    return "".join(parts)


def _cell_text(cell: list) -> str:
    """Extract text from a Pandoc v3 table cell = [attr, alignment, rowspan, colspan, [blocks]]."""
    if not isinstance(cell, list) or len(cell) < 5:
        return ""
    cell_blocks = cell[4]
    sub_blocks = _walk_blocks(cell_blocks, set())
    return " ".join(b.content for b in sub_blocks).strip()


def _render_table(c: list) -> str:
    """
    Render a Pandoc v3 Table node as Markdown.

    c = [attr, caption, colspecs, head, bodies, foot]
    head = [attr, [rows]]      row = [attr, [cells]]
    bodies = [body, ...]       body = [attr, row_head_cols, head_rows, body_rows]
    """
    try:
        # c[3] = head = [attr, [rows]]
        head = c[3]
        head_rows = head[1]

        col_count = 0
        header_cells: list[str] = []
        if head_rows:
            first_row = head_rows[0]
            cells = first_row[1]  # [attr, [cells]] → cells
            header_cells = [_cell_text(cell) for cell in cells]
            col_count = len(header_cells)

        if col_count == 0:
            return ""

        separator = "| " + " | ".join("---" for _ in header_cells) + " |"
        header_line = "| " + " | ".join(header_cells) + " |"

        lines: list[str] = [header_line, separator]

        # c[4] = bodies = [body, ...]
        bodies = c[4]
        if bodies:
            first_body = bodies[0]
            # body = [attr, row_head_cols, head_rows, body_rows]
            body_rows = first_body[3]
            for row in body_rows:
                row_cells = row[1]
                row_texts = [_cell_text(cell) for cell in row_cells]
                # Pad or trim to col_count for alignment
                while len(row_texts) < col_count:
                    row_texts.append("")
                row_texts = row_texts[:col_count]
                lines.append("| " + " | ".join(row_texts) + " |")

        return "\n".join(lines)
    except Exception:
        return f"[Table: {repr(c)[:200]}]"


def _walk_blocks(pandoc_blocks: list, unsupported: set) -> list[Block]:
    """Walk a list of Pandoc block nodes and return AksharaMD Block instances."""
    blocks: list[Block] = []

    for node in pandoc_blocks:
        if not isinstance(node, dict):
            continue
        t = node.get("t", "")
        c = node.get("c", None)

        if t == "Header":
            # c = [level, attrs, inlines]
            if isinstance(c, list) and len(c) >= 3:
                level = int(c[0])
                inlines = c[2] if isinstance(c[2], list) else []
                text = _inlines_to_text(inlines).strip()
                if text:
                    blocks.append(Block(
                        type=BlockType.HEADING,
                        content=text,
                        level=max(1, min(6, level)),
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        elif t in ("Para", "Plain"):
            # c = [inlines]
            inlines = c if isinstance(c, list) else []
            text = _inlines_to_text(inlines).strip()
            if text:
                blocks.append(Block(
                    type=BlockType.PARAGRAPH,
                    content=text,
                    index=len(blocks),
                    confidence=ExtractionConfidence.EXTRACTED,
                ))

        elif t == "BulletList":
            # c = [[blocks], [blocks], ...]  — each item is a list of block nodes
            if isinstance(c, list):
                lines: list[str] = []
                for item_blocks in c:
                    sub = _walk_blocks(item_blocks if isinstance(item_blocks, list) else [], unsupported)
                    item_text = sub[0].content if sub else ""
                    lines.append(f"- {item_text}")
                content = "\n".join(lines)
                if content.strip():
                    blocks.append(Block(
                        type=BlockType.LIST,
                        content=content,
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        elif t == "OrderedList":
            # c = [attrs, [[blocks], [blocks], ...]]
            if isinstance(c, list) and len(c) >= 2:
                items = c[1]
                lines = []
                for n, item_blocks in enumerate(items, start=1):
                    sub = _walk_blocks(item_blocks if isinstance(item_blocks, list) else [], unsupported)
                    item_text = sub[0].content if sub else ""
                    lines.append(f"{n}. {item_text}")
                content = "\n".join(lines)
                if content.strip():
                    blocks.append(Block(
                        type=BlockType.LIST,
                        content=content,
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        elif t == "CodeBlock":
            # c = [[id, classes, kvs], code_str]
            if isinstance(c, list) and len(c) >= 2:
                attrs = c[0]
                code_str = c[1]
                language: str | None = None
                if isinstance(attrs, list) and len(attrs) >= 2:
                    classes = attrs[1]
                    if isinstance(classes, list) and classes:
                        language = classes[0] or None
                blocks.append(Block(
                    type=BlockType.CODE_BLOCK,
                    content=code_str,
                    language=language,
                    index=len(blocks),
                    confidence=ExtractionConfidence.EXTRACTED,
                ))

        elif t == "BlockQuote":
            # c = [blocks]
            if isinstance(c, list):
                sub = _walk_blocks(c, unsupported)
                if sub:
                    quoted_content = "\n\n".join(b.content for b in sub)
                    blocks.append(Block(
                        type=BlockType.BLOCKQUOTE,
                        content=quoted_content,
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        elif t == "Table":
            if isinstance(c, list):
                md_table = _render_table(c)
                if md_table.strip():
                    blocks.append(Block(
                        type=BlockType.TABLE,
                        content=md_table,
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        elif t == "Image":
            # At block level (rare) — c = [attrs, [inlines], [url, title]]
            if isinstance(c, list) and len(c) >= 3:
                alt = _inlines_to_text(c[1] if isinstance(c[1], list) else [])
                target = c[2]
                src = target[0] if isinstance(target, list) and target else ""
                blocks.append(Block(
                    type=BlockType.IMAGE,
                    content=f"![{alt}]({src})",
                    index=len(blocks),
                    confidence=ExtractionConfidence.EXTRACTED,
                ))

        elif t == "HorizontalRule":
            pass  # intentionally skipped

        elif t == "Div":
            # c = [attrs, [blocks]] — transparent container, recurse into inner blocks
            if isinstance(c, list) and len(c) >= 2:
                inner = c[1] if isinstance(c[1], list) else []
                sub = _walk_blocks(inner, unsupported)
                blocks.extend(sub)

        elif t == "RawBlock":
            # c = [format, raw_str]
            if isinstance(c, list) and len(c) >= 2:
                fmt = c[0]
                raw_str = c[1]
                if fmt in ("html", "markdown") and raw_str.strip():
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content=raw_str.strip(),
                        index=len(blocks),
                        confidence=ExtractionConfidence.INFERRED,
                    ))
            # other raw formats are silently skipped

        elif t == "LineBlock":
            # c = [[inlines], [inlines], ...]  — line-level block (poetry, addresses)
            if isinstance(c, list):
                lines = [_inlines_to_text(line).strip() for line in c if isinstance(line, list)]
                content = "\n".join(ln for ln in lines if ln)
                if content:
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content=content,
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        elif t == "DefinitionList":
            # c = [[[inlines], [[blocks], ...]], ...]
            if isinstance(c, list):
                parts_out: list[str] = []
                for entry in c:
                    if not isinstance(entry, list) or len(entry) < 2:
                        continue
                    term = _inlines_to_text(entry[0] if isinstance(entry[0], list) else []).strip()
                    defs_raw = entry[1] if isinstance(entry[1], list) else []
                    defs: list[str] = []
                    for def_blocks in defs_raw:
                        sub = _walk_blocks(def_blocks if isinstance(def_blocks, list) else [], unsupported)
                        defs.append(" ".join(b.content for b in sub))
                    if term:
                        parts_out.append(f"**{term}**: {'; '.join(defs)}")
                content = "\n".join(parts_out)
                if content:
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content=content,
                        index=len(blocks),
                        confidence=ExtractionConfidence.EXTRACTED,
                    ))

        else:
            if t:  # ignore empty-type nodes
                unsupported.add(t)

    return blocks


class PandocParser(ParserPlugin):
    """Parse niche markup formats (AsciiDoc, Org-mode, Textile, MediaWiki, DocBook, OPML, man/roff)
    by delegating to the system Pandoc binary and walking its JSON AST."""

    name = "pandoc_parser"
    priority = 50

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        available, version = _detect_pandoc()

        source_path = Path(ctx.source)
        ext = source_path.suffix.lower().lstrip(".")

        if not available:
            ctx.error(
                "PANDOC_UNAVAILABLE",
                f"Pandoc binary not found on PATH. Install Pandoc to parse .{ext} files: "
                "https://pandoc.org/installing.html",
            )
            return ctx

        pandoc_format = _FORMAT_MAP.get(ext, "")
        if not pandoc_format:
            ctx.error(
                "PANDOC_FORMAT_UNKNOWN",
                f"No Pandoc --from format configured for extension: .{ext}",
            )
            return ctx

        # ── Run Pandoc ────────────────────────────────────────────────────────
        try:
            result = subprocess.run(
                ["pandoc", "--from", pandoc_format, "--to", "json", str(source_path)],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            ctx.error("PANDOC_TIMEOUT", f"Pandoc timed out processing {source_path.name}")
            return ctx
        except Exception as exc:
            ctx.error("PANDOC_EXEC_ERROR", f"Failed to run Pandoc: {exc}")
            return ctx

        if result.returncode != 0:
            ctx.error(
                "PANDOC_FAILED",
                f"Pandoc exited {result.returncode}: {result.stderr[:500]}",
            )
            return ctx

        # ── Parse JSON AST ────────────────────────────────────────────────────
        try:
            ast = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            ctx.error(
                "PANDOC_INVALID_JSON",
                f"Pandoc returned invalid JSON: {exc}",
            )
            return ctx

        # ── Walk AST ──────────────────────────────────────────────────────────
        unsupported: set[str] = set()
        raw_blocks = ast.get("blocks", []) if isinstance(ast, dict) else []
        blocks = _walk_blocks(raw_blocks, unsupported)

        # Re-index after collection (index values assigned during walk reflect insertion
        # order within sub-calls; re-number sequentially for the final flat list)
        for i, block in enumerate(blocks):
            block.index = i

        # ── Build Document ────────────────────────────────────────────────────
        ctx.document = Document(
            source=str(source_path),
            file_type=ext,
            pages=1,
            blocks=blocks,
            metadata={
                "parser_backend": "pandoc",
                "pandoc_version": version,
                "pandoc_source_format": pandoc_format,
                "unsupported_node_types": sorted(unsupported),
            },
        )
        ctx.document.compute_id()
        return ctx


# ── Registration ──────────────────────────────────────────────────────────────

_FORMATS = [
    "adoc", "asciidoc", "org", "textile",
    "wiki", "mediawiki", "opml",
    "docbook", "dbk", "man", "roff",
]

for _ext in _FORMATS:
    register_parser(_ext, PandocParser)
