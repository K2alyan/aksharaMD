from __future__ import annotations
import io
import logging
import tarfile
from pathlib import Path

logger = logging.getLogger(__name__)

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala",
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".htm", ".css", ".scss", ".json", ".xml", ".sh", ".bash",
    ".sql", ".r", ".m", ".jl", ".lua", ".tf", ".hcl", ".proto",
}
_MAX_FILE_BYTES  = 32_768
_MAX_FILES_SHOWN = 100
_MAX_LIST_ENTRIES = 500


def _is_text(name: str) -> bool:
    return Path(name).suffix.lower() in _TEXT_EXTENSIONS


def _lang(name: str) -> str:
    return Path(name).suffix.lower().lstrip(".") or "text"


def _read_tar(path: Path, mode: str) -> tuple[list[Block], dict]:
    try:
        tf = tarfile.open(str(path), mode)
    except Exception as e:
        raise RuntimeError(f"Cannot open archive: {e}") from e

    members = tf.getmembers()
    blocks: list[Block] = []
    idx = 0

    # Manifest
    blocks.append(Block(
        type=BlockType.METADATA,
        content=f"Archive: {path.name} | Entries: {len(members)} | Size: {path.stat().st_size:,} bytes",
        index=idx,
    ))
    idx += 1

    listing_rows = [["Name", "Size", "Type"]]
    for m in members[:_MAX_LIST_ENTRIES]:
        ext = Path(m.name).suffix or "(none)"
        listing_rows.append([m.name, f"{m.size:,}", ext])
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

    # Text content
    text_files = [m for m in members if m.isfile() and _is_text(m.name)]
    blocks.append(Block(
        type=BlockType.HEADING,
        content=f"Text File Contents ({min(len(text_files), _MAX_FILES_SHOWN)} of {len(text_files)} readable files)",
        level=2,
        index=idx,
    ))
    idx += 1

    for member in text_files[:_MAX_FILES_SHOWN]:
        try:
            f = tf.extractfile(member)
            if f is None:
                continue
            raw = f.read(_MAX_FILE_BYTES)
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            logger.debug("Could not read %s from TAR", member.name, exc_info=True)
            continue
        if not text:
            continue
        blocks.append(Block(type=BlockType.HEADING, content=member.name, level=3, index=idx))
        idx += 1
        blocks.append(Block(type=BlockType.CODE_BLOCK, content=text, language=_lang(member.name), index=idx))
        idx += 1

    tf.close()
    return blocks, {"total_entries": len(members), "text_files": len(text_files)}


def _read_7z(path: Path) -> tuple[list[Block], dict]:
    import py7zr
    with py7zr.SevenZipFile(str(path), mode="r") as sz:
        all_files = sz.list()
        blocks: list[Block] = []
        idx = 0

        blocks.append(Block(
            type=BlockType.METADATA,
            content=f"Archive: {path.name} | Entries: {len(all_files)} | Size: {path.stat().st_size:,} bytes",
            index=idx,
        ))
        idx += 1

        listing_rows = [["Name", "Size", "Type"]]
        for fi in all_files[:_MAX_LIST_ENTRIES]:
            ext = Path(fi.filename).suffix or "(none)"
            listing_rows.append([fi.filename, f"{fi.uncompressed:,}", ext])
        md_table = "\n".join(
            "| " + " | ".join(row) + " |" +
            (" \n| --- | --- | --- |" if i == 0 else "")
            for i, row in enumerate(listing_rows)
        )
        blocks.append(Block(type=BlockType.TABLE, content=md_table, index=idx))
        idx += 1

        text_names = [fi.filename for fi in all_files if not fi.is_directory and _is_text(fi.filename)]
        blocks.append(Block(
            type=BlockType.HEADING,
            content=f"Text File Contents ({min(len(text_names), _MAX_FILES_SHOWN)} of {len(text_names)} readable files)",
            level=2,
            index=idx,
        ))
        idx += 1

        if text_names:
            extracted = sz.read(text_names[:_MAX_FILES_SHOWN])
            for name, bio in (extracted or {}).items():
                if bio is None:
                    continue
                raw = bio.read(_MAX_FILE_BYTES)
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                blocks.append(Block(type=BlockType.HEADING, content=name, level=3, index=idx))
                idx += 1
                blocks.append(Block(type=BlockType.CODE_BLOCK, content=text, language=_lang(name), index=idx))
                idx += 1

    return blocks, {"total_entries": len(all_files), "text_files": len(text_names)}


class TarParser(ParserPlugin):
    name = "tar_parser"
    supported_types = ["tar", "tgz", "gz", "bz2", "xz"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        name = path.name.lower()

        if name.endswith(".tar.gz") or name.endswith(".tgz"):
            mode = "r:gz"
        elif name.endswith(".tar.bz2"):
            mode = "r:bz2"
        elif name.endswith(".tar.xz"):
            mode = "r:xz"
        elif name.endswith(".tar"):
            mode = "r:"
        else:
            # Single compressed file (.gz / .bz2 / .xz) — might be a tar or just compressed
            try:
                mode = "r:*"
                tarfile.open(str(path), mode).close()
            except Exception:
                ctx.error("TAR_NOT_ARCHIVE", "File is not a tar archive")
                return ctx

        try:
            blocks, meta = _read_tar(path, mode)
        except Exception as e:
            ctx.error("TAR_PARSE_ERROR", str(e))
            return ctx

        ctx.document = Document(
            source=str(path),
            file_type=path.suffix.lower().lstrip("."),
            title=path.stem,
            pages=1,
            blocks=blocks,
            metadata=meta,
        ).compute_id()
        return ctx


class SevenZipParser(ParserPlugin):
    name = "sevenz_parser"
    supported_types = ["7z"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        try:
            blocks, meta = _read_7z(path)
        except Exception as e:
            ctx.error("7Z_PARSE_ERROR", str(e))
            return ctx

        ctx.document = Document(
            source=str(path),
            file_type="7z",
            title=path.stem,
            pages=1,
            blocks=blocks,
            metadata=meta,
        ).compute_id()
        return ctx


register_parser("tar", TarParser)
register_parser("tgz", TarParser)
register_parser("gz",  TarParser)
register_parser("bz2", TarParser)
register_parser("xz",  TarParser)
register_parser("7z",  SevenZipParser)
