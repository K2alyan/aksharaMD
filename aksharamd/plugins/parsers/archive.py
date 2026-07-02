from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala",
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".htm", ".css", ".scss", ".json", ".xml", ".sh", ".bash",
    ".sql", ".r", ".m", ".jl",
}
_MAX_FILE_BYTES   = 32_768   # 32 KB per file
_MAX_FILES_SHOWN  = 100      # max files to extract text from
_MAX_LIST_ENTRIES = 500      # max entries in file listing
_MAX_ARCHIVE_DECOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def _is_text(name: str) -> bool:
    return Path(name).suffix.lower() in _TEXT_EXTENSIONS


def _lang(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return ext or "text"


class ZipParser(ParserPlugin):
    name = "zip_parser"
    supported_types = ["zip"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        try:
            zf = zipfile.ZipFile(str(path), "r")
        except Exception as e:
            ctx.error("ZIP_PARSE_ERROR", str(e))
            return ctx

        total_uncompressed = sum(i.file_size for i in zf.infolist())
        if total_uncompressed > _MAX_ARCHIVE_DECOMPRESSED_BYTES:
            ctx.error(
                "ARCHIVE_TOO_LARGE",
                f"Archive decompressed size {total_uncompressed:,} bytes exceeds "
                f"{_MAX_ARCHIVE_DECOMPRESSED_BYTES:,} byte limit",
            )
            return ctx

        members = zf.infolist()
        blocks: list[Block] = []
        idx = 0

        # ── File manifest ─────────────────────────────────────────────────────
        blocks.append(Block(
            type=BlockType.METADATA,
            content=f"Archive: {path.name} | Entries: {len(members)} | Size: {path.stat().st_size:,} bytes",
            index=idx,
        ))
        idx += 1

        listing_rows = [["Name", "Size", "Type"]]
        for info in members[:_MAX_LIST_ENTRIES]:
            ext = Path(info.filename).suffix or "(none)"
            listing_rows.append([info.filename, f"{info.file_size:,}", ext])
        md_table = "\n".join(
            "| " + " | ".join(row) + " |" +
            (" \n| --- | --- | --- |" if i == 0 else "")
            for i, row in enumerate(listing_rows)
        )
        blocks.append(Block(type=BlockType.TABLE, content=md_table, index=idx))
        idx += 1

        if len(members) > _MAX_LIST_ENTRIES:
            blocks.append(Block(
                type=BlockType.PARAGRAPH,
                content=f"*({len(members) - _MAX_LIST_ENTRIES} additional entries not listed)*",
                index=idx,
            ))
            idx += 1

        # ── Extract text content from readable files ───────────────────────────
        text_files = [m for m in members if _is_text(m.filename) and not m.filename.endswith("/")]
        blocks.append(Block(
            type=BlockType.HEADING,
            content=f"Text File Contents ({min(len(text_files), _MAX_FILES_SHOWN)} of {len(text_files)} readable files)",
            level=2,
            index=idx,
        ))
        idx += 1

        for info in text_files[:_MAX_FILES_SHOWN]:
            try:
                raw = zf.read(info.filename)[:_MAX_FILE_BYTES]
                text = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                logger.debug("Could not read %s from ZIP", info.filename, exc_info=True)
                continue
            if not text:
                continue

            blocks.append(Block(
                type=BlockType.HEADING,
                content=info.filename,
                level=3,
                index=idx,
            ))
            idx += 1
            blocks.append(Block(
                type=BlockType.CODE_BLOCK,
                content=text,
                language=_lang(info.filename),
                index=idx,
            ))
            idx += 1

        zf.close()

        ctx.document = Document(
            source=str(path),
            file_type="zip",
            title=path.stem,
            pages=1,
            blocks=blocks,
            metadata={
                "total_entries": len(members),
                "text_files": len(text_files),
            },
        ).compute_id()
        return ctx


register_parser("zip", ZipParser)
