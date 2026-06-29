from __future__ import annotations
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_EXIF_TAGS_KEEP = {
    "Make", "Model", "Software", "DateTime", "DateTimeOriginal",
    "DateTimeDigitized", "GPSInfo", "ImageDescription", "Artist",
    "Copyright", "Orientation", "XResolution", "YResolution",
    "ExposureTime", "FNumber", "ISOSpeedRatings", "FocalLength",
    "Flash", "LightSource", "MeteringMode", "ExposureProgram",
}

_HUMAN_ORIENTATION = {
    1: "Normal", 2: "Mirrored horizontal", 3: "Rotated 180",
    4: "Mirrored vertical", 5: "Mirrored horizontal, rotated 90 CW",
    6: "Rotated 90 CW", 7: "Mirrored horizontal, rotated 90 CCW",
    8: "Rotated 90 CCW",
}

# Common Tesseract install paths across platforms
_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]

_MIN_OCR_CHARS = 10        # discard results shorter than this
_MIN_WORD_RATIO = 0.3      # discard if fewer than 30% of tokens look like real words
_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _find_tesseract() -> str | None:
    for path in _TESSERACT_CANDIDATES:
        if Path(path).exists():
            return path
    return shutil.which("tesseract")


def _configure_tesseract() -> bool:
    """Set tesseract_cmd if binary found. Returns True if available."""
    try:
        import pytesseract
        binary = _find_tesseract()
        if binary:
            pytesseract.pytesseract.tesseract_cmd = binary
            return True
        logger.debug("Tesseract binary not found; OCR disabled")
        return False
    except ImportError:
        logger.debug("pytesseract not installed; OCR disabled")
        return False


def _exif_value(val) -> str:
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", errors="replace").strip("\x00").strip()
        except Exception:
            return repr(val)
    if isinstance(val, tuple) and len(val) == 2 and isinstance(val[0], int):
        return f"{val[0]}/{val[1]}"
    return str(val)


def _extract_exif(img) -> dict[str, str]:
    from PIL.ExifTags import TAGS
    exif_data: dict[str, str] = {}
    try:
        raw = img._getexif()
        if not raw:
            return exif_data
        for tag_id, val in raw.items():
            name = TAGS.get(tag_id, str(tag_id))
            if name not in _EXIF_TAGS_KEEP:
                continue
            if name == "Orientation" and isinstance(val, int):
                val = _HUMAN_ORIENTATION.get(val, str(val))
            elif name == "GPSInfo":
                continue
            exif_data[name] = _exif_value(val)
    except Exception:
        logger.debug("EXIF extraction failed", exc_info=True)
    return exif_data


def _preprocess_for_ocr(img):
    """Convert to grayscale RGB; scale up tiny images for better recognition."""
    from PIL import Image
    # Convert to RGB for consistent Tesseract input
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Scale up very small images (Tesseract struggles below ~300px)
    w, h = img.size
    if max(w, h) < 500:
        scale = max(2, 1000 // max(w, h))
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
    return img


def _is_quality_ocr(text: str) -> bool:
    """Return False if OCR result looks like noise (too short or too few real words)."""
    if len(text) < _MIN_OCR_CHARS:
        return False
    tokens = text.split()
    if not tokens:
        return False
    word_count = sum(1 for t in tokens if _WORD_RE.search(t))
    return word_count / len(tokens) >= _MIN_WORD_RATIO


def _ocr_to_blocks(text: str, start_idx: int) -> list[Block]:
    """Split OCR text into paragraph blocks."""
    blocks = []
    idx = start_idx
    for chunk in re.split(r"\n{2,}", text):
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]
        para = " ".join(lines)
        if para and len(para) >= _MIN_OCR_CHARS:
            blocks.append(Block(type=BlockType.PARAGRAPH, content=para, index=idx))
            idx += 1
    return blocks


def _try_ocr(img) -> str | None:
    """Run Tesseract OCR on the image. Returns cleaned text or None."""
    if not _configure_tesseract():
        return None
    try:
        import pytesseract
        preprocessed = _preprocess_for_ocr(img)
        text = pytesseract.image_to_string(preprocessed, config="--psm 3")
        text = text.strip()
        if not _is_quality_ocr(text):
            return None
        return text
    except Exception:
        logger.debug("Tesseract OCR failed", exc_info=True)
        return None


class ImageParser(ParserPlugin):
    name = "image_parser"
    supported_types = ["jpg", "jpeg", "png", "gif", "tiff", "tif", "bmp", "webp"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        from PIL import Image

        path = Path(ctx.source)
        try:
            img = Image.open(str(path))
            img.load()
        except Exception as e:
            ctx.error("IMAGE_PARSE_ERROR", str(e))
            return ctx

        blocks: list[Block] = []
        idx = 0

        # ── Core image properties ──────────────────────────────────────────────
        width, height = img.size
        mode = img.mode
        fmt = img.format or path.suffix.upper().lstrip(".")
        file_size = path.stat().st_size

        meta_parts = [
            f"Format: {fmt}",
            f"Dimensions: {width}x{height}px",
            f"Mode: {mode}",
            f"File size: {file_size:,} bytes",
        ]

        # ── EXIF metadata ──────────────────────────────────────────────────────
        exif = _extract_exif(img)
        if exif:
            for k, v in exif.items():
                meta_parts.append(f"{k}: {v}")

        blocks.append(Block(
            type=BlockType.METADATA,
            content=" | ".join(meta_parts),
            index=idx,
        ))
        idx += 1

        # ── OCR ───────────────────────────────────────────────────────────────
        ocr_text = _try_ocr(img)
        has_ocr = False
        if ocr_text:
            ocr_blocks = _ocr_to_blocks(ocr_text, idx)
            if ocr_blocks:
                has_ocr = True
                blocks.extend(ocr_blocks)
                idx += len(ocr_blocks)

        # ── Animation info for GIF ─────────────────────────────────────────────
        n_frames = getattr(img, "n_frames", 1)
        if n_frames > 1:
            blocks.append(Block(
                type=BlockType.METADATA,
                content=f"Animated: {n_frames} frames",
                index=idx,
            ))
            idx += 1

        title = exif.get("ImageDescription") or path.stem
        author = exif.get("Artist") or exif.get("Make") or None
        camera = " ".join(filter(None, [exif.get("Make"), exif.get("Model")])) or None

        ctx.document = Document(
            source=str(path),
            file_type=path.suffix.lower().lstrip("."),
            title=title,
            author=author,
            pages=1,
            blocks=blocks,
            metadata={
                "width": width,
                "height": height,
                "mode": mode,
                "format": fmt,
                "has_exif": bool(exif),
                "has_ocr": has_ocr,
                "camera": camera,
                "tesseract_available": _find_tesseract() is not None,
            },
        ).compute_id()
        return ctx


for _ext in ImageParser.supported_types:
    register_parser(_ext, ImageParser)
