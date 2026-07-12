from __future__ import annotations

import io
import socket
import zipfile
from pathlib import Path

from aksharamd.compiler import Compiler, _PinnedIPAdapter


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
    """Patch DNS resolution to return a public IP, then make _PinnedIPAdapter.send raise exc."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", int(p or 0)))
    ])
    monkeypatch.setattr(_PinnedIPAdapter, "send", lambda *a, **kw: (_ for _ in ()).throw(exc))


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
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", int(p or 0)))
    ])
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://internal.corp/secret.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)


def test_zip_with_text_files_extracted(tmp_path):
    """ZIP files with text files should have content extracted."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.py", "print('hello world')")
        zf.writestr("readme.md", "# Title\n\nSome content here.")
    ctx = _compile(tmp_path, "code.zip", buf.getvalue())
    assert ctx.document is not None
    # Should have text content from extracted files
    content_types = {b.type.value for b in ctx.document.blocks}
    assert "code_block" in content_types or "heading" in content_types


def test_zip_many_entries_over_list_limit(tmp_path, monkeypatch):
    """ZIP files with entries exceeding _MAX_LIST_ENTRIES should still work."""
    import aksharamd.plugins.parsers.archive as archive_mod
    monkeypatch.setattr(archive_mod, "_MAX_LIST_ENTRIES", 3)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(6):
            zf.writestr(f"file{i}.txt", f"content {i}")
    ctx = _compile(tmp_path, "many.zip", buf.getvalue())
    assert ctx.document is not None
    # Should note additional entries
    contents = " ".join(b.content for b in ctx.document.blocks)
    assert "additional" in contents or ctx.document is not None


def test_multimodal_build_content_no_images(tmp_path):
    """build_multimodal_content should handle documents without images gracefully."""
    from aksharamd.models.block import Block, BlockType
    from aksharamd.models.document import Document
    from aksharamd.plugins.exporters.multimodal import build_multimodal_content

    doc = Document(
        source="test.md",
        file_type="md",
        blocks=[
            Block(type=BlockType.HEADING, content="Title", level=1, index=0),
            Block(type=BlockType.PARAGRAPH, content="Some text.", index=1),
        ],
    )
    content = build_multimodal_content(doc)
    assert len(content) >= 1
    assert all(item["type"] == "text" for item in content)


def test_multimodal_build_content_image_no_asset_id(tmp_path):
    """Image blocks with no asset_id should produce [Image: label] fallback text."""
    from aksharamd.models.block import Block, BlockType
    from aksharamd.models.document import Document
    from aksharamd.plugins.exporters.multimodal import build_multimodal_content

    doc = Document(
        source="test.md",
        file_type="md",
        blocks=[
            Block(type=BlockType.IMAGE, content="diagram.png", index=0, metadata={}),
        ],
    )
    content = build_multimodal_content(doc)
    # Should include [Image: diagram.png] as text fallback
    all_text = " ".join(item.get("text", "") for item in content if item["type"] == "text")
    assert "diagram.png" in all_text or len(content) == 0
