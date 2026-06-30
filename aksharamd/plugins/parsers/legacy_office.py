from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ...context import CompilationContext
from ...models.block import Block
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_SOFFICE_CANDIDATES = [
    "soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def _find_soffice() -> str | None:
    for candidate in _SOFFICE_CANDIDATES:
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None


def _convert_with_libreoffice(path: Path, target_format: str, out_dir: Path) -> Path | None:
    soffice = _find_soffice()
    if soffice is None:
        return None

    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", target_format, "--outdir", str(out_dir), str(path)],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None

    stem = path.stem
    ext = "html" if target_format.startswith("html") else target_format
    result = out_dir / f"{stem}.{ext}"
    return result if result.exists() else None


def _html_to_blocks(html_path: Path) -> list[Block]:
    from bs4 import BeautifulSoup

    from ..base import Asset
    from .html import _walk as html_walk

    html = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    blocks: list[Block] = []
    assets: list[Asset] = []
    idx = [0]
    body = soup.find("body") or soup
    html_walk(body, blocks, assets, idx)
    return blocks


class LegacyOfficeParser(ParserPlugin):
    name = "legacy_office_parser"
    supported_types = ["doc", "ppt", "pptm", "docm"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)

        if _find_soffice() is None:
            ctx.error(
                "LIBREOFFICE_NOT_FOUND",
                "LibreOffice is required to parse .doc/.ppt files. "
                "Install from https://www.libreoffice.org/download/libreoffice/ "
                "and ensure 'soffice' is on your PATH.",
            )
            return ctx

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            converted = _convert_with_libreoffice(path, "html", tmp_path)

            if converted is None:
                ctx.error("LIBREOFFICE_CONVERT_FAILED", f"Conversion of {path.name} failed")
                return ctx

            blocks = _html_to_blocks(converted)

        if not blocks:
            ctx.error("LEGACY_OFFICE_EMPTY", "No content extracted after conversion")
            return ctx

        file_type = path.suffix.lower().lstrip(".")
        ctx.document = Document(
            source=str(path),
            file_type=file_type,
            title=path.stem,
            pages=1,
            blocks=blocks,
        ).compute_id()
        return ctx


for _ext in LegacyOfficeParser.supported_types:
    register_parser(_ext, LegacyOfficeParser)
