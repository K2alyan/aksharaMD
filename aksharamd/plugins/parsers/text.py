from __future__ import annotations
import re
from pathlib import Path

import chardet

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document


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
        blocks: list[Block] = []

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            blocks.append(Block(
                type=BlockType.PARAGRAPH,
                content=para,
                index=i,
            ))

        doc = Document(
            source=str(path),
            file_type="txt",
            title=path.stem,
            pages=1,
            blocks=blocks,
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
