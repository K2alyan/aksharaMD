"""Tests for legacy_office.py (mocked — no LibreOffice or olefile required)."""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.legacy_office import (
    LegacyOfficeParser,
    _convert_with_libreoffice,
    _extract_doc_text_olefile,
    _extract_ppt_text_olefile,
    _find_soffice,
)

# ── Helper ─────────────────────────────────────────────────────────────────────

def _make_ctx(tmp_path: Path, suffix: str = ".doc") -> CompilationContext:
    f = tmp_path / f"test{suffix}"
    f.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 12)  # OLE magic header
    return CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))


# ── _find_soffice ──────────────────────────────────────────────────────────────

@patch("shutil.which", return_value="/usr/bin/soffice")
def test_find_soffice_on_path(mock_which):
    result = _find_soffice()
    assert result is not None


@patch("shutil.which", return_value=None)
@patch("pathlib.Path.exists", return_value=False)
def test_find_soffice_not_found(mock_exists, mock_which):
    result = _find_soffice()
    assert result is None


# ── _convert_with_libreoffice ──────────────────────────────────────────────────

@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value=None)
def test_convert_returns_none_when_no_soffice(mock_find, tmp_path):
    result = _convert_with_libreoffice(tmp_path / "test.doc", "html", tmp_path)
    assert result is None


@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value="/usr/bin/soffice")
@patch("subprocess.run")
def test_convert_constructs_correct_command(mock_run, mock_find, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    doc = tmp_path / "test.doc"
    doc.write_bytes(b"\x00")
    # Create the expected output file so the function returns it
    (tmp_path / "test.html").write_text("<html><body>hi</body></html>")
    _convert_with_libreoffice(doc, "html", tmp_path)
    args = mock_run.call_args[0][0]
    assert args[0] == "/usr/bin/soffice"
    assert "--headless" in args
    assert "--convert-to" in args
    assert "html" in args
    assert str(doc) in args


@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value="/usr/bin/soffice")
@patch("subprocess.run", side_effect=subprocess.TimeoutExpired("soffice", 60))
def test_convert_returns_none_on_error(mock_run, mock_find, tmp_path):
    result = _convert_with_libreoffice(tmp_path / "test.doc", "html", tmp_path)
    assert result is None


# ── _extract_doc_text_olefile ──────────────────────────────────────────────────

def test_extract_doc_olefile_no_olefile(tmp_path):
    """Gracefully returns empty list when olefile is not installed."""
    with patch.dict("sys.modules", {"olefile": None}):
        import importlib

        import aksharamd.plugins.parsers.legacy_office as mod
        importlib.reload(mod)
        result = mod._extract_doc_text_olefile(tmp_path / "test.doc")
    assert result == []


def test_extract_doc_olefile_missing_word_stream(tmp_path):
    mock_ole = MagicMock()
    mock_ole.__enter__ = lambda s: mock_ole
    mock_ole.__exit__ = MagicMock(return_value=False)
    mock_ole.exists.return_value = False  # no "WordDocument" stream

    mock_olefile = MagicMock()
    mock_olefile.OleFileIO.return_value = mock_ole

    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        result = _extract_doc_text_olefile(tmp_path / "test.doc")
    assert result == []


def test_extract_doc_olefile_returns_paragraphs(tmp_path):
    content = b"This is a test paragraph with enough alphabetic content to pass."
    mock_stream = MagicMock()
    mock_stream.read.return_value = content

    mock_ole = MagicMock()
    mock_ole.__enter__ = lambda s: mock_ole
    mock_ole.__exit__ = MagicMock(return_value=False)
    mock_ole.exists.return_value = True
    mock_ole.openstream.return_value = mock_stream

    mock_olefile = MagicMock()
    mock_olefile.OleFileIO.return_value = mock_ole

    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        result = _extract_doc_text_olefile(tmp_path / "test.doc")
    assert len(result) >= 1
    assert all(b.type == BlockType.PARAGRAPH for b in result)


# ── _extract_ppt_text_olefile ──────────────────────────────────────────────────

def _make_ppt_record(rec_type: int, payload: bytes) -> bytes:
    """Build a minimal PPT binary record."""
    header = struct.pack("<HHI", 0x0F00, rec_type, len(payload))
    return header + payload


