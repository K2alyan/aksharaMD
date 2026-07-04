"""Tests for the audio parser (mocked — no Whisper/ffmpeg required)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.audio import (
    AudioParser,
    _format_duration,
    _probe_duration,
    _segments_to_paragraphs,
)

# ── Unit helpers ───────────────────────────────────────────────────────────────

def test_format_duration_seconds():
    assert _format_duration(45) == "45s"


def test_format_duration_minutes():
    assert _format_duration(125) == "2m 5s"


def test_format_duration_hours():
    assert _format_duration(3661) == "1h 1m 1s"


def test_segments_to_paragraphs_empty():
    assert _segments_to_paragraphs([]) == []


def test_segments_to_paragraphs_sentence_boundary():
    segments = [
        {"text": "Hello world.", "start": 0.0, "end": 1.5},
        {"text": "New sentence.", "start": 1.6, "end": 3.0},
    ]
    result = _segments_to_paragraphs(segments)
    assert len(result) == 2


def test_segments_to_paragraphs_gap_break():
    segments = [
        {"text": "First chunk", "start": 0.0, "end": 1.0},
        {"text": "Second chunk", "start": 4.0, "end": 5.0},  # 3s gap → new paragraph
    ]
    result = _segments_to_paragraphs(segments)
    assert len(result) == 2


def test_segments_to_paragraphs_groups_short_gaps():
    segments = [
        {"text": "Part one", "start": 0.0, "end": 1.0},
        {"text": "part two", "start": 1.5, "end": 2.5},  # 0.5s gap — stays together
        {"text": "end.", "start": 2.6, "end": 3.0},
    ]
    result = _segments_to_paragraphs(segments)
    # Sentence ends on last segment — all should merge into one paragraph
    assert len(result) == 1


def test_segments_to_paragraphs_skips_empty():
    segments = [{"text": "", "start": 0.0, "end": 1.0}]
    assert _segments_to_paragraphs(segments) == []


@patch("subprocess.run")
def test_probe_duration_success(mock_run, tmp_path):
    mock_run.return_value = MagicMock(stdout="123.45\n", returncode=0)
    result = _probe_duration(tmp_path / "audio.mp3")
    assert result == pytest.approx(123.45)
    # Verify ffprobe is called with correct flags
    args = mock_run.call_args[0][0]
    assert args[0] == "ffprobe"
    assert "-show_entries" in args
    assert "format=duration" in args


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_probe_duration_ffprobe_missing(mock_run, tmp_path):
    result = _probe_duration(tmp_path / "audio.mp3")
    assert result is None


# ── AudioParser integration (whisper mocked) ───────────────────────────────────

def _make_ctx(tmp_path: Path, suffix: str = ".mp3") -> CompilationContext:
    audio = tmp_path / f"test{suffix}"
    audio.write_bytes(b"\x00" * 16)  # dummy bytes
    return CompilationContext(source=str(audio), output_dir=str(tmp_path / "out"))


@patch("aksharamd.plugins.parsers.audio._probe_duration", return_value=60.0)
@patch("aksharamd.plugins.parsers.audio.whisper", create=True)
def test_audio_parser_successful_transcription(mock_whisper, mock_probe, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {
        "text": "Hello world. This is a test.",
        "language": "en",
        "segments": [
            {"text": "Hello world.", "start": 0.0, "end": 2.0},
            {"text": "This is a test.", "start": 2.5, "end": 5.0},
        ],
    }
    mock_whisper.load_model.return_value = mock_model

    ctx = _make_ctx(tmp_path)
    with patch.dict("sys.modules", {"whisper": mock_whisper}):
        result = AudioParser().execute(ctx)

    assert result.document is not None
    types = {b.type for b in result.document.blocks}
    assert BlockType.METADATA in types
    assert BlockType.HEADING in types
    assert BlockType.PARAGRAPH in types


@patch("aksharamd.plugins.parsers.audio._probe_duration", return_value=None)
@patch("aksharamd.plugins.parsers.audio.whisper", create=True)
def test_audio_parser_empty_transcript(mock_whisper, mock_probe, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "", "language": "en", "segments": []}
    mock_whisper.load_model.return_value = mock_model

    ctx = _make_ctx(tmp_path)
    with patch.dict("sys.modules", {"whisper": mock_whisper}):
        result = AudioParser().execute(ctx)

    assert result.document is not None
    codes = [i.code for i in result.validation.issues]
    assert "AUDIO_EMPTY_TRANSCRIPT" in codes


@patch("aksharamd.plugins.parsers.audio._probe_duration", return_value=30.0)
@patch("aksharamd.plugins.parsers.audio.whisper", create=True)
def test_audio_parser_transcription_error(mock_whisper, mock_probe, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("CUDA out of memory")
    mock_whisper.load_model.return_value = mock_model

    ctx = _make_ctx(tmp_path)
    with patch.dict("sys.modules", {"whisper": mock_whisper}):
        result = AudioParser().execute(ctx)

    # Should still return a document with metadata block
    assert result.document is not None
    codes = [i.code for i in result.validation.issues]
    assert "WHISPER_TRANSCRIBE_ERROR" in codes


@patch("aksharamd.plugins.parsers.audio._probe_duration", return_value=30.0)
@patch("aksharamd.plugins.parsers.audio.whisper", create=True)
def test_audio_parser_fallback_to_full_text_when_no_segments(mock_whisper, mock_probe, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {
        "text": "Hello world. No segments here.",
        "language": "en",
        "segments": [],
    }
    mock_whisper.load_model.return_value = mock_model

    ctx = _make_ctx(tmp_path)
    with patch.dict("sys.modules", {"whisper": mock_whisper}):
        result = AudioParser().execute(ctx)

    paragraphs = [b for b in result.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paragraphs) >= 1


def test_audio_parser_whisper_not_installed(tmp_path):
    ctx = _make_ctx(tmp_path)
    # Temporarily hide whisper from imports
    with patch.dict("sys.modules", {"whisper": None}):
        # Re-import to trigger ImportError path
        import importlib

        import aksharamd.plugins.parsers.audio as audio_mod
        importlib.reload(audio_mod)
        result = audio_mod.AudioParser().execute(ctx)
    codes = [i.code for i in result.validation.issues]
    assert any("WHISPER" in c for c in codes)
