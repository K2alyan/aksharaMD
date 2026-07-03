from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

from ...context import CompilationContext
from ...models.block import Block, BlockType, ExtractionConfidence
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


_PRINTABLE_RE = re.compile(r'[ -~\t\n\r\x80-\xFF]{4,}')
_ALPHA_THRESHOLD = 0.25  # at least 25% alphabetic characters to not be noise


def _extract_doc_text_olefile(path: Path) -> list[Block]:
    """Best-effort .doc text extraction via OLE stream parsing (no LibreOffice)."""
    try:
        import olefile
    except ImportError:
        return []
    try:
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("WordDocument"):
                return []
            raw = ole.openstream("WordDocument").read()
        text = raw.decode("latin-1", errors="replace")
        runs = _PRINTABLE_RE.findall(text)
        blocks: list[Block] = []
        idx = 0
        for run in runs:
            run = run.strip()
            if len(run) < 4:
                continue
            alpha = sum(1 for c in run if c.isalpha())
            if alpha / max(len(run), 1) < _ALPHA_THRESHOLD:
                continue
            for para in run.splitlines():
                para = para.strip()
                if len(para) >= 4:
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=para, index=idx,
                                        confidence=ExtractionConfidence.AMBIGUOUS))
                    idx += 1
        return blocks
    except Exception:
        logger.debug("olefile .doc extraction failed for %s", path.name, exc_info=True)
        return []


def _extract_ppt_text_olefile(path: Path) -> list[Block]:
    """Best-effort .ppt text extraction via OLE record walking (no LibreOffice)."""
    try:
        import olefile
    except ImportError:
        return []
    try:
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("PowerPoint Document"):
                return []
            data = ole.openstream("PowerPoint Document").read()

        # Walk binary record stream: each record = 2B ver/inst + 2B type + 4B len + data
        _TEXT_CHARS = 0x0FA0   # TextCharsAtom — UTF-16LE
        _TEXT_BYTES = 0x0FA8   # TextBytesAtom — Latin-1
        texts: list[str] = []
        i = 0
        while i + 8 <= len(data):
            rec_type = int.from_bytes(data[i + 2: i + 4], "little")
            rec_len = int.from_bytes(data[i + 4: i + 8], "little")
            payload = data[i + 8: i + 8 + rec_len]
            if rec_type == _TEXT_CHARS:
                try:
                    texts.append(payload.decode("utf-16-le", errors="replace").strip())
                except Exception:
                    pass
            elif rec_type == _TEXT_BYTES:
                try:
                    texts.append(payload.decode("latin-1", errors="replace").strip())
                except Exception:
                    pass
            i += 8 + max(rec_len, 0)

        blocks: list[Block] = []
        for idx, txt in enumerate(t for t in texts if t):
            blocks.append(Block(type=BlockType.PARAGRAPH, content=txt, index=idx,
                                confidence=ExtractionConfidence.AMBIGUOUS))
        return blocks
    except Exception:
        logger.debug("olefile .ppt extraction failed for %s", path.name, exc_info=True)
        return []


def _html_to_blocks(html_path: Path) -> list[Block]:
    from bs4 import BeautifulSoup, Tag

    from ...models.asset import Asset
    from .html import _walk as html_walk

    html = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    blocks: list[Block] = []
    assets: list[Asset] = []
    idx = [0]
    _body = soup.find("body") or soup
    body: Tag = _body if isinstance(_body, Tag) else soup
    html_walk(body, blocks, assets, idx)
    return blocks


class LegacyOfficeParser(ParserPlugin):
    name = "legacy_office_parser"
    supported_types = ["doc", "ppt", "pptm", "docm"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        file_type = path.suffix.lower().lstrip(".")

        # Preferred path: LibreOffice → high-fidelity HTML conversion
        if _find_soffice() is not None:
            with tempfile.TemporaryDirectory() as tmp:
                converted = _convert_with_libreoffice(path, "html", Path(tmp))
                if converted is not None:
                    blocks = _html_to_blocks(converted)
                    if blocks:
                        ctx.document = Document(
                            source=str(path), file_type=file_type,
                            title=path.stem, pages=1, blocks=blocks,
                        ).compute_id()
                        return ctx
            ctx.error("LIBREOFFICE_CONVERT_FAILED", f"Conversion of {path.name} failed")
            return ctx

        # Fallback: OLE stream extraction (no LibreOffice required, reduced fidelity)
        logger.warning(
            "LibreOffice not found — using best-effort OLE extraction for %s "
            "(install LibreOffice for full fidelity: https://www.libreoffice.org/)",
            path.name,
        )
        if file_type in ("doc", "docm"):
            blocks = _extract_doc_text_olefile(path)
        else:
            blocks = _extract_ppt_text_olefile(path)

        if not blocks:
            ctx.error(
                "LEGACY_OFFICE_EXTRACT_FAILED",
                f"Could not extract text from {path.name}. "
                "Install LibreOffice for reliable .doc/.ppt parsing.",
            )
            return ctx

        ctx.document = Document(
            source=str(path), file_type=file_type,
            title=path.stem, pages=1, blocks=blocks,
            metadata={"extraction": "olefile_fallback"},
        ).compute_id()
        return ctx


for _ext in LegacyOfficeParser.supported_types:
    register_parser(_ext, LegacyOfficeParser)
