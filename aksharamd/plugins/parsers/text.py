from __future__ import annotations

import re
from pathlib import Path

import chardet

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

# Budget for large prose files — keeps output under ~12k tokens for full novels
_MAX_CONTENT_CHARS = 50_000

# Heading patterns common in plain-text books (Gutenberg, etc.)
_CHAPTER_RE = re.compile(
    r'^(?:chapter|part|book|section|appendix|prologue|epilogue|preface|introduction)\s+\S',
    re.IGNORECASE,
)
_ALLCAPS_RE = re.compile(r'^[A-Z][A-Z\d\s\-\'\.,:]{3,}$')
_DIVIDER_RE = re.compile(r'^\*{3,}.*\*{3,}$|^-{5,}$|^={5,}$')


def _is_prose_heading(para: str) -> bool:
    first_line = para.splitlines()[0].strip()
    if len(first_line) < 3 or len(first_line) > 100:
        return False
    return bool(
        _CHAPTER_RE.match(first_line)
        or _ALLCAPS_RE.match(first_line)
        or _DIVIDER_RE.match(first_line)
    )


class TextParser(ParserPlugin):
    name = "text_parser"
    supported_types = ["txt", "text"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        raw = path.read_bytes()
        enc = chardet.detect(raw).get("encoding") or "utf-8"
        text = raw.decode(enc, errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        paragraphs = re.split(r"\n{2,}", text.strip())
        total_chars = len(text)
        large_file = total_chars > _MAX_CONTENT_CHARS

        blocks: list[Block] = []
        idx = 0

        if large_file:
            word_count = len(text.split())
            blocks.append(Block(
                type=BlockType.METADATA,
                content=(
                    f"File: {path.name} | "
                    f"Size: {len(raw):,} bytes | "
                    f"Words: ~{word_count:,} | "
                    f"Paragraphs: {len(paragraphs)}"
                ),
                index=idx,
            ))
            idx += 1

            # Detect headings for a TOC (prose books only — source code won't match)
            headings = [
                para.splitlines()[0].strip()
                for para in paragraphs
                if _is_prose_heading(para)
            ]
            if headings:
                toc = "\n".join(f"- {h}" for h in headings[:60])
                blocks.append(Block(
                    type=BlockType.PARAGRAPH,
                    content=f"**Structure ({len(headings)} sections)**\n{toc}",
                    index=idx,
                ))
                idx += 1

        # Emit paragraphs up to budget (or all for small files)
        cumulative = 0
        truncated_at: int | None = None

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            if large_file and cumulative + len(para) > _MAX_CONTENT_CHARS:
                truncated_at = i
                break
            blocks.append(Block(type=BlockType.PARAGRAPH, content=para, index=idx))
            idx += 1
            cumulative += len(para)

        if truncated_at is not None:
            remaining = len(paragraphs) - truncated_at
            pct = 100 * cumulative // total_chars if total_chars else 0
            blocks.append(Block(
                type=BlockType.METADATA,
                content=(
                    f"[Truncated: {cumulative:,} of {total_chars:,} chars shown ({pct}%)."
                    f" {remaining} paragraphs omitted."
                    f" Full document: ~{len(text.split()):,} words.]"
                ),
                index=idx,
            ))

        doc = Document(
            source=str(path),
            file_type="txt",
            title=path.stem,
            pages=1,
            blocks=blocks,
            metadata={"truncated": truncated_at is not None} if large_file else {},
        )
        doc.compute_id()
        ctx.document = doc
        return ctx


_TEXT_LIKE = [
    "txt", "text", "log", "conf", "cfg", "ini", "env",
    # source code
    "py", "pyw",
    "js", "mjs", "cjs",
    "ts", "tsx", "jsx",
    "java", "kt", "kts",
    "c", "h", "cpp", "cc", "cxx", "hpp",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "swift",
    "scala",
    "r",
    "m",
    "jl",
    "lua",
    "pl", "pm",
    "sh", "bash", "zsh", "fish",
    "ps1",
    "bat", "cmd",
    "sql",
    "tf", "hcl",
    "proto",
    "graphql", "gql",
    # markup / config
    "yaml", "yml",
    "toml",
    "rst",
    "tex",
    "latex",
    "srt", "vtt",
    "diff", "patch",
    "gitignore", "dockerignore",
    "makefile",
    "dockerfile",
]

for _ext in _TEXT_LIKE:
    register_parser(_ext, TextParser)
