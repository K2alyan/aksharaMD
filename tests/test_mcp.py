from __future__ import annotations

# Import the tool functions directly (they're plain Python callables)
from aksharamd.mcp_server import (
    _check_allowed_path,
    compile_document,
    compile_document_multimodal,
    get_stats,
    get_supported_formats,
)


def test_compile_document_not_found():
    result = compile_document("/nonexistent/path/file.pdf")
    assert result.startswith("Error:")
    assert "not found" in result


def test_compile_document_directory(tmp_path):
    result = compile_document(str(tmp_path))
    assert result.startswith("Error:")
    assert "not a file" in result


def test_compile_document_empty_file(tmp_path):
    f = tmp_path / "empty.md"
    f.write_bytes(b"")
    result = compile_document(str(f))
    assert result.startswith("Error:")
    assert "empty" in result


def test_compile_document_markdown(tmp_path):
    f = tmp_path / "sample.md"
    f.write_text("# Hello\n\nThis is a test document.\n", encoding="utf-8")
    result = compile_document(str(f))
    assert isinstance(result, str)
    assert "Hello" in result
    assert "AksharaMD compilation summary" in result


def test_compile_document_html(tmp_path):
    f = tmp_path / "sample.html"
    f.write_text("<html><body><h1>Title</h1><p>Content.</p></body></html>", encoding="utf-8")
    result = compile_document(str(f))
    assert "Title" in result
    assert "Content" in result


def test_compile_document_txt(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("Short document.\n\nTwo paragraphs.", encoding="utf-8")
    result = compile_document(str(f))
    assert "Short document" in result


def test_compile_document_rst(tmp_path):
    f = tmp_path / "sample.rst"
    f.write_text("Title\n=====\n\nSome RST content.\n", encoding="utf-8")
    result = compile_document(str(f))
    assert "Title" in result or "RST" in result or "content" in result.lower()


def test_compile_document_summary_block(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Doc\n\nParagraph.\n", encoding="utf-8")
    result = compile_document(str(f))
    assert "Tokens:" in result
    assert "Confidence:" in result
    assert "Time:" in result


def test_get_supported_formats():
    result = get_supported_formats()
    assert isinstance(result, str)
    assert "pdf" in result.lower()
    assert "docx" in result.lower()
    assert "html" in result.lower()
    assert "csv" in result.lower()
    assert "Total:" in result


def test_get_stats_returns_string():
    result = get_stats()
    assert isinstance(result, str)
    # Either "no compilations" (fresh install) or the stats table
    assert len(result) > 0


def test_compile_unknown_extension(tmp_path):
    f = tmp_path / "file.xyz123"
    f.write_text("content", encoding="utf-8")
    result = compile_document(str(f))
    # Should return an error or empty-content message, not crash
    assert isinstance(result, str)


# ── Path restriction (_check_allowed_path) ────────────────────────────────────

def test_check_allowed_path_no_restriction(tmp_path, monkeypatch):
    import aksharamd.mcp_server as mcp_mod
    monkeypatch.setattr(mcp_mod, "_ALLOWED_ROOT", None)
    assert _check_allowed_path(str(tmp_path / "any.pdf")) is None


def test_check_allowed_path_inside_root(tmp_path, monkeypatch):
    import aksharamd.mcp_server as mcp_mod
    monkeypatch.setattr(mcp_mod, "_ALLOWED_ROOT", tmp_path.resolve())
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    assert _check_allowed_path(str(f)) is None


def test_check_allowed_path_outside_root(tmp_path, monkeypatch):
    import aksharamd.mcp_server as mcp_mod
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(mcp_mod, "_ALLOWED_ROOT", allowed.resolve())
    outside = tmp_path / "secret.txt"
    outside.write_text("sensitive")
    result = _check_allowed_path(str(outside))
    assert result is not None
    assert "Access denied" in result


def test_check_allowed_path_dotdot_traversal(tmp_path, monkeypatch):
    import aksharamd.mcp_server as mcp_mod
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(mcp_mod, "_ALLOWED_ROOT", allowed.resolve())
    traversal = str(allowed / ".." / "secret.txt")
    result = _check_allowed_path(traversal)
    assert result is not None
    assert "Access denied" in result


def test_compile_document_blocked_by_allowed_root(tmp_path, monkeypatch):
    import aksharamd.mcp_server as mcp_mod
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(mcp_mod, "_ALLOWED_ROOT", allowed.resolve())
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret")
    result = compile_document(str(outside))
    assert "Access denied" in result


# ── compile_document_multimodal ───────────────────────────────────────────────

def test_compile_document_multimodal_not_found():
    result = compile_document_multimodal("/nonexistent/file.pdf")
    assert isinstance(result, list)
    assert len(result) > 0
    assert "not found" in result[0]


def test_compile_document_multimodal_markdown(tmp_path):
    f = tmp_path / "sample.md"
    f.write_text("# Hello\n\nBody text.\n", encoding="utf-8")
    result = compile_document_multimodal(str(f))
    assert isinstance(result, list)
    assert len(result) > 0
    combined = " ".join(str(r) for r in result if isinstance(r, str))
    assert "Hello" in combined


def test_compile_document_multimodal_blocked_by_allowed_root(tmp_path, monkeypatch):
    import aksharamd.mcp_server as mcp_mod
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(mcp_mod, "_ALLOWED_ROOT", allowed.resolve())
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret")
    result = compile_document_multimodal(str(outside))
    assert isinstance(result, list)
    assert "Access denied" in result[0]


# ── SSRF edge cases ───────────────────────────────────────────────────────────

def test_ssrf_localhost_by_hostname_rejected(monkeypatch, tmp_path):
    """'localhost' must be blocked even though it's passed as a hostname, not an IP."""
    import socket
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "127.0.0.1")
    from aksharamd.compiler import Compiler
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("https://localhost/secret.pdf")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)


def test_ssrf_non_http_scheme_rejected(tmp_path):
    """file:// and ftp:// schemes must be rejected."""
    from aksharamd.compiler import Compiler
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile("file:///etc/passwd")
    assert any(e.code == "URL_FETCH_ERROR" for e in ctx.validation.errors)