def test_extract_ppt_olefile_returns_text_atoms(tmp_path):
    _TEXT_CHARS = 0x0FA0
    text = "Hello from slide one"
    payload = text.encode("utf-16-le")
    data = _make_ppt_record(_TEXT_CHARS, payload)

    mock_stream = MagicMock()
    mock_stream.read.return_value = data

    mock_ole = MagicMock()
    mock_ole.__enter__ = lambda s: mock_ole
    mock_ole.__exit__ = MagicMock(return_value=False)
    mock_ole.exists.return_value = True
    mock_ole.openstream.return_value = mock_stream

    mock_olefile = MagicMock()
    mock_olefile.OleFileIO.return_value = mock_ole

    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        result = _extract_ppt_text_olefile(tmp_path / "test.ppt")
    assert len(result) >= 1
    assert result[0].content == text


def test_extract_ppt_olefile_missing_stream(tmp_path):
    mock_ole = MagicMock()
    mock_ole.__enter__ = lambda s: mock_ole
    mock_ole.__exit__ = MagicMock(return_value=False)
    mock_ole.exists.return_value = False

    mock_olefile = MagicMock()
    mock_olefile.OleFileIO.return_value = mock_ole

    with patch.dict("sys.modules", {"olefile": mock_olefile}):
        result = _extract_ppt_text_olefile(tmp_path / "test.ppt")
    assert result == []


# ── LegacyOfficeParser full flow ───────────────────────────────────────────────

@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value=None)
@patch("aksharamd.plugins.parsers.legacy_office._extract_doc_text_olefile")
def test_parser_falls_back_to_olefile_for_doc(mock_extract, mock_find, tmp_path):
    from aksharamd.models.block import Block, ExtractionConfidence
    mock_extract.return_value = [
        Block(type=BlockType.PARAGRAPH, content="Test content from OLE", index=0,
              confidence=ExtractionConfidence.AMBIGUOUS)
    ]
    ctx = _make_ctx(tmp_path, ".doc")
    result = LegacyOfficeParser().execute(ctx)
    assert result.document is not None
    assert result.document.metadata.get("extraction") == "olefile_fallback"
    mock_extract.assert_called_once()


@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value=None)
@patch("aksharamd.plugins.parsers.legacy_office._extract_doc_text_olefile", return_value=[])
def test_parser_errors_when_olefile_returns_nothing(mock_extract, mock_find, tmp_path):
    ctx = _make_ctx(tmp_path, ".doc")
    result = LegacyOfficeParser().execute(ctx)
    codes = [i.code for i in result.validation.issues]
    assert "LEGACY_OFFICE_EXTRACT_FAILED" in codes


@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value="/usr/bin/soffice")
@patch("aksharamd.plugins.parsers.legacy_office._convert_with_libreoffice")
@patch("aksharamd.plugins.parsers.legacy_office._html_to_blocks")
def test_parser_uses_libreoffice_when_available(mock_html, mock_convert, mock_find, tmp_path):
    from aksharamd.models.block import Block
    converted_html = tmp_path / "test.html"
    converted_html.write_text("<html><body><p>LibreOffice output</p></body></html>")
    mock_convert.return_value = converted_html
    mock_html.return_value = [Block(type=BlockType.PARAGRAPH, content="LibreOffice output", index=0)]

    ctx = _make_ctx(tmp_path, ".doc")
    result = LegacyOfficeParser().execute(ctx)
    assert result.document is not None
    assert result.document.blocks[0].content == "LibreOffice output"
    # olefile path should NOT have been used
    mock_html.assert_called_once()


@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value="/usr/bin/soffice")
@patch("aksharamd.plugins.parsers.legacy_office._convert_with_libreoffice", return_value=None)
def test_parser_errors_when_libreoffice_conversion_fails(mock_convert, mock_find, tmp_path):
    ctx = _make_ctx(tmp_path, ".doc")
    result = LegacyOfficeParser().execute(ctx)
    codes = [i.code for i in result.validation.issues]
    assert "LIBREOFFICE_CONVERT_FAILED" in codes


@patch("aksharamd.plugins.parsers.legacy_office._find_soffice", return_value=None)
@patch("aksharamd.plugins.parsers.legacy_office._extract_ppt_text_olefile")
def test_parser_uses_ppt_extractor_for_ppt_files(mock_extract, mock_find, tmp_path):
    from aksharamd.models.block import Block, ExtractionConfidence
    mock_extract.return_value = [
        Block(type=BlockType.PARAGRAPH, content="Slide content", index=0,
              confidence=ExtractionConfidence.AMBIGUOUS)
    ]
    ctx = _make_ctx(tmp_path, ".ppt")
    result = LegacyOfficeParser().execute(ctx)
    assert result.document is not None
    mock_extract.assert_called_once()
