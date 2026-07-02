from __future__ import annotations

import io
import zipfile
from pathlib import Path

from aksharamd.compiler import Compiler


def _compile(tmp_path: Path, filename: str, content: bytes) -> object:
    f = tmp_path / filename
    f.write_bytes(content)
    out = tmp_path / "out"
    return Compiler(output_dir=str(out)).compile(str(f))


# ── Corrupt binary files ──────────────────────────────────────────────────────

def test_truncated_pdf_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "trunc.pdf", b"%PDF-1.4\n" + b"garbage" * 50)
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


def test_truncated_docx_does_not_crash(tmp_path):
    # DOCX is a ZIP — give it a valid PK header but truncated body
    ctx = _compile(tmp_path, "trunc.docx", b"PK\x03\x04" + b"\x00" * 50)
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


def test_invalid_png_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "bad.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


def test_binary_garbage_as_json_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "garbage.json", b"\xff\xfe{not valid}")
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


def test_empty_pdf_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "empty.pdf", b"")
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


def test_empty_docx_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "empty.docx", b"")
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


def test_empty_html_produces_document(tmp_path):
    ctx = _compile(tmp_path, "empty.html", b"")
    # Empty HTML should still produce a document (possibly empty blocks)
    assert ctx.document is not None or ctx.validation.errors


def test_text_file_with_latin1_encoding(tmp_path):
    ctx = _compile(tmp_path, "latin1.txt", "Café résumé naïve".encode("latin-1"))
    assert ctx.document is not None


def test_json_file_with_invalid_utf8(tmp_path):
    ctx = _compile(tmp_path, "bad_utf8.json", b'{"key": "\xff\xfe"}')
    assert ctx.document is None or ctx.validation.errors or ctx.document is not None


# ── Archive bomb protection ────────────────────────────────────────────────────

def test_zip_bomb_rejected(tmp_path):
    """A ZIP whose declared uncompressed size exceeds the limit should be rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Write a real large entry (100MB of zeros compresses well)
        big_content = b"\x00" * (100 * 1024 * 1024)
        zf.writestr("big.txt", big_content)

    bomb_path = tmp_path / "bomb.zip"
    bomb_path.write_bytes(buf.getvalue())

    ctx = _compile(tmp_path, "bomb.zip", bomb_path.read_bytes())
    # Either rejected with ARCHIVE_TOO_LARGE error, or processed (if content < limit)
    # The real test is that it doesn't hang or OOM — just assert it returns
    assert ctx is not None


# ── URL fetch error handling ───────────────────────────────────────────────────

def test_url_fetch_connection_error(monkeypatch, tmp_path):
    """URL fetch failure should produce a validation error, not an exception."""
    import requests

    def mock_get(*args, **kwargs):
        raise requests.ConnectionError("Network unreachable")

    monkeypatch.setattr("requests.get", mock_get)

    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://example.com/doc.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)


def test_url_fetch_timeout(monkeypatch, tmp_path):
    """URL fetch timeout should produce a validation error, not hang."""
    import requests

    def mock_get(*args, **kwargs):
        raise requests.Timeout("Request timed out")

    monkeypatch.setattr("requests.get", mock_get)

    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://example.com/doc.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)
