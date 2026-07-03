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
    assert ctx is not None


def test_truncated_docx_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "trunc.docx", b"PK\x03\x04" + b"\x00" * 50)
    assert ctx is not None


def test_invalid_png_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "bad.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
    assert ctx is not None


def test_binary_garbage_as_json_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "garbage.json", b"\xff\xfe{not valid}")
    assert ctx is not None


def test_empty_pdf_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "empty.pdf", b"")
    assert ctx is not None


def test_empty_docx_does_not_crash(tmp_path):
    ctx = _compile(tmp_path, "empty.docx", b"")
    assert ctx is not None


def test_empty_html_produces_document(tmp_path):
    ctx = _compile(tmp_path, "empty.html", b"")
    assert ctx.document is not None or ctx.validation.errors


def test_text_file_with_latin1_encoding(tmp_path):
    ctx = _compile(tmp_path, "latin1.txt", "Café résumé naïve".encode("latin-1"))
    assert ctx.document is not None


def test_json_file_with_invalid_utf8(tmp_path):
    ctx = _compile(tmp_path, "bad_utf8.json", b'{"key": "\xff\xfe"}')
    assert ctx is not None


# ── Archive bomb protection ────────────────────────────────────────────────────

def test_zip_bomb_rejected(tmp_path, monkeypatch):
    """A ZIP whose declared uncompressed size exceeds the limit must be rejected."""
    import aksharamd.plugins.parsers.archive as archive_mod
    monkeypatch.setattr(archive_mod, "_MAX_ARCHIVE_DECOMPRESSED_BYTES", 1024)  # 1 KB limit

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", "x" * 4096)  # 4 KB uncompressed → exceeds the patched 1 KB limit

    ctx = _compile(tmp_path, "bomb.zip", buf.getvalue())
    error_codes = [e.code for e in ctx.validation.errors]
    assert "ARCHIVE_TOO_LARGE" in error_codes


# ── URL fetch error handling ───────────────────────────────────────────────────

def _patch_url_fetch(monkeypatch, exc):
    """Patch socket resolution (returns a public IP) and requests.get to raise exc."""
    import socket
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "93.184.216.34")  # example.com public IP
    monkeypatch.setattr("requests.get", lambda *a, **kw: (_ for _ in ()).throw(exc))


def test_url_fetch_connection_error(monkeypatch, tmp_path):
    """URL fetch failure should produce a validation error, not an exception."""
    import requests
    _patch_url_fetch(monkeypatch, requests.ConnectionError("Network unreachable"))
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://example.com/doc.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)


def test_url_fetch_timeout(monkeypatch, tmp_path):
    """URL fetch timeout should produce a validation error, not hang."""
    import requests
    _patch_url_fetch(monkeypatch, requests.Timeout("Request timed out"))
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://example.com/doc.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)


def test_ssrf_private_ip_rejected(monkeypatch, tmp_path):
    """Requests to private/internal IPs must be rejected before any network call."""
    import socket
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "192.168.1.1")
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://internal.corp/secret.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)
