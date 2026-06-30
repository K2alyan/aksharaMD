from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Default model: "base" balances speed vs quality. Users can override via
# environment variable OMNIMARK_WHISPER_MODEL (tiny/base/small/medium/large).
import os as _os

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large", "large-v2", "large-v3", "turbo"}
_raw_model = _os.environ.get("OMNIMARK_WHISPER_MODEL", "base")
if _raw_model not in _ALLOWED_MODELS:
    logger.warning("Unknown OMNIMARK_WHISPER_MODEL %r — falling back to 'base'", _raw_model)
    _raw_model = "base"
_DEFAULT_MODEL = _raw_model

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _probe_duration(path: Path) -> float | None:
    """Use ffprobe to get duration in seconds without loading audio into Python."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        logger.debug("ffprobe duration probe failed", exc_info=True)
        return None


def _format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _segments_to_paragraphs(segments: list[dict], max_gap_s: float = 2.0) -> list[str]:
    """
    Group whisper segments into paragraphs.
    A new paragraph starts when there's a gap > max_gap_s between segments,
    or when the previous segment ends with sentence-terminal punctuation.
    """
    if not segments:
        return []

    paragraphs: list[str] = []
    current: list[str] = []

    for i, seg in enumerate(segments):
        text = seg.get("text", "").strip()
        if not text:
            continue

        current.append(text)

        end_of_sentence = text[-1] in ".!?"
        last_seg = (i == len(segments) - 1)
        gap = (
            segments[i + 1].get("start", 0) - seg.get("end", 0)
            if not last_seg else 0
        )
        long_pause = gap >= max_gap_s

        if end_of_sentence or long_pause or last_seg:
            para = " ".join(current).strip()
            if para:
                paragraphs.append(para)
            current = []

    return paragraphs


class AudioParser(ParserPlugin):
    name = "audio_parser"
    supported_types = ["mp3", "wav", "m4a", "mp4", "ogg", "flac", "webm", "opus", "aac"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        try:
            import whisper
        except ImportError:
            ctx.error(
                "WHISPER_NOT_INSTALLED",
                "openai-whisper is required for audio transcription. "
                "Install with: pip install openai-whisper",
            )
            return ctx

        path = Path(ctx.source)
        file_type = path.suffix.lower().lstrip(".")
        blocks: list[Block] = []
        idx = 0

        # ── Probe metadata ────────────────────────────────────────────────────
        duration = _probe_duration(path)
        file_size = path.stat().st_size
        meta_parts = [
            f"Format: {file_type.upper()}",
            f"File size: {file_size:,} bytes",
        ]
        if duration is not None:
            meta_parts.append(f"Duration: {_format_duration(duration)}")
        blocks.append(Block(
            type=BlockType.METADATA,
            content=" | ".join(meta_parts),
            index=idx,
        ))
        idx += 1

        # ── Transcribe ────────────────────────────────────────────────────────
        try:
            model = whisper.load_model(_DEFAULT_MODEL)
            result = model.transcribe(
                str(path),
                verbose=False,
                word_timestamps=False,
                fp16=False,   # CPU-safe
            )
        except Exception as e:
            ctx.error("WHISPER_TRANSCRIBE_ERROR", str(e)[:200])
            # Return with just the metadata block — still useful
            ctx.document = Document(
                source=str(path),
                file_type=file_type,
                title=path.stem,
                pages=1,
                blocks=blocks,
                metadata={"duration_seconds": duration},
            ).compute_id()
            return ctx

        detected_lang = result.get("language", "unknown")
        segments = result.get("segments", [])
        full_text = result.get("text", "").strip()

        if detected_lang:
            blocks[0] = blocks[0].model_copy(update={
                "content": blocks[0].content + f" | Language: {detected_lang}"
            })

        if not full_text:
            ctx.warn("AUDIO_EMPTY_TRANSCRIPT", "No speech detected in audio file")
            ctx.document = Document(
                source=str(path),
                file_type=file_type,
                title=path.stem,
                pages=1,
                blocks=blocks,
                metadata={"duration_seconds": duration, "language": detected_lang},
            ).compute_id()
            return ctx

        # ── Transcript heading ────────────────────────────────────────────────
        blocks.append(Block(
            type=BlockType.HEADING,
            content="Transcript",
            level=2,
            index=idx,
        ))
        idx += 1

        # ── Paragraphs from segments ──────────────────────────────────────────
        if segments:
            for para in _segments_to_paragraphs(segments):
                blocks.append(Block(type=BlockType.PARAGRAPH, content=para, index=idx))
                idx += 1
        else:
            # Fallback: split full text on sentence boundaries
            for sent in _SENTENCE_END.split(full_text):
                sent = sent.strip()
                if sent:
                    blocks.append(Block(type=BlockType.PARAGRAPH, content=sent, index=idx))
                    idx += 1

        ctx.document = Document(
            source=str(path),
            file_type=file_type,
            title=path.stem,
            pages=1,
            blocks=blocks,
            metadata={
                "duration_seconds": duration,
                "language": detected_lang,
                "segments": len(segments),
                "model": _DEFAULT_MODEL,
            },
        ).compute_id()
        return ctx


for _ext in AudioParser.supported_types:
    register_parser(_ext, AudioParser)
